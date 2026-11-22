# Music Library Server

Backend for Fares's **Offline Music** iPad app (iOS 9). It holds **your own
uploaded music files**, organises them by your Spotify playlists, and adds
album covers and lyrics.

**Get started:** [Deploy the server](#deploy-on-railway) → [add a playlist](#add-a-playlist) → [install the IPA and save your first track](#install-the-ipa-and-save-your-first-track).
If your server is already running, you only need its HTTPS address, library token and the [iPad release](https://github.com/faresbazed/music-server/releases/latest).

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

1. In the [Railway dashboard](https://railway.com/dashboard), choose **New Project → Deploy from GitHub repo**. Select `faresbazed/music-server` (or your fork) and branch `main`. If you already have a service for this repo, use that service instead.
2. Attach a [persistent volume](https://docs.railway.com/volumes) to the service at **`/data`** before uploading anything. Uploads, imported playlists and Spotify authorization live there.
3. In **Variables**, set `DATA_DIR` to `/data` and `LIBRARY_TOKEN` to a long random secret from your password manager. Save the token for the iPad. Deploy the changes; no custom start command is needed.
4. Once the deployment is healthy, open **Settings → Networking → Generate Domain**. Open the resulting HTTPS address in a browser. The library home page should offer links to upload music and import playlists. Keep this address for the app.

### Add a playlist

1. Open `/upload` on your server, enter your library token, and select an MP3 or AAC/M4A recording you have permission to copy. Click **Upload**. You can also select many files or upload a ZIP.
2. Open `/import`. For a first test without a Spotify account, choose **Paste a track list**, name it `First sync`, and enter the uploaded song’s tagged title and artist as `Title | Artist`. Click **Import track list**. For a live Spotify link instead, follow [Connect Spotify](#connect-spotify-for-live-playlist-links).
3. Check the import report. At least one track must be **matched** to download anything; for a missing track, upload the corresponding recording and check its title and artist tags.

### Install the IPA and save your first track

1. On a Mac or Windows PC, open the [latest GitHub release](https://github.com/faresbazed/music-server/releases/latest). Under **Assets**, download **OfflineMusic.ipa**, not the source-code ZIP. The app targets iPad on iOS 9.0 or later.
2. Install [Sideloadly](https://sideloadly.io/) on the computer. Connect your unlocked iPad over USB, choose **Trust** if prompted, and select the iPad and the downloaded IPA in Sideloadly.
3. Enter your Apple ID in Sideloadly and click **Start** to sign the app for your device and install it. The release is **not pre-signed for your iPad**. If iOS prompts you, trust your own developer identity under **Settings → General → Device Management** (the name varies by iOS version). Free Apple ID signing typically needs renewal every seven days. For Apple’s tools instead, see the [Mac signing instructions](ios/README.md#sign-and-install-on-a-stock-ipad).
4. Open **Offline Music → Playlists → Settings** on the iPad. Enter the HTTPS server address **without** `/upload` or `/import`, enter the same library token, and tap **Connect**.
5. Open your playlist and tap **Save matched**. Keep the app in the foreground until the summary appears. Then enable airplane mode, close and reopen the app, and tap the song under **On My iPad**. If it plays, the audio is on the iPad, not streaming.

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
