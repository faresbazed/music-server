"""Read playlist metadata from Spotify on behalf of the library owner."""

import os
import secrets
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests


class SpotifyClient:
    def __init__(self, data_dir: Path):
        self.token_path = data_dir / "spotify_refresh_token"
        self.lock = threading.Lock()

    @staticmethod
    def credentials():
        cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
        secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
        if not cid or not secret:
            raise ValueError("spotify_not_configured: set SPOTIFY_CLIENT_ID and "
                             "SPOTIFY_CLIENT_SECRET, or import a text/CSV track list.")
        return cid, secret

    @staticmethod
    def redirect_uri():
        uri = os.environ.get("SPOTIFY_REDIRECT_URI", "").strip()
        parts = urlsplit(uri)
        loopback = parts.scheme == "http" and parts.hostname in ("127.0.0.1", "::1")
        if (not parts.hostname or parts.path != "/spotify/callback" or parts.query
                or parts.fragment or parts.username or parts.password
                or (parts.scheme != "https" and not loopback)):
            raise ValueError("Set SPOTIFY_REDIRECT_URI to your HTTPS server URL "
                             "followed by /spotify/callback and register it with Spotify.")
        return uri

    def authorization(self):
        cid, _ = self.credentials()
        state = secrets.token_urlsafe(32)
        return state, "https://accounts.spotify.com/authorize?" + urlencode({
            "response_type": "code", "client_id": cid, "state": state,
            "scope": "playlist-read-private playlist-read-collaborative",
            "redirect_uri": self.redirect_uri(),
        })

    def _token(self, data):
        try:
            response = requests.post("https://accounts.spotify.com/api/token",
                                     data=data, auth=self.credentials(), timeout=6,
                                     allow_redirects=False)
        except requests.RequestException:
            raise ValueError("spotify_unavailable: could not reach Spotify; try again.") from None
        if response.status_code != 200:
            raise ValueError(f"spotify_auth_failed: HTTP {response.status_code}. "
                             "Check the app credentials or reconnect Spotify on /import.")
        body = response.json()
        if not body.get("access_token"):
            raise ValueError("spotify_auth_failed: missing access token.")
        return body

    def _save_refresh(self, value):
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.token_path.parent,
                                             delete=False) as out:
                tmp = Path(out.name)
                out.write(value)
            tmp.replace(self.token_path)
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)

    def connect(self, code):
        with self.lock:
            tokens = self._token({"grant_type": "authorization_code", "code": code,
                                  "redirect_uri": self.redirect_uri()})
            if not tokens.get("refresh_token"):
                raise ValueError("Spotify did not grant persistent access. Reconnect on /import.")
            self._save_refresh(tokens["refresh_token"])

    def _access_token(self):
        with self.lock:
            if not self.token_path.exists():
                # Existing extended-quota installations can read public playlists.
                return self._token({"grant_type": "client_credentials"})["access_token"]
            current = self.token_path.read_text().strip()
            tokens = self._token({"grant_type": "refresh_token", "refresh_token": current})
            if tokens.get("refresh_token") and tokens["refresh_token"] != current:
                self._save_refresh(tokens["refresh_token"])
            return tokens["access_token"]

    def playlist(self, playlist_id):
        headers = {"Authorization": "Bearer " + self._access_token()}
        base = f"https://api.spotify.com/v1/playlists/{playlist_id}"

        def fetch(url):
            try:
                response = requests.get(url, headers=headers, timeout=6, allow_redirects=False)
            except requests.RequestException:
                raise ValueError("spotify_unavailable: could not reach Spotify; try again.") from None
            if response.status_code != 200:
                hint = (" Connect Spotify on /import and use a playlist you own or collaborate on."
                        if response.status_code in (401, 403, 404) else
                        " Try again later." if response.status_code == 429 else "")
                raise ValueError(f"spotify_fetch_failed: HTTP {response.status_code}.{hint}")
            return response.json()

        data = fetch(base)
        page = data.get("items", data.get("tracks"))
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            raise ValueError("spotify_playlist_unavailable: connect Spotify on /import and "
                             "use a playlist you own or collaborate on, or import a CSV.")
        items = list(page["items"])
        seen = set()
        while page.get("next"):
            url = page["next"]
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.netloc != "api.spotify.com"
                    or parsed.path not in (f"/v1/playlists/{playlist_id}/items",
                                           f"/v1/playlists/{playlist_id}/tracks")
                    or url in seen):
                raise ValueError("spotify_fetch_failed: unexpected pagination link.")
            seen.add(url)
            page = fetch(url)
            if not isinstance(page.get("items"), list):
                raise ValueError("spotify_fetch_failed: incomplete playlist page.")
            items.extend(page["items"])
        return data, items
