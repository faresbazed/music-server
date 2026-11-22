#!/usr/bin/env python3
"""
Music Library Server - backend for the "Offline Music" iOS 9 iPad app.

Holds the owner's OWN uploaded music files, organises them by his Spotify
playlists (playlists_seed.json, a read-only export of playlist/track
metadata), and enriches tracks with album artwork and lyrics.

Boundary: this server NEVER downloads audio from Spotify / YouTube / any
third party. Audio files only ever arrive via the /upload page. Only
metadata, artwork images and lyrics text are fetched from public APIs
(iTunes Search, Deezer, LRCLIB).

Run locally:   flask --app app run   (or: python app.py)
On Railway:    gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4
"""

import base64
import csv
import hashlib
import hmac
import html as html_mod
import io
import itertools
import json
import mimetypes
import os
import re
import secrets
import string
import tempfile
import threading
import time
import unicodedata
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from urllib.parse import quote, urlencode

import mutagen
import requests
from flask import (Flask, abort, jsonify, make_response, redirect, request,
                   send_file, session)
from mutagen.aac import AAC
from mutagen.apev2 import APEv2File
from mutagen.id3 import ID3FileType
from werkzeug.utils import secure_filename

from spotify_client import SpotifyClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
SEED_PATH = BASE_DIR / "playlists_seed.json"


def _resolve_data_dir() -> Path:
    env = os.environ.get("DATA_DIR")
    if env:
        return Path(env)
    root_data = Path("/data")
    if root_data.is_dir() and os.access(str(root_data), os.W_OK):
        return root_data
    return Path.cwd() / "data"


DATA_DIR = _resolve_data_dir()
MUSIC_DIR = Path(os.environ["MUSIC_DIR"]) if os.environ.get("MUSIC_DIR") else (DATA_DIR / "music")
CACHE_DIR = DATA_DIR / "cache"
ART_DIR = CACHE_DIR / "art"
ARTIST_DIR = CACHE_DIR / "artist"
LYRICS_CACHE_PATH = CACHE_DIR / "lyrics.json"
ENRICH_CACHE_PATH = CACHE_DIR / "enrich.json"
IMPORTED_PATH = DATA_DIR / "imported_playlists.json"

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".flac", ".ogg"}
HTTP_TIMEOUT = 6
USER_AGENT = "MusicLibraryServer/1.0 (+personal library)"

for _d in (DATA_DIR, MUSIC_DIR, ART_DIR, ARTIST_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _resolve_token() -> str:
    tok = os.environ.get("LIBRARY_TOKEN", "").strip()
    if tok:
        return tok
    token_file = DATA_DIR / "token.txt"
    if token_file.is_file():
        saved = token_file.read_text(encoding="utf-8").strip()
        if saved:
            print("[music-server] LIBRARY_TOKEN not set; using saved token from "
                  f"{token_file}", flush=True)
            return saved
    tok = secrets.token_urlsafe(24)
    try:
        with os.fdopen(os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                               0o600), "w") as out:
            out.write(tok + "\n")
    except OSError as exc:
        print(f"[music-server] WARNING: could not save token: {exc}", flush=True)
    print("[music-server] Set LIBRARY_TOKEN on Railway to choose your own token.",
          flush=True)
    return tok


LIBRARY_TOKEN = _resolve_token()

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _json_load(path: Path, default):
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[music-server] cache read failed {path}: {exc}", flush=True)
    return default


def _json_save(path: Path, obj) -> None:
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, prefix=path.name + ".",
                                         suffix=".tmp", delete=False) as handle:
            tmp = Path(handle.name)
            handle.write(json.dumps(obj, ensure_ascii=False, indent=1))
        tmp.replace(path)
    except OSError as exc:
        print(f"[music-server] cache write failed {path}: {exc}", flush=True)
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _duration_ms(value, *, scale=1) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        duration = round(float(value) * scale)
    except (TypeError, ValueError, OverflowError):
        return None
    return duration if duration > 0 else None


def _norm(text) -> str:
    """Normalise a title/artist for matching: lowercase, strip diacritics
    and punctuation, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _collapse_repeats(norm_text: str) -> str:
    """Secondary key: collapse runs of the same letter (ellisa -> elisa,
    elissa -> elisa) so spelling variants still match."""
    return re.sub(r"(.)\1+", r"\1", norm_text or "")


_ARTIST_FILLER = frozenset({"the", "and", "feat", "ft", "featuring"})


def _artist_match_score(a: str, b: str) -> int:
    """0 = no match; higher = stronger (exact > spelling variant > substring > shared word)."""
    na, nb = _norm(a), _norm(b)
    if not (set(na.split()) - _ARTIST_FILLER) or not (set(nb.split()) - _ARTIST_FILLER):
        return 0
    if na == nb:
        return 4
    ca, cb = _collapse_repeats(na), _collapse_repeats(nb)
    if ca == cb:
        return 3
    if na in nb or nb in na or ca in cb or cb in ca:
        return 2
    return int(bool((set(na.split()) & set(nb.split())) - _ARTIST_FILLER))


def _artists_match(a: str, b: str) -> bool:
    return _artist_match_score(a, b) > 0


_PLAIN_FROM_LRC = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")


def _plain_from_synced(synced: str) -> str:
    lines = []
    for line in (synced or "").splitlines():
        txt = _PLAIN_FROM_LRC.sub("", line).strip()
        if txt:
            lines.append(txt)
    return "\n".join(lines)


def _image_mimetype(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


# ---------------------------------------------------------------------------
# Metadata extraction (mutagen)
# ---------------------------------------------------------------------------


def _first_text(value):
    if value is None:
        return None
    if hasattr(value, "text"):  # ID3 text frame
        try:
            return str(value.text[0]) if value.text else None
        except Exception:
            return None
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else None
    return str(value)


def _extract_artwork(audio, tags) -> bytes | None:
    if audio is None:
        return None
    # FLAC / OGG picture blocks
    pictures = getattr(audio, "pictures", None)
    if pictures:
        try:
            if pictures[0].data:
                return bytes(pictures[0].data)
        except Exception:
            pass
    if tags is None:
        return None
    # ID3 APIC frames
    try:
        if hasattr(tags, "getall"):
            for frame in tags.getall("APIC"):
                if getattr(frame, "data", None):
                    return bytes(frame.data)
    except Exception:
        pass
    if hasattr(tags, "get"):
        # MP4 covr
        covr = tags.get("covr")
        if covr:
            try:
                return bytes(covr[0])
            except Exception:
                pass
    if isinstance(tags, dict):
        # Vorbis embedded picture (base64 FLAC block) - best effort
        mbp = tags.get("metadata_block_picture")
        if mbp:
            try:
                import base64

                from mutagen.flac import Picture

                pic = Picture(base64.b64decode(_first_text(mbp)))
                if pic.data:
                    return bytes(pic.data)
            except Exception:
                pass
    return None


def _probe_audio(filename: str, stream: BinaryIO) -> tuple[bool, mutagen.FileType | None]:
    try:
        audio = mutagen.File(filename=filename, fileobj=stream)
    except Exception:
        audio = None
    if audio is not None and audio.info is not None and type(audio) not in (APEv2File, ID3FileType):
        return True, audio
    suffix = Path(filename).suffix.lower()
    if suffix == ".aac":
        # Mutagen's autodetection can mistake ID3-prefixed ADTS for MP3.
        try:
            stream.seek(0)
            return True, AAC(filename=filename, fileobj=stream)
        except Exception:
            pass
    elif suffix == ".wav":
        # Extended WAVE containers are valid audio but not supported by Mutagen.
        stream.seek(0)
        header = stream.read(40)
        rf64 = header[:4] in (b"RF64", b"BW64") and header[4:16] == b"\xff\xff\xff\xffWAVEds64"
        rifx = header[:4] == b"RIFX" and header[8:12] == b"WAVE"
        # Wave64 uses two 16-byte GUIDs separated by an 8-byte size.
        wave64 = (
            header[:16] == b"riff.\x91\xcf\x11\xa5\xd6(\xdb\x04\xc1\x00\x00"
            and header[24:40] == b"wave\xf3\xac\xd3\x11\x8c\xd1\x00\xc0O\x8e\xdb\x8a"
        )
        if rf64 or rifx or wave64:
            return True, None
    return False, None


def _extract_metadata(path: Path) -> dict | None:
    try:
        with path.open("rb") as stream:
            valid, audio = _probe_audio(str(path), stream)
    except OSError:
        return None
    if not valid:
        return None
    meta = {"title": None, "artist": None, "album": None,
            "duration_sec": None, "lyrics": None, "artwork": None}
    if audio is None:
        return meta
    try:
        if audio.info is not None and getattr(audio.info, "length", None):
            meta["duration_sec"] = round(float(audio.info.length), 3)
    except Exception:
        pass
    tags = getattr(audio, "tags", None)
    if tags is None:
        return meta

    def lookup(*keys):
        # exact keys first (ID3 frame ids, MP4 atoms), then case-insensitive
        for key in keys:
            try:
                if key in tags:
                    val = _first_text(tags[key])
                    if val:
                        return val
            except Exception:
                continue
        if isinstance(tags, dict):
            lower = {str(k).lower(): v for k, v in tags.items()}
            for key in keys:
                val = _first_text(lower.get(key.lower()))
                if val:
                    return val
        return None

    meta["title"] = lookup("TIT2", "\xa9nam", "title")
    meta["artist"] = lookup("TPE1", "\xa9ART", "artist")
    meta["album"] = lookup("TALB", "\xa9alb", "album")
    meta["lyrics"] = lookup("\xa9lyr", "lyrics", "unsyncedlyrics", "LYRICS")
    # ID3 USLT frames are not dict-addressable on all mutagen versions
    if not meta["lyrics"] and hasattr(tags, "getall"):
        try:
            for frame in tags.getall("USLT"):
                if getattr(frame, "text", None):
                    meta["lyrics"] = str(frame.text)
                    break
        except Exception:
            pass
    meta["artwork"] = _extract_artwork(audio, tags)
    return meta


# ---------------------------------------------------------------------------
# Index: scan MUSIC_DIR, match files to seed playlist tracks
# ---------------------------------------------------------------------------

SEED = _json_load(SEED_PATH, {"playlists": []})
SEED_PLAYLISTS = SEED.get("playlists", []) if isinstance(SEED, dict) else []

# Playlists imported at runtime (Spotify link / pasted track list / JSON-CSV
# file). Persisted on the data volume so they survive Railway redeploys.
_imported_doc = _json_load(IMPORTED_PATH, {"playlists": []})
IMPORTED_PLAYLISTS = (_imported_doc.get("playlists", [])
                      if isinstance(_imported_doc, dict) else [])
_imported_lock = threading.RLock()


def _save_imported() -> None:
    with _imported_lock:
        _json_save(IMPORTED_PATH, {"playlists": IMPORTED_PLAYLISTS})


def _all_playlists() -> list:
    with _imported_lock:
        return list(SEED_PLAYLISTS) + list(IMPORTED_PLAYLISTS)

_index_lock = threading.RLock()
_FILES: dict[str, dict] = {}   # id -> record (record includes private keys)
_FILES_ORDER: list[str] = []     # ids sorted by relpath


def track_id_for_rel(rel: str) -> str:
    return hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]


def scan_library() -> int:
    """(Re)build the in-memory index from MUSIC_DIR. Returns file count."""
    records: dict[str, dict] = {}
    order: list[str] = []
    if MUSIC_DIR.is_dir():
        paths = sorted(
            (p for p in MUSIC_DIR.rglob("*")
             if p.is_file() and p.suffix.lower() in AUDIO_EXTS),
            key=lambda p: str(p.relative_to(MUSIC_DIR)).lower(),
        )
    else:
        paths = []
    for path in paths:
        meta = _extract_metadata(path)
        if meta is None:
            continue
        rel = str(path.relative_to(MUSIC_DIR))
        tid = track_id_for_rel(rel)
        art_path = None
        if meta["artwork"]:
            art_path = ART_DIR / f"{tid}.jpg"
            try:
                if not art_path.is_file() or art_path.stat().st_size != len(meta["artwork"]):
                    art_path.write_bytes(meta["artwork"])
            except OSError:
                art_path = None
        enriched_art = ART_DIR / f"{tid}.jpg"
        has_artwork = bool(meta["artwork"]) or enriched_art.is_file()
        lrc = path.with_suffix(".lrc")
        txt = path.with_suffix(".txt")
        sidecar_lrc = str(lrc) if lrc.is_file() else None
        sidecar_txt = str(txt) if txt.is_file() else None
        records[tid] = {
            "id": tid,
            "rel": rel,
            "path": str(path),
            "ext": path.suffix.lower().lstrip("."),
            "title": meta["title"] or path.stem,
            "artist": meta["artist"],
            "album": meta["album"],
            "duration_sec": meta["duration_sec"],
            "has_artwork": has_artwork,
            "embedded_lyrics": meta["lyrics"],
            "sidecar_lrc": sidecar_lrc,
            "sidecar_txt": sidecar_txt,
            "has_lyrics_local": bool(meta["lyrics"] or sidecar_lrc or sidecar_txt),
        }
        order.append(tid)
    with _index_lock:
        _FILES.clear()
        _FILES.update(records)
        _FILES_ORDER.clear()
        _FILES_ORDER.extend(order)
    print(f"[music-server] scanned {len(order)} audio file(s) in {MUSIC_DIR}", flush=True)
    return len(order)


def _file_matches_seed(rec: dict, seed_track: dict) -> bool:
    ft = _norm(rec.get("title"))
    st = _norm(seed_track.get("title"))
    if not ft or not st:
        return False
    if ft != st and _collapse_repeats(ft) != _collapse_repeats(st):
        return False
    if _artists_match(rec.get("artist") or "", seed_track.get("artist") or ""):
        return True
    # fall back to duration agreement (within 3 seconds)
    dur = rec.get("duration_sec")
    seed_ms = _duration_ms(seed_track.get("duration_ms"))
    return dur is not None and seed_ms is not None and abs(dur - seed_ms / 1000) <= 3.0


def _match_file_for_seed(seed_track: dict, records: list[dict]):
    candidates = (rec for rec in records if _file_matches_seed(rec, seed_track))
    return max(candidates, key=lambda rec: _artist_match_score(
        rec.get("artist") or "", seed_track.get("artist") or ""
    ), default=None)


# ---------------------------------------------------------------------------
# Enrichment: iTunes (album + artwork), Deezer (artist image), LRCLIB (lyrics)
# ---------------------------------------------------------------------------

_enrich_cache = _json_load(ENRICH_CACHE_PATH, {})
_lyrics_cache = _json_load(LYRICS_CACHE_PATH, {})
_cache_lock = threading.Lock()


def _http_get(url, **kwargs):
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    kwargs.setdefault("headers", {"User-Agent": USER_AGENT})
    return requests.get(url, **kwargs)


def itunes_lookup(artist: str, title: str):
    """Cached iTunes Search lookup -> {"album":..., "artwork_url":...} or None."""
    if not artist or not title:
        return None
    key = f"{_norm(artist)}|{_norm(title)}"
    with _cache_lock:
        if key in _enrich_cache:
            return _enrich_cache[key]
    result = None
    try:
        resp = _http_get("https://itunes.apple.com/search",
                         params={"term": f"{artist} {title}", "entity": "song",
                                 "limit": 1})
        if resp.status_code != 200:
            return None
        items = resp.json().get("results") or []
        if items:
            item = items[0]
            art = item.get("artworkUrl100")
            if art:
                art = re.sub(r"/\d+x\d+bb\.(jpg|png)", "/600x600bb.jpg", art)
            result = {"album": item.get("collectionName"),
                      "artwork_url": art}
    except Exception as exc:
        print(f"[music-server] iTunes lookup failed ({artist} - {title}): {exc}",
              flush=True)
        return None
    with _cache_lock:
        _enrich_cache[key] = result  # negative results cached too
        _json_save(ENRICH_CACHE_PATH, _enrich_cache)
    return result


def lrclib_lookup(rec: dict):
    """Cached LRCLIB lookup -> {"plain","synced","source"} (source may be 'none')."""
    tid = rec["id"]
    with _cache_lock:
        if tid in _lyrics_cache:
            return _lyrics_cache[tid]
    found = {"plain": None, "synced": None, "source": "none"}
    artist = rec.get("artist") or ""
    title = rec.get("title") or ""
    try:
        params = {"artist_name": artist, "track_name": title,
                  "album_name": rec.get("album") or ""}
        if rec.get("duration_sec"):
            params["duration"] = int(round(rec["duration_sec"]))
        resp = _http_get("https://lrclib.net/api/get", params=params)
        if resp.status_code not in (200, 404):
            return found
        data = None
        if resp.status_code == 200:
            data = resp.json()
        if not data or not (data.get("plainLyrics") or data.get("syncedLyrics")):
            resp2 = _http_get("https://lrclib.net/api/search",
                              params={"artist_name": artist, "track_name": title})
            if resp2.status_code != 200:
                return found
            for item in resp2.json() or []:
                if item.get("plainLyrics") or item.get("syncedLyrics"):
                    data = item
                    break
        if data and (data.get("plainLyrics") or data.get("syncedLyrics")):
            found = {"plain": data.get("plainLyrics"),
                     "synced": data.get("syncedLyrics"),
                     "source": "lrclib"}
            if not found["plain"] and found["synced"]:
                found["plain"] = _plain_from_synced(found["synced"]) or None
    except Exception as exc:
        print(f"[music-server] LRCLIB lookup failed ({artist} - {title}): {exc}",
              flush=True)
        return found
    with _cache_lock:
        _lyrics_cache[tid] = found  # negative results cached too
        _json_save(LYRICS_CACHE_PATH, _lyrics_cache)
    return found


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)
spotify = SpotifyClient(DATA_DIR)
app.secret_key = hmac.digest(LIBRARY_TOKEN.encode(), b"spotify-connect", "sha256")
app.config.update(SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=not os.environ.get("SPOTIFY_REDIRECT_URI", "").startswith("http://"))
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GB uploads
try:
    app.json.ensure_ascii = False  # Flask >= 2.2: keep Arabic titles readable
except Exception:
    pass


def _token_matches(candidate: str | None) -> bool:
    # compare bytes: compare_digest raises TypeError on non-ASCII str
    return bool(candidate) and hmac.compare_digest(
        candidate.encode("utf-8"), LIBRARY_TOKEN.encode("utf-8"))


def _token_ok() -> bool:
    candidates = [
        request.headers.get("X-Library-Token"),
        request.args.get("token"),
        request.cookies.get("library_token"),
    ]
    if request.method == "POST":
        try:
            candidates.append(request.form.get("token"))
        except Exception:
            pass
    return any(_token_matches(cand) for cand in candidates)


@app.before_request
def _auth_gate():
    path = request.path or ""
    if path.startswith("/api/"):
        if not _token_ok():
            return jsonify({"error": "unauthorized",
                            "detail": "missing or wrong library token"}), 401
    elif path in ("/spotify/connect", "/spotify/callback") or (
            path in ("/upload", "/import") and request.method != "GET"):
        if not _token_ok():
            return jsonify({"error": "unauthorized",
                            "detail": "missing or wrong library token"}), 401
    return None


def _snapshot():
    with _index_lock:
        return list(_FILES.values()), [ _FILES[i] for i in _FILES_ORDER ]


def _public_track(rec: dict, spotify_url=None, album_override=None, *, enrich=True) -> dict:
    album = rec.get("album") or album_override
    if not album and enrich:
        found = itunes_lookup(rec.get("artist") or "", rec.get("title") or "")
        album = found.get("album") if found else None
    with _cache_lock:
        if not album:
            key = f"{_norm(rec.get('artist'))}|{_norm(rec.get('title'))}"
            album = (_enrich_cache.get(key) or {}).get("album")
        lyr_cached = _lyrics_cache.get(rec["id"])
    has_lyrics = bool(rec.get("has_lyrics_local")
                      or (lyr_cached and lyr_cached.get("source") not in (None, "none")))
    return {
        "id": rec["id"],
        "title": rec.get("title"),
        "artist": rec.get("artist"),
        "album": album,
        "duration_sec": rec.get("duration_sec"),
        "ext": rec.get("ext"),
        "has_file": True,
        "has_artwork": bool(rec.get("has_artwork")),
        "has_lyrics": has_lyrics,
        "spotify_url": spotify_url,
    }


@app.route("/")
def status():
    with _index_lock:
        count = len(_FILES)
    payload = {"ok": True, "service": "music-library-server",
               "tracks": count, "playlists": len(_all_playlists())}
    if request.accept_mimetypes.best == "text/html":
        return make_response(
            "<!doctype html><title>Music Library Server</title>"
            f"<h1>Music Library Server</h1><p>OK &middot; {count} track(s) &middot; "
            f"{len(_all_playlists())} playlist(s)</p>"
            '<p><a href="/upload">Upload music</a> &middot; '
            '<a href="/import">Import a playlist</a></p>')
    return jsonify(payload)


@app.route("/api/catalog")
def catalog():
    _all, ordered = _snapshot()
    enrich = request.args.get("enrich") != "0"
    library = [_public_track(rec, enrich=enrich) for rec in ordered]
    playlists = []
    for pl in _all_playlists():
        tracks = []
        for st in pl.get("tracks", []):
            rec = _match_file_for_seed(st, ordered)
            if rec is not None:
                tracks.append(_public_track(rec, spotify_url=st.get("spotify_url"),
                                            enrich=enrich))
            else:
                dur_ms = _duration_ms(st.get("duration_ms"))
                tracks.append({
                    "id": None,
                    "title": st.get("title"),
                    "artist": st.get("artist"),
                    "album": None,
                    "duration_sec": dur_ms / 1000 if dur_ms is not None else None,
                    "ext": None,
                    "has_file": False,
                    "has_artwork": False,
                    "has_lyrics": False,
                    "spotify_url": st.get("spotify_url"),
                })
        playlists.append({"name": pl.get("name"),
                          "spotify_url": pl.get("spotify_url"),
                          "tracks": tracks})
    return jsonify({"playlists": playlists, "library": library})


def _get_record_or_404(tid: str) -> dict:
    with _index_lock:
        rec = _FILES.get(tid)
    if rec is None:
        abort(404)
    return rec


@app.route("/api/artwork/<tid>")
def artwork(tid):
    rec = _get_record_or_404(tid)
    art_path = ART_DIR / f"{tid}.jpg"
    if art_path.is_file() and art_path.stat().st_size > 0:
        data = art_path.read_bytes()
        return make_response(data, 200, {"Content-Type": _image_mimetype(data),
                                         "Cache-Control": "public, max-age=86400"})
    enriched = itunes_lookup(rec.get("artist") or "", rec.get("title") or "")
    if enriched and enriched.get("artwork_url"):
        try:
            resp = _http_get(enriched["artwork_url"])
            if resp.status_code == 200 and resp.content:
                try:
                    art_path.write_bytes(resp.content)
                except OSError:
                    pass
                with _index_lock:
                    if tid in _FILES:
                        _FILES[tid]["has_artwork"] = True
                return make_response(
                    resp.content, 200,
                    {"Content-Type": _image_mimetype(resp.content),
                     "Cache-Control": "public, max-age=86400"})
        except Exception as exc:
            print(f"[music-server] artwork download failed for {tid}: {exc}",
                  flush=True)
    return jsonify({"error": "no_artwork", "id": tid}), 404


@app.route("/api/artist-image")
def artist_image():
    name = (request.args.get("artist") or "").strip()
    if not name:
        return jsonify({"error": "missing_artist"}), 404
    cache_key = hashlib.sha1(_norm(name).encode("utf-8")).hexdigest()[:16]
    img_path = ARTIST_DIR / f"{cache_key}.jpg"
    neg_path = ARTIST_DIR / f"{cache_key}.none"
    if img_path.is_file() and img_path.stat().st_size > 0:
        data = img_path.read_bytes()
        return make_response(data, 200, {"Content-Type": _image_mimetype(data),
                                         "Cache-Control": "public, max-age=604800"})
    if neg_path.is_file():
        return jsonify({"error": "no_artist_image", "artist": name}), 404
    pic_url = None
    cache_negative = False
    try:
        resp = _http_get("https://api.deezer.com/search/artist", params={"q": name})
        if resp.status_code == 200:
            items = (resp.json().get("data") or [])
            if items:
                pic_url = items[0].get("picture_medium") or items[0].get("picture")
            cache_negative = not pic_url
    except Exception as exc:
        print(f"[music-server] Deezer lookup failed ({name}): {exc}", flush=True)
    if pic_url:
        try:
            img = _http_get(pic_url)
            if img.status_code == 200 and img.content:
                try:
                    img_path.write_bytes(img.content)
                except OSError:
                    pass
                return make_response(
                    img.content, 200,
                    {"Content-Type": _image_mimetype(img.content),
                     "Cache-Control": "public, max-age=604800"})
        except Exception as exc:
            print(f"[music-server] artist image download failed ({name}): {exc}",
                  flush=True)
    if cache_negative:
        try:
            neg_path.write_text("none", encoding="utf-8")
        except OSError:
            pass
    return jsonify({"error": "no_artist_image", "artist": name}), 404


@app.route("/api/lyrics/<tid>")
def lyrics(tid):
    rec = _get_record_or_404(tid)
    if rec.get("embedded_lyrics"):
        text = rec["embedded_lyrics"]
        return jsonify({"plain": text, "synced": None, "source": "embedded"})
    for key, is_lrc in (("sidecar_lrc", True), ("sidecar_txt", False)):
        spath = rec.get(key)
        if spath and Path(spath).is_file():
            try:
                content = Path(spath).read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            if content.strip():
                if is_lrc:
                    return jsonify({"plain": _plain_from_synced(content) or None,
                                    "synced": content, "source": "sidecar"})
                return jsonify({"plain": content, "synced": None,
                                "source": "sidecar"})
    found = lrclib_lookup(rec)
    return jsonify({"plain": found.get("plain"), "synced": found.get("synced"),
                    "source": found.get("source", "none")})


@app.route("/api/stream/<tid>")
def stream(tid):
    rec = _get_record_or_404(tid)
    path = Path(rec["path"])
    if not path.is_file():
        abort(404)
    mime = mimetypes.guess_type(str(path))[0] or "audio/mpeg"
    return send_file(str(path), mimetype=mime, conditional=True)


@app.route("/api/download/<tid>")
def download(tid):
    rec = _get_record_or_404(tid)
    path = Path(rec["path"])
    if not path.is_file():
        abort(404)
    mime = mimetypes.guess_type(str(path))[0] or "audio/mpeg"
    return send_file(str(path), mimetype=mime, conditional=True,
                     as_attachment=True, download_name=path.name)


@app.route("/api/rescan", methods=["POST"])
def rescan():
    count = scan_library()
    return jsonify({"ok": True, "tracks": count})


_UPLOAD_TOKEN_PAGE = """<!doctype html>
<title>Music Library - token</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font-family:-apple-system,system-ui,sans-serif;margin:2em auto;max-width:32em;padding:0 1em}
input,button{font-size:1em;padding:.5em;width:100%;box-sizing:border-box;margin:.4em 0}</style>
<h1>Music Library</h1>
<p>Enter the library token once - it is kept in a cookie on this device.</p>
<form method="POST" action="/login">
  <input type="hidden" name="page" value="/upload">
  <input type="password" name="token" placeholder="Library token" required>
  <button type="submit">Continue</button>
</form>
"""


def _remember_library_token(response):
    response.set_cookie("library_token", LIBRARY_TOKEN, max_age=30 * 24 * 3600,
                        httponly=True, samesite="Lax",
                        secure=request.is_secure or bool(os.environ.get("RAILWAY_ENVIRONMENT_ID")))
    return response


@app.post("/login")
def library_login():
    if not _token_matches(request.form.get("token")):
        return "Wrong library token. Go back and try again.", 401
    target = "/import" if request.form.get("page") == "/import" else "/upload"
    return _remember_library_token(redirect(target, code=303))


_UPLOAD_PAGE = """<!doctype html>
<title>Music Library - upload</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font-family:-apple-system,system-ui,sans-serif;margin:2em auto;max-width:32em;padding:0 1em}
input,button{font-size:1em;padding:.5em;width:100%;box-sizing:border-box;margin:.4em 0}</style>
<h1>Upload music</h1>
<p>Two bulk ways - never one song at a time:</p>
<p><b>Select many files at once</b> in the picker (Ctrl/Cmd+A inside a
folder), or <b>upload one ZIP</b> of your whole music folder - audio files
inside it are extracted automatically, .lrc lyrics files included.</p>
<p>Up to 1 GB per upload, so split a big library into a few ZIPs.
Tags decide artist/album and playlist matching.</p>
<form method="POST" action="/upload" enctype="multipart/form-data">
  <input type="file" name="files" multiple accept="audio/*,.mp3,.m4a,.aac,.wav,.aif,.aiff,.flac,.ogg,.zip">
  <button type="submit">Upload</button>
</form>
"""


@app.route("/upload", methods=["GET"])
def upload_page():
    if not _token_ok():
        return make_response(_UPLOAD_TOKEN_PAGE)
    resp = make_response(_UPLOAD_PAGE)
    # remember the token on this device so it is only typed once
    supplied = request.args.get("token") or request.headers.get("X-Library-Token")
    if _token_matches(supplied):
        _remember_library_token(resp)
    return resp


def _upload_filename(name: str) -> str:
    name = re.split(r"[/\\]", name)[-1]
    name = "".join(char for char in name if not unicodedata.category(char).startswith("C"))
    return re.sub(r"^[.\s]+", "", unicodedata.normalize("NFC", name))


def _unique_dest(name: str) -> Path:
    dest = MUSIC_DIR / name
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        n = 2
        while dest.exists():
            dest = MUSIC_DIR / f"{stem}-{n}{suffix}"
            n += 1
    return dest


def _zip_dest(name: str, taken_stems: set[str]) -> Path:
    dest = MUSIC_DIR / name
    if dest.stem not in taken_stems and not dest.exists():
        return dest
    # Keep the filename intact: its stem is also the fallback track title.
    for n in itertools.count(2):
        folder = MUSIC_DIR / f"{dest.stem}-{n}"
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        return folder / name


_MAX_ZIP_TOTAL = 5 * 1024 * 1024 * 1024   # 5 GB extracted per ZIP
_MAX_ZIP_FILE = 300 * 1024 * 1024         # 300 MB per file inside a ZIP


def _save_zip(fh) -> int:
    """Save audio and its same-folder lyrics together. Return the audio count."""
    saved = 0
    extracted = 0
    try:
        archive = zipfile.ZipFile(fh)
    except zipfile.BadZipFile:
        return 0

    def read_member(info: zipfile.ZipInfo, copies: int = 1) -> bytes | None:
        nonlocal extracted
        size = info.file_size
        if not copies or size > _MAX_ZIP_FILE or extracted + size * copies > _MAX_ZIP_TOTAL:
            return None
        try:
            data = archive.read(info)
        except Exception:
            return None
        extracted += len(data) * copies
        return data

    def normalized_stem(key: PurePosixPath) -> tuple[PurePosixPath, str]:
        return key.parent, unicodedata.normalize("NFC", key.name)

    def unique_aliases(
        keys: Iterable[PurePosixPath],
    ) -> dict[tuple[PurePosixPath, str], PurePosixPath | None]:
        aliases: dict[tuple[PurePosixPath, str], PurePosixPath | None] = {}
        for key in keys:
            alias = normalized_stem(key)
            aliases[alias] = None if alias in aliases else key
        return aliases

    sidecar_exts = {".lrc", ".txt"}
    supported_exts = AUDIO_EXTS | sidecar_exts
    with archive:
        # The scanner matches by stem regardless of the audio extension.
        taken_stems = {
            path.stem for path in MUSIC_DIR.iterdir()
            if path.suffix.lower() in supported_exts
        }
        audio_dests: dict[PurePosixPath, list[Path]] = {}
        sidecars: dict[PurePosixPath, list[tuple[zipfile.ZipInfo, str]]] = {}
        for info in archive.infolist():
            path = PurePosixPath(info.filename.replace("\\", "/"))
            if info.is_dir() or "__MACOSX" in path.parts[:-1]:
                continue
            base = _upload_filename(info.filename)
            suffix = Path(base).suffix.lower()
            if suffix not in supported_exts:
                continue
            key = path.with_suffix("")
            if suffix in sidecar_exts:
                sidecars.setdefault(key, []).append((info, suffix))
                continue
            # Even an audio entry we skip can make a fallback ambiguous.
            dests = audio_dests.setdefault(key, [])
            data = read_member(info)
            if data is None:
                continue
            valid, _ = _probe_audio(base, io.BytesIO(data))
            if not valid:
                del data
                continue
            dest = _zip_dest(base, taken_stems)
            dest.write_bytes(data)
            del data
            dests.append(dest)
            taken_stems.add(dest.stem)
            saved += 1

        audio_aliases = unique_aliases(audio_dests)
        sidecar_aliases = unique_aliases(sidecars)
        for key, dests in audio_dests.items():
            if not dests:
                continue
            matches = sidecars.get(key)
            if matches is None:
                alias = normalized_stem(key)
                sidecar_key = sidecar_aliases.get(alias)
                if audio_aliases[alias] != key or sidecar_key is None:
                    continue
                matches = sidecars[sidecar_key]
            for info, suffix in matches:
                lyrics_dests = [audio.with_suffix(suffix) for audio in dests]
                unwritten = [dest for dest in lyrics_dests if not dest.exists()]
                data = read_member(info, len(unwritten))
                if data is not None:
                    for dest in unwritten:
                        dest.write_bytes(data)
                    del data
    return saved


@app.route("/upload", methods=["POST"])
def upload_files():
    files = request.files.getlist("files")
    saved = 0
    for fh in files:
        raw_name = (fh.filename or "").strip()
        if not raw_name:
            continue
        if Path(raw_name).suffix.lower() == ".zip":
            saved += _save_zip(fh)
            continue
        name = _upload_filename(raw_name)
        if not name or Path(name).suffix.lower() not in AUDIO_EXTS:
            continue
        valid, _ = _probe_audio(name, fh.stream)
        if not valid:
            continue
        fh.stream.seek(0)
        fh.save(str(_unique_dest(name)))
        saved += 1
    total = scan_library()
    wants_json = "application/json" in (request.headers.get("Accept") or "") \
        or request.args.get("format") == "json"
    if wants_json:
        return jsonify({"ok": True, "saved": saved, "tracks": total})
    return make_response(
        "<!doctype html><title>Uploaded</title>"
        f"<p>Saved {saved} file(s). Library now has {total} track(s).</p>"
        '<p><a href="/upload">Upload more</a></p>')


# ---------------------------------------------------------------------------
# Playlist importer (metadata only)
#
# Imports a playlist's TRACK LIST into the catalogue, then matches it
# against the owner's OWN uploaded files using the same matcher as the
# seed playlists. It never fetches audio: matched tracks are the files
# already on this server, missing tracks are only reported by name.
# Three ways in:
#   1. Spotify playlist link - connects to the owner's Spotify account.
#   2. Paste a track list - one per line: "Title | Artist" (or Title - Artist).
#   3. Upload a .json (seed/Spotify shape) or .csv (title,artist) file.
# ---------------------------------------------------------------------------


def _parse_spotify_playlist_id(value: str):
    if not value:
        return None
    value = str(value).strip()
    m = re.search(r"spotify:playlist:([A-Za-z0-9]+)", value)
    if m:
        return m.group(1)
    m = re.search(r"open\.spotify\.com/(?:intl-[a-z]{2}/)?playlist/([A-Za-z0-9]+)",
                  value)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9]{22}", value):
        return value
    return None


def _spotify_fetch_playlist(playlist_id: str) -> dict:
    data, items = spotify.playlist(playlist_id)
    tracks = []
    for item in items:
        tr = item.get("item", item.get("track")) if isinstance(item, dict) else None
        if (not isinstance(tr, dict) or not tr.get("name") or item.get("is_local")
                or tr.get("is_local") or tr.get("type", "track") != "track"):
            continue
        artists = ", ".join(a.get("name", "") for a in (tr.get("artists") or [])
                            if a.get("name"))
        tracks.append({
            "title": tr.get("name"),
            "artist": artists or None,
            "duration_ms": _duration_ms(tr.get("duration_ms")),
            "spotify_url": (tr.get("external_urls") or {}).get("spotify"),
            "spotify_uri": tr.get("uri"),
        })
    return {"name": data.get("name") or f"Spotify playlist {playlist_id}",
            "spotify_url": (data.get("external_urls") or {}).get("spotify"),
            "tracks": tracks}


def _norm_track(raw: dict):
    if not isinstance(raw, dict):
        return None
    title = raw.get("title") or raw.get("name")
    if not title:
        return None
    artist = raw.get("artist")
    if not artist and isinstance(raw.get("artists"), list):
        parts = []
        for a in raw["artists"]:
            parts.append(a.get("name") if isinstance(a, dict) else str(a))
        artist = ", ".join(p for p in parts if p)
    if raw.get("duration_ms") is not None:
        dur_ms = _duration_ms(raw["duration_ms"])
    else:
        dur_ms = _duration_ms(raw.get("duration_sec"), scale=1000)
    return {"title": str(title), "artist": str(artist) if artist else None,
            "duration_ms": dur_ms,
            "spotify_url": raw.get("spotify_url"),
            "spotify_uri": raw.get("spotify_uri")}


def _parse_track_text(text: str) -> list:
    tracks = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        title, artist = line, None
        if "|" in line:
            title, artist = (p.strip() for p in line.split("|", 1))
        elif "\t" in line:
            title, artist = (p.strip() for p in line.split("\t", 1))
        elif " - " in line:
            title, artist = (p.strip() for p in line.split(" - ", 1))
        elif "," in line:
            title, artist = (p.strip() for p in line.split(",", 1))
        t = _norm_track({"title": title, "artist": artist})
        if t:
            tracks.append(t)
    return tracks


def _parse_import_json(body, default_name="Imported playlist") -> list:
    docs = body.get("playlists") or [body]
    playlists = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        tracks = [t for t in (_norm_track(r) for r in doc.get("tracks", []))
                  if t]
        if tracks:
            playlists.append({"name": doc.get("name") or default_name,
                              "spotify_url": doc.get("spotify_url"),
                              "tracks": tracks})
    return playlists


def _parse_import_file(data: bytes, filename: str) -> list:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".csv" or (suffix != ".json" and b"," in data[:512]
                            and not data.lstrip().startswith(b"{")):
        rows = csv.DictReader(io.StringIO(data.decode("utf-8-sig",
                                                     errors="replace")))
        aliases = {"track name": "title", "artist name(s)": "artist",
                   "duration (ms)": "duration_ms"}
        rows.fieldnames = [aliases.get(key.strip().lower(), key.strip().lower())
                           for key in (rows.fieldnames or [])]
        tracks = [t for t in (_norm_track(r) for r in rows) if t]
        name = Path(filename or "Imported playlist").stem
        return ([{"name": name, "spotify_url": None, "tracks": tracks}]
                if tracks else [])
    doc = json.loads(data.decode("utf-8-sig", errors="replace"))
    if isinstance(doc, list):
        doc = {"name": Path(filename or "Imported playlist").stem,
               "tracks": doc}
    return _parse_import_json(doc, Path(filename or "Imported").stem)


def _add_imported_playlist(name: str, tracks: list, spotify_url=None,
                           source: str = "import") -> dict:
    pl = {"name": (name or "Imported playlist").strip() or "Imported playlist",
          "spotify_url": spotify_url, "source": source,
          "tracks": [t for t in tracks if t]}
    key_url = spotify_url or ""
    key_name = _norm(pl["name"])
    with _imported_lock:
        kept = [p for p in IMPORTED_PLAYLISTS
                if not ((key_url and p.get("spotify_url") == key_url)
                        or _norm(p.get("name")) == key_name)]
        kept.append(pl)
        IMPORTED_PLAYLISTS.clear()
        IMPORTED_PLAYLISTS.extend(kept)
        _save_imported()
    return pl


def _find_playlist(name: str):
    if not name:
        return None
    for pl in _all_playlists():
        if pl.get("name") == name or _norm(pl.get("name")) == _norm(name):
            return pl
    return None


def _playlist_report(pl: dict) -> dict:
    _all, ordered = _snapshot()
    matched, missing = [], []
    for st in pl.get("tracks", []):
        rec = _match_file_for_seed(st, ordered)
        if rec is not None:
            matched.append({"title": st.get("title"),
                            "artist": st.get("artist"), "id": rec["id"]})
        else:
            missing.append({"title": st.get("title"),
                            "artist": st.get("artist"),
                            "duration_ms": _duration_ms(st.get("duration_ms")),
                            "spotify_url": st.get("spotify_url")})
    return {"name": pl.get("name"), "spotify_url": pl.get("spotify_url"),
            "total": len(pl.get("tracks", [])), "matched": len(matched),
            "missing_count": len(missing), "matched_tracks": matched,
            "missing_tracks": missing}


@app.route("/api/import/spotify", methods=["POST"])
def import_spotify():
    body = request.get_json(silent=True) or {}
    pid = _parse_spotify_playlist_id(body.get("url") or body.get("id") or "")
    if not pid:
        return jsonify({"error": "bad_spotify_url",
                        "detail": "paste a Spotify playlist link"}), 400
    try:
        fetched = _spotify_fetch_playlist(pid)
    except ValueError as exc:
        return jsonify({"error": "import_failed", "detail": str(exc)}), 400
    pl = _add_imported_playlist(body.get("name") or fetched["name"],
                                fetched["tracks"],
                                spotify_url=fetched.get("spotify_url"),
                                source="spotify")
    return jsonify({"ok": True, "report": _playlist_report(pl)})


@app.route("/api/import/text", methods=["POST"])
def import_text():
    body = request.get_json(silent=True) or {}
    tracks = _parse_track_text(body.get("text") or "")
    if not tracks:
        return jsonify({"error": "no_tracks",
                        "detail": "one track per line: Title | Artist"}), 400
    pl = _add_imported_playlist(body.get("name") or "Imported playlist",
                                tracks,
                                spotify_url=body.get("spotify_url"),
                                source="text")
    return jsonify({"ok": True, "report": _playlist_report(pl)})


@app.route("/api/import/json", methods=["POST"])
def import_json():
    body = request.get_json(silent=True) or {}
    reports = []
    for doc in _parse_import_json(body):
        pl = _add_imported_playlist(doc["name"], doc["tracks"],
                                    spotify_url=doc.get("spotify_url"),
                                    source="json")
        reports.append(_playlist_report(pl))
    if not reports:
        return jsonify({"error": "no_tracks"}), 400
    return jsonify({"ok": True, "report": reports[0], "reports": reports})


@app.route("/api/missing")
def missing():
    pl = _find_playlist(request.args.get("playlist") or "")
    if pl is None:
        return jsonify({"error": "playlist_not_found"}), 404
    return jsonify(_playlist_report(pl))


def _download_filename(name: str, fallback: str) -> str:
    name = unicodedata.normalize("NFC", name)
    name = "".join(char for char in name
                   if not unicodedata.category(char).startswith("C")
                   and char not in '<>:"/\\|?*')
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name.encode("utf-8")[:180].decode("utf-8", errors="ignore").rstrip(" .")
    return name or fallback


@app.route("/api/download-playlist")
def download_playlist():
    pl = _find_playlist(request.args.get("playlist") or "")
    if pl is None:
        return jsonify({"error": "playlist_not_found"}), 404
    _all, ordered = _snapshot()
    buf = io.BytesIO()
    used, m3u, missing_lines, count = set(), ["#EXTM3U"], [], 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, st in enumerate(pl.get("tracks", []), 1):
            rec = _match_file_for_seed(st, ordered)
            if rec is None:
                missing_lines.append(
                    f"{st.get('title') or ''} - {st.get('artist') or ''}")
                continue
            path = Path(rec["path"])
            if not path.is_file():
                continue
            ext = "." + _download_filename(path.suffix.lower().lstrip("."), "mp3")
            base = _download_filename(
                f"{idx:03d} - {st.get('title') or path.stem}", f"{idx:03d}")
            arc = base + ext
            n = 2
            while arc in used:
                arc = f"{base}-{n}{ext}"
                n += 1
            used.add(arc)
            zf.write(str(path), arc)
            m3u.append(arc)
            count += 1
        zf.writestr("playlist.m3u", "\n".join(m3u) + "\n")
        if missing_lines:
            zf.writestr("missing.txt",
                        "Not on this server yet (upload your own files "
                        "for these):\n" + "\n".join(missing_lines) + "\n")
    if count == 0:
        return jsonify({"error": "no_matched_files",
                        "detail": "none of this playlist's tracks are "
                                  "uploaded to this server yet"}), 404
    buf.seek(0)
    safe = _download_filename(pl.get("name") or "", "playlist")
    ascii_name = secure_filename(safe)
    if not re.search(r"[A-Za-z0-9]", ascii_name):
        ascii_name = "playlist"
    response = send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"{ascii_name}.zip")
    if not safe.isascii():
        response.headers.set(
            "Content-Disposition", "attachment", filename=f"{ascii_name}.zip",
            **{"filename*": "UTF-8''" + quote(f"{safe}.zip", safe="")})
    return response


_IMPORT_TOKEN_PAGE = _UPLOAD_TOKEN_PAGE.replace('value="/upload"', 'value="/import"')

_IMPORT_PAGE = """<!doctype html>
<title>Music Library - import playlist</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font-family:-apple-system,system-ui,sans-serif;margin:2em auto;max-width:34em;padding:0 1em}
input,button,textarea{font-size:1em;padding:.5em;width:100%;box-sizing:border-box;margin:.4em 0}
textarea{height:9em}fieldset{margin:1em 0}</style>
<h1>Import a playlist</h1>
<p>This imports the <b>track list only</b>. Tracks are matched against
<b>your own MP3s already uploaded</b> to this server - nothing is
downloaded from Spotify. You get a matched / missing report, and matched
playlists appear in the app catalogue ready for Download All.</p>
<fieldset><legend>1. Spotify playlist link (needs server Spotify keys)</legend>
<p>For a personal Spotify developer app, connect the owner account first.
Use a playlist you own or collaborate on.</p>
<form method="POST" action="/spotify/connect">
  <button type="submit">Connect Spotify account</button>
</form>
<form method="POST" action="/import">
  <input type="hidden" name="mode" value="spotify">
  <input type="text" name="url" placeholder="https://open.spotify.com/playlist/...">
  <button type="submit">Import from Spotify</button>
</form></fieldset>
<fieldset><legend>2. Paste a track list</legend>
<form method="POST" action="/import">
  <input type="hidden" name="mode" value="text">
  <input type="text" name="name" placeholder="Playlist name" required>
  <textarea name="text" placeholder="One per line: Title | Artist"></textarea>
  <button type="submit">Import track list</button>
</form></fieldset>
<fieldset><legend>3. Upload a .json / .csv track list</legend>
<form method="POST" action="/import" enctype="multipart/form-data">
  <input type="hidden" name="mode" value="file">
  <input type="file" name="file" accept=".json,.csv">
  <button type="submit">Import file</button>
</form></fieldset>
<p><a href="/upload">Upload music</a></p>
"""


@app.route("/import", methods=["GET"])
def import_page():
    if not _token_ok():
        return make_response(_IMPORT_TOKEN_PAGE)
    resp = make_response(_IMPORT_PAGE)
    supplied = request.args.get("token") or request.headers.get("X-Library-Token")
    if _token_matches(supplied):
        _remember_library_token(resp)
    return resp


@app.post("/spotify/connect")
def spotify_connect():
    try:
        state, url = spotify.authorization()
    except ValueError as exc:
        return jsonify(error="spotify_config", detail=str(exc)), 400
    session["spotify_login"] = [state, int(time.time())]
    return redirect(url, code=303)


@app.get("/spotify/callback")
def spotify_callback():
    pending = session.pop("spotify_login", None)
    state = request.args.get("state", "")
    if (not pending or not secrets.compare_digest(pending[0].encode(), state.encode())
            or time.time() - pending[1] > 600):
        return "Spotify connection expired or invalid. Start again on /import.", 400
    code = request.args.get("code")
    if not code:
        return "Spotify connection canceled. Start again on /import.", 400
    try:
        spotify.connect(code)
    except ValueError as exc:
        return jsonify(error="spotify_connect_failed", detail=str(exc)), 400
    response = redirect("/import", code=303)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/import", methods=["POST"])
def import_submit():
    mode = (request.form.get("mode") or "").strip()
    playlists = []
    try:
        if mode == "spotify":
            pid = _parse_spotify_playlist_id(request.form.get("url") or "")
            if not pid:
                raise ValueError("bad_spotify_url: paste a playlist link")
            fetched = _spotify_fetch_playlist(pid)
            playlists.append(_add_imported_playlist(
                fetched["name"], fetched["tracks"],
                spotify_url=fetched.get("spotify_url"), source="spotify"))
        elif mode == "file":
            fh = request.files.get("file")
            if fh is None or not fh.filename:
                raise ValueError("no_file: choose a .json or .csv file")
            parsed = _parse_import_file(fh.read(), fh.filename)
            if not parsed:
                raise ValueError("no_tracks: file contains no tracks")
            for doc in parsed:
                playlists.append(_add_imported_playlist(
                    doc["name"], doc["tracks"],
                    spotify_url=doc.get("spotify_url"), source="file"))
        else:
            tracks = _parse_track_text(request.form.get("text") or "")
            if not tracks:
                raise ValueError("no_tracks: one per line, Title | Artist")
            playlists.append(_add_imported_playlist(
                request.form.get("name") or "Imported playlist", tracks,
                source="text"))
    except Exception as exc:
        return make_response(
            "<!doctype html><title>Import failed</title>"
            f"<p>Import failed: {html_mod.escape(str(exc))}</p>"
            '<p><a href="/import">Back</a></p>'), 400
    sections = []
    for pl in playlists:
        rep = _playlist_report(pl)
        missing_html = "".join(
            f"<li>{html_mod.escape(t.get('title') or '')} - "
            f"{html_mod.escape(t.get('artist') or '')}</li>"
            for t in rep["missing_tracks"])
        download_q = html_mod.escape(
            urlencode({"playlist": pl.get("name") or ""}), quote=True)
        sections.append(
            f"<h1>{html_mod.escape(pl.get('name') or '')}</h1>"
            f"<p>{rep['matched']} of {rep['total']} track(s) matched your "
            f"uploaded files; {rep['missing_count']} missing.</p>"
            f"<ul>{missing_html}</ul>"
            f'<p><a href="/api/download-playlist?{download_q}">'
            "Download ZIP of matched (your) files</a></p>")
    return make_response(
        "<!doctype html><title>Imported</title>" + "".join(sections)
        + '<p><a href="/import">Import another</a></p>')


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

scan_library()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
