import json

import pytest


def playlist(duration_ms):
    return {
        "name": "Duration Example",
        "tracks": [
            {
                "title": "Not Uploaded Yet",
                "artist": "Example Artist",
                "duration_ms": duration_ms,
            }
        ],
    }


def catalogue_track(client, auth_headers):
    response = client.get("/api/catalog", headers=auth_headers)
    assert response.status_code == 200
    tracks, = (
        item["tracks"]
        for item in response.get_json()["playlists"]
        if item["name"] == "Duration Example"
    )
    track, = tracks
    return track


@pytest.mark.parametrize(
    ("duration_ms", "canonical_ms", "duration_sec"),
    [("180000", 180000, 180.0), ("not a duration", None, None), ("Infinity", None, None)],
)
def test_catalogue_after_json_import(
    client, data_dir, auth_headers, duration_ms, canonical_ms, duration_sec
):
    response = client.post(
        "/api/import/json", headers=auth_headers, json=playlist(duration_ms)
    )
    assert response.status_code == 200
    track = catalogue_track(client, auth_headers)
    assert track["has_file"] is False
    assert track["duration_sec"] == duration_sec
    assert response.get_json()["report"]["missing_tracks"][0]["duration_ms"] == canonical_ms
    saved = json.loads((data_dir / "imported_playlists.json").read_text(encoding="utf-8"))
    assert saved["playlists"][0]["tracks"][0]["duration_ms"] == canonical_ms


@pytest.mark.parametrize(
    ("duration_ms", "duration_sec"),
    [("180000", 180.0), ("not a duration", None)],
)
def test_catalogue_with_preexisting_playlist(
    app_factory, data_dir, auth_headers, duration_ms, duration_sec
):
    (data_dir / "imported_playlists.json").write_text(
        json.dumps({"playlists": [playlist(duration_ms)]}), encoding="utf-8"
    )
    client = app_factory().test_client()
    track = catalogue_track(client, auth_headers)
    assert track["has_file"] is False
    assert track["duration_sec"] == duration_sec
