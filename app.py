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

import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import string
import threading
import time
import unicodedata
import zipfile
from pathlib import Path

import requests
from flask import (Flask, abort, jsonify, make_response, request,
                   send_file)
from werkzeug.utils import secure_filename

try:
    import mutagen
    from mutagen.id3 import ID3
except Exception:  # pragma: no cover - mutagen is a hard dependency in prod
    mutagen = None
    ID3 = None

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
        token_file.write_text(tok + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[music-server] WARNING: could not save token: {exc}", flush=True)
    print(f"[music-server] LIBRARY_TOKEN not set; generated token: {tok}", flush=True)
    print(f"[music-server] (saved to {token_file} - set LIBRARY_TOKEN on Railway "
          "to choose your own)", flush=True)
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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        print(f"[music-server] cache write failed {path}: {exc}", flush=True)


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


def _artists_match(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    ca, cb = _collapse_repeats(na), _collapse_repeats(nb)
    if ca == cb or ca in cb or cb in ca:
        return True
    return bool(set(na.split()) & set(nb.split()))


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
    if isinstance(tags, dict):
        # MP4 covr
        covr = tags.get("covr")
        if covr:
            try:
                return bytes(covr[0])
            except Exception:
                pass
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


def _extract_metadata(path: Path) -> dict:
    meta = {"title": None, "artist": None, "album": None,
            "duration_sec": None, "lyrics": None, "artwork": None}
    if mutagen is None:
        return meta
    try:
        audio = mutagen.File(str(path))
    except Exception as exc:
        print(f"[music-server] mutagen failed on {path.name}: {exc}", flush=True)
        return meta
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
        rel = str(path.relative_to(MUSIC_DIR))
        tid = track_id_for_rel(rel)
        meta = _extract_metadata(path)
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
    try:
        dur = rec.get("duration_sec")
        seed_dur = (seed_track.get("duration_ms") or 0) / 1000.0
        if dur is not None and seed_dur and abs(float(dur) - seed_dur) <= 3.0:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _match_file_for_seed(seed_track: dict, records: list[dict]):
    for rec in records:
        if _file_matches_seed(rec, seed_track):
            return rec
    return None


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
        if resp.status_code == 200:
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
        data = None
        if resp.status_code == 200:
            data = resp.json()
        if not data or not (data.get("plainLyrics") or data.get("syncedLyrics")):
            resp2 = _http_get("https://lrclib.net/api/search",
                              params={"artist_name": artist, "track_name": title})
            if resp2.status_code == 200:
                results = resp2.json() or []
                for item in results:
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
    with _cache_lock:
        _lyrics_cache[tid] = found  # negative results cached too
        _json_save(LYRICS_CACHE_PATH, _lyrics_cache)
    return found


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GB uploads
try:
    app.json.ensure_ascii = False  # Flask >= 2.2: keep Arabic titles readable
except Exception:
    pass


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
    for cand in candidates:
        if cand and hmac.compare_digest(str(cand), LIBRARY_TOKEN):
            return True
    return False


@app.before_request
def _auth_gate():
    path = request.path or ""
    if path.startswith("/api/"):
        if not _token_ok():
            return jsonify({"error": "unauthorized",
                            "detail": "missing or wrong library token"}), 401
    elif path == "/upload" and request.method != "GET":
        if not _token_ok():
            return jsonify({"error": "unauthorized",
                            "detail": "missing or wrong library token"}), 401
    return None


def _snapshot():
    with _index_lock:
        return list(_FILES.values()), [ _FILES[i] for i in _FILES_ORDER ]


def _public_track(rec: dict, spotify_url=None, album_override=None) -> dict:
    album = rec.get("album") or album_override
    if not album:
        enriched = itunes_lookup(rec.get("artist") or "", rec.get("title") or "")
        if enriched:
            album = enriched.get("album")
    with _cache_lock:
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
               "tracks": count, "playlists": len(SEED_PLAYLISTS)}
    if request.accept_mimetypes.best == "text/html":
        return make_response(
            "<!doctype html><title>Music Library Server</title>"
            f"<h1>Music Library Server</h1><p>OK &middot; {count} track(s) &middot; "
            f"{len(SEED_PLAYLISTS)} playlist(s)</p>"
            '<p><a href="/upload">Upload music</a></p>')
    return jsonify(payload)


@app.route("/api/catalog")
def catalog():
    _all, ordered = _snapshot()
    library = [_public_track(rec) for rec in ordered]
    playlists = []
    for pl in SEED_PLAYLISTS:
        tracks = []
        for st in pl.get("tracks", []):
            rec = _match_file_for_seed(st, ordered)
            if rec is not None:
                tracks.append(_public_track(rec, spotify_url=st.get("spotify_url")))
            else:
                dur = None
                if st.get("duration_ms"):
                    dur = round(st["duration_ms"] / 1000.0, 3)
                tracks.append({
                    "id": None,
                    "title": st.get("title"),
                    "artist": st.get("artist"),
                    "album": None,
                    "duration_sec": dur,
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
    try:
        resp = _http_get("https://api.deezer.com/search/artist", params={"q": name})
        if resp.status_code == 200:
            items = (resp.json().get("data") or [])
            if items:
                pic_url = items[0].get("picture_medium") or items[0].get("picture")
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
<form method="GET" action="/upload">
  <input type="password" name="token" placeholder="Library token" required>
  <button type="submit">Continue</button>
</form>
"""

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
Files are saved flat - tags decide artist/album and playlist matching.</p>
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
    if supplied and hmac.compare_digest(str(supplied), LIBRARY_TOKEN):
        resp.set_cookie("library_token", LIBRARY_TOKEN, max_age=30 * 24 * 3600,
                        httponly=True, samesite="Lax")
    return resp


def _unique_dest(name: str) -> Path:
    dest = MUSIC_DIR / name
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        n = 2
        while dest.exists():
            dest = MUSIC_DIR / f"{stem}-{n}{suffix}"
            n += 1
    return dest


_MAX_ZIP_TOTAL = 5 * 1024 * 1024 * 1024   # 5 GB extracted per ZIP
_MAX_ZIP_FILE = 300 * 1024 * 1024         # 300 MB per file inside a ZIP


def _save_zip(fh) -> int:
    """Extract audio files from an uploaded ZIP into MUSIC_DIR (flat).

    Only basenames are used (no path traversal), non-audio entries are
    skipped (including .lrc/.txt lyrics sidecars, which ARE kept next to
    nothing - they are saved with their audio-matching basename so the
    scanner picks them up as sidecars for same-named tracks).
    Returns the number of audio files saved.
    """
    saved = 0
    extracted = 0
    try:
        zf = zipfile.ZipFile(io.BytesIO(fh.read()))
    except zipfile.BadZipFile:
        return 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        base = secure_filename(Path(info.filename).name)
        if not base:
            continue
        suffix = Path(base).suffix.lower()
        is_audio = suffix in AUDIO_EXTS
        is_sidecar = suffix in (".lrc", ".txt")
        if not is_audio and not is_sidecar:
            continue
        if info.file_size > _MAX_ZIP_FILE or extracted + info.file_size > _MAX_ZIP_TOTAL:
            continue
        try:
            data = zf.read(info)
        except Exception:
            continue
        extracted += len(data)
        dest = _unique_dest(base)
        # A sidecar that collides with an existing sidecar would be renamed
        # and stop matching its track; skip duplicate sidecars instead.
        if is_sidecar and (MUSIC_DIR / base).exists():
            continue
        dest.write_bytes(data)
        if is_audio:
            saved += 1
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
        name = secure_filename(Path(raw_name).name)
        if not name or Path(name).suffix.lower() not in AUDIO_EXTS:
            continue
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
# Startup
# ---------------------------------------------------------------------------

scan_library()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
