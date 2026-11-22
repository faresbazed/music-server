import io
import stat
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from test_upload_filenames import wav_bytes


PID = "P" * 22
BASE = f"https://api.spotify.com/v1/playlists/{PID}"
CALLBACK = "/spotify/callback"


def reply(body, status=200):
    return Mock(status_code=status, json=Mock(return_value=body))


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test-client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "https://library.example" + CALLBACK)


def test_connect_requires_library_browser_and_matching_state(configured, client, monkeypatch,
                                                             library_token, data_dir):
    exchange = Mock(return_value=reply({"access_token": "access", "refresh_token": "refresh"}))
    monkeypatch.setattr(requests, "post", exchange)
    assert client.post("/spotify/connect").status_code == 401
    assert client.get(CALLBACK).status_code == 401
    assert client.post("/login", data={"token": "wrong"}).status_code == 401
    location = client.post("/login", data={"token": library_token, "page": "/import"}).location
    assert location == "/import"
    start = client.post("/spotify/connect")
    query = parse_qs(urlsplit(start.location).query)
    assert query["redirect_uri"] == ["https://library.example" + CALLBACK]
    assert client.get(CALLBACK, query_string={"state": "other", "code": "ignored"}).status_code == 400
    exchange.assert_not_called()
    start = client.post("/spotify/connect")
    state = parse_qs(urlsplit(start.location).query)["state"][0]
    callback = {"state": state, "code": "good"}
    assert client.get(CALLBACK, query_string=callback).location == "/import"
    assert client.get(CALLBACK, query_string=callback).status_code == 400
    assert exchange.call_args.kwargs["data"]["grant_type"] == "authorization_code"
    token_file = data_dir / "spotify_refresh_token"
    assert token_file.read_text() == "refresh"
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


@pytest.mark.parametrize("field,entry", [("items", "item"), ("tracks", "track")])
def test_import_to_ipad_download(configured, app_factory, data_dir, auth_headers, monkeypatch,
                                 field, entry):
    client = app_factory().test_client()
    audio = wav_bytes()
    client.post("/upload", headers=auth_headers, data={"files": (io.BytesIO(audio), "First.wav")})
    token_file = data_dir / "spotify_refresh_token"
    token_file.write_text("old-refresh")
    token = Mock(return_value=reply({"access_token": "access", "refresh_token": "new-refresh"}))
    monkeypatch.setattr(requests, "post", token)
    next_url = BASE + f"/{field}?offset=1"
    get = Mock(side_effect=[
        reply({"name": "iPad test", field: {"items": [
            {entry: {"name": "First", "duration_ms": 10}},
            {entry: None}, {entry: {"type": "episode", "name": "Not music"}},
        ], "next": next_url}}),
        reply({"items": [{entry: {"name": "Missing"}}], "next": None}),
    ])
    monkeypatch.setattr(requests, "get", get)
    imported = client.post("/api/import/spotify", headers=auth_headers,
                           json={"url": "https://open.spotify.com/playlist/" + PID})
    assert imported.status_code == 200
    report = imported.json["report"]
    assert (report["matched"], report["missing_count"]) == (1, 1)
    assert [call.args[0] for call in get.call_args_list] == [BASE, next_url]
    assert token.call_args.kwargs["data"]["refresh_token"] == "old-refresh"
    assert token_file.read_text() == "new-refresh"

    # A new server process must still serve the uploaded bytes and imported list.
    restarted = app_factory().test_client()
    catalog = restarted.get("/api/catalog", headers=auth_headers).json
    first, missing = next(p["tracks"] for p in catalog["playlists"] if p["name"] == "iPad test")
    assert missing["has_file"] is False
    path = "/api/download/" + first["id"]
    assert restarted.get(path).status_code == 401
    assert restarted.get(path, headers=auth_headers).data == audio


@pytest.mark.parametrize("pages", [
    [reply({"name": "No access"})],
    [reply({"items": {"items": [], "next": BASE + "/items?offset=5"}}), reply({}, 429)],
    [reply({"items": {"items": [], "next": "https://other.example/items"}})],
    [requests.Timeout()],
])
def test_failure_never_saves_partial_playlist(configured, client, data_dir, auth_headers,
                                             monkeypatch, pages):
    monkeypatch.setattr(requests, "post", Mock(return_value=reply({"access_token": "access"})))
    get = Mock(side_effect=pages)
    monkeypatch.setattr(requests, "get", get)
    response = client.post("/api/import/spotify", headers=auth_headers, json={"id": PID})
    assert response.status_code == 400
    assert response.json["error"] == "import_failed"
    assert not (data_dir / "imported_playlists.json").exists()
    assert all(call.kwargs["allow_redirects"] is False for call in get.call_args_list)
    assert all(urlsplit(call.args[0]).hostname == "api.spotify.com" for call in get.call_args_list)
