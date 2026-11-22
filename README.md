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

## Playlist import and offline playback

Import a playlist's **track list** and match it against your own uploaded
recordings — from the web page **`/import`**, from the iPad app, or via the API:

1. **Spotify playlist link** — connect Spotify once (instructions below),
   then paste the link. This imports **metadata only**; it cannot export
   a Spotify Premium offline download as an audio file.
2. **Paste a track list** — one per line, `Title | Artist`.
3. **Upload a `.json` / `.csv`** track list (seed/Spotify shape, or
   `title,artist` columns).

Imported playlists are saved to `$DATA_DIR/imported_playlists.json` (so
they survive redeploys on the Volume), appear in `/api/catalog` next to
the seed playlists. Open a playlist in the app and tap **Save matched**
to copy its available recordings to **On My iPad**. `GET /api/missing?playlist=<name>` reports which
tracks you haven't uploaded yet, and
`GET /api/download-playlist?playlist=<name>` returns a ZIP of the
**matched files only** (your own MP3s) plus `playlist.m3u` and a
`missing.txt`. No audio is ever fetched from Spotify/YouTube/anywhere.

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
The iPad uses the header and requests `/api/catalog?enrich=0` to avoid
waiting for online album lookups before starting a download.

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
| `POST /api/import/spotify` | JSON `{url}` — import a Spotify playlist's track list (metadata only; needs the Spotify env keys) → `{ok, report}` |
| `POST /api/import/text` | JSON `{name, text}` — import a pasted track list → `{ok, report}` |
| `POST /api/import/json` | JSON playlist / `{playlists:[...]}` — import track lists → `{ok, report}` |
| `GET /api/missing?playlist=<name>` | Match report for any playlist: `{total, matched, missing_count, matched_tracks, missing_tracks}` |
| `GET /api/download-playlist?playlist=<name>` | ZIP of your own matched files for that playlist + `playlist.m3u` + `missing.txt` (404 if nothing matched yet) |
| `GET /import` | Web playlist-import page (token once, cookie after) |
| `GET /upload` | Web upload page (asks for the token once, stores a cookie) |
| `POST /upload` | Multipart upload, field name **`files`** (multiple). Saves into the music folder, then rescans. In **`.zip` files**, `.lrc` and `.txt` lyrics go with audio that has the same filename stem in the same ZIP folder. Tracks with conflicting stems are stored in separate folders to keep their filenames and lyrics intact. Up to 1 GB per request, so split very large libraries into a few ZIPs. |

## Deploy on Railway

1. **Push this folder to a GitHub repo** (or `railway up` from this folder).
   `playlists_seed.json` must be in the repo — it's the playlist data.
2. In Railway: **New Project → Deploy from GitHub repo**, pick the repo.
   Select the branch containing these changes. Railway reads `railway.json` and starts:
   `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4`
3. **Attach a Volume** (service → Settings → Volumes) mounted at **`/data`**.
   Without it, uploads and Spotify authorization are lost on redeploy.
4. **Set the environment variable `LIBRARY_TOKEN`** to a long secret you
   choose (service → Variables). If you skip this, the server generates a
   token on first boot in `/data/token.txt`; it does not log the value.
   Optional: `DATA_DIR=/data` (default), `MUSIC_DIR=/data/music` (default).
5. **Generate a public domain** (service → Settings → Networking) — you'll
   get a `https://….up.railway.app` URL.
6. **Upload your music:** open `https://<your-url>/upload` in a browser,
   enter the token once, then either select many files at once in the
   picker (Ctrl/Cmd+A in your music folder) or upload a ZIP of the whole
   folder — never one song at a time (your own files —
   e.g. exported from your PC library). Tags decide artist/album; file
   names don't matter much.
7. **Connect the iPad:** install the [native iPad client](ios/README.md)
   and enter the HTTPS Railway URL and the same library token. Open a
   playlist and download its available tracks. To check offline playback,
   enable Airplane Mode, quit and reopen the app, and play a saved track.
   You can try a seed playlist or import a CSV without any Spotify credentials.

### Connect Spotify for live playlist links

1. In the [Spotify developer dashboard](https://developer.spotify.com/dashboard),
   configure a Web API app and register `https://<your-railway-domain>/spotify/callback`
   as a redirect URI. In [development mode](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
   the app owner needs Premium; playlist contents are available only for
   playlists the signed-in user owns or collaborates on.
2. In Railway service variables set `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`,
   and `SPOTIFY_REDIRECT_URI` (exactly the registered URI); deploy the changes.
3. In a browser, open **/import**, enter the library token, and choose
   **Connect Spotify account**. After consent, paste the playlist link in
   the app or browser. Upload missing recordings via **/upload**.
   The refresh token is private to the server volume; no Spotify credentials
   go in the IPA. To disconnect, [revoke access](https://www.spotify.com/account/apps/)
   and remove `$DATA_DIR/spotify_refresh_token`. Reconnect if authorization expires.

Previously authorized extended-quota apps can still import supported public
playlists with client credentials. The app reports inaccessible playlists
and failed pages instead of saving an empty or partial import.

## Run locally

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
LIBRARY_TOKEN=test123 DATA_DIR=./data .venv/bin/python app.py
# or: DATA_DIR=./data LIBRARY_TOKEN=test123 .venv/bin/gunicorn app:app --bind 0.0.0.0:5000 --workers 1 --threads 4
curl -H "X-Library-Token: test123" http://localhost:5000/api/catalog
```

## Run tests

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Files

- `app.py`, `spotify_client.py` — Flask library server and Spotify metadata client
- `playlists_seed.json` — Spotify playlist export (do not edit by hand)
- `requirements.txt`, `Procfile`, `railway.json` — deployment
- Runtime data (never committed): `$DATA_DIR/music/` (uploads),
  `$DATA_DIR/cache/` (artwork, artist images, lyrics + enrichment caches),
  `$DATA_DIR/token.txt` (only when `LIBRARY_TOKEN` is not set)
