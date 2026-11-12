# Music Library Server

Backend for Fares's **Offline Music** iPad app (iOS 9). It holds **your own
uploaded music files**, organises them by your Spotify playlists, and adds
album covers and lyrics.

> **Boundary:** this server **never** downloads audio from Spotify, YouTube
> or anywhere else. Audio only ever arrives through the `/upload` page —
> files you own. Only *metadata* is fetched from public APIs: album artwork
> / album names from the iTunes Search API, artist photos from Deezer, and
> lyrics from LRCLIB.

Playlists come from `playlists_seed.json` (a read-only Spotify export:
**11 playlists, 445 tracks** — *Liked Songs* 24, the artist playlists, and
the two big ones: **“the devil made me do it”** (185 tracks, Marwan Pablo)
and **“The cycle ends here”** (211 tracks, Travis Scott / Yeat / Drake /
Metro Boomin / Kendrick Lamar …)). All playlists ship in the catalogue — a
playlist track with no uploaded file yet simply shows `has_file: false`
until you upload a file whose title + artist (or duration) matches it.

## How it works

- On start (and on `POST /api/rescan`) it scans the music folder with
  **mutagen**: title, artist, album, duration, embedded cover art and
  embedded lyrics (ID3 `USLT`, MP4 `©lyr`, Vorbis `LYRICS`). A `.lrc` or
  `.txt` file sitting next to an audio file is used as sidecar lyrics.
- Local files are matched to playlist tracks by normalised title
  (case / punctuation / diacritics ignored, repeated letters collapsed —
  so “Ellisa” ≈ “Elissa”) **and** artist overlap or duration within 3 s.
  One file can appear in several playlists.
- Enrichment is cached on disk (`cache/` under the data dir), including
  negative results, so repeat requests are fast and offline-tolerant.
  All external lookups use a 6 s timeout and fail gracefully.

## API (all `/api/*` need the token)

Send the token as header `X-Library-Token: <token>`, or `?token=<token>`.

| Endpoint | What it returns |
|---|---|
| `GET /` | Status: `{ok, tracks, playlists}` (no token needed) |
| `GET /api/catalog` | `{playlists: [{name, spotify_url, tracks: [TRACK…]}], library: [TRACK…]}` — TRACK = `{id, title, artist, album, duration_sec, ext, has_file, has_artwork, has_lyrics, spotify_url}`; unmatched playlist tracks have `id: null, has_file: false` |
| `GET /api/artwork/<id>` | Cover image (embedded first, else iTunes 600×600), 404 if none |
| `GET /api/artist-image?artist=<name>` | Artist photo (Deezer), cached, 404 if none |
| `GET /api/lyrics/<id>` | `{plain, synced, source}` — source: `embedded` → `sidecar` → `lrclib` → `none`; `synced` is raw `.lrc` |
| `GET /api/stream/<id>` | Audio stream with HTTP Range support (seeking) |
| `GET /api/download/<id>` | Same file as an attachment (for offline saving in the app) |
| `POST /api/rescan` | Re-scan the music folder → `{ok, tracks}` |
| `GET /upload` | Web upload page (asks for the token once, stores a cookie) |
| `POST /upload` | Multipart upload, field name **`files`** (multiple). Saves flat into the music folder, then rescans. |

## Deploy on Railway

1. **Push this folder to a GitHub repo** (or `railway up` from this folder).
   `playlists_seed.json` must be in the repo — it's the playlist data.
2. In Railway: **New Project → Deploy from GitHub repo**, pick the repo.
   Railway reads `railway.json` / `Procfile` and starts:
   `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4`
3. **Attach a Volume** (service → Settings → Volumes) mounted at **`/data`**.
   Without it, uploaded music is wiped on every redeploy. Note Railway
   Hobby volumes are capped at **5 GB** (~700–900 MP3s); a bigger library
   needs Pro or object storage (e.g. Cloudflare R2) later.
4. **Set the environment variable `LIBRARY_TOKEN`** to a long secret you
   choose (service → Variables). If you skip this, the server generates a
   token on first boot, saves it to `/data/token.txt` and prints it in the
   deploy logs — grab it from there.
   Optional: `DATA_DIR=/data` (default), `MUSIC_DIR=/data/music` (default).
5. **Generate a public domain** (service → Settings → Networking) — you'll
   get a `https://….up.railway.app` URL.
6. **Upload your music:** open `https://<your-url>/upload` in a browser,
   enter the token once, and upload your MP3/M4A files (your own files —
   e.g. exported from your PC library). Tags decide artist/album; file
   names don't matter much.
7. **Point the iPad app at it:** in Offline Music v2's server settings,
   enter the Railway URL and the same `LIBRARY_TOKEN`. The app can then
   browse the catalogue by playlist, stream, see covers/lyrics, and
   download tracks for offline playback.

## Run locally

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
LIBRARY_TOKEN=test123 DATA_DIR=./data .venv/bin/python app.py
# or: DATA_DIR=./data LIBRARY_TOKEN=test123 .venv/bin/gunicorn app:app --bind 0.0.0.0:5000 --workers 1 --threads 4
curl -H "X-Library-Token: test123" http://localhost:5000/api/catalog
```

## Files

- `app.py` — the whole server (Flask, single file)
- `playlists_seed.json` — Spotify playlist export (do not edit by hand)
- `requirements.txt`, `Procfile`, `railway.json` — deployment
- Runtime data (never committed): `$DATA_DIR/music/` (uploads),
  `$DATA_DIR/cache/` (artwork, artist images, lyrics + enrichment caches),
  `$DATA_DIR/token.txt` (only when `LIBRARY_TOKEN` is not set)
