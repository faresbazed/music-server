import io
import json

import pytest


@pytest.mark.parametrize("kind", ["multiple", "single", "list", "csv"])
def test_file_import(client, data_dir, auth_headers, kind):
    first = {"name": "First playlist", "tracks": [{"title": "First song"}]}
    second = {"name": "Second playlist", "tracks": [{"title": "Second song"}]}
    filename = "uploaded.json"
    names = ["First playlist"]
    if kind == "multiple":
        payload = json.dumps({"playlists": [first, second]}).encode()
        names.append("Second playlist")
    elif kind == "single":
        payload = json.dumps(first).encode()
    elif kind == "list":
        payload = json.dumps(first["tracks"]).encode()
        names = ["uploaded"]
    else:
        filename = "uploaded.csv"
        payload = b"title,artist\nFirst song,An artist\n"
        names = ["uploaded"]
    response = client.post(
        "/import", headers=auth_headers,
        data={"mode": "file", "file": (io.BytesIO(payload), filename)},
    )
    assert response.status_code == 200
    saved = json.loads((data_dir / "imported_playlists.json").read_text())
    assert [pl["name"] for pl in saved["playlists"]] == names
    catalog = client.get("/api/catalog", headers=auth_headers).get_json()
    assert set(names) <= {pl["name"] for pl in catalog["playlists"]}
    for name in names:
        assert name in response.get_data(as_text=True)


@pytest.mark.parametrize("payload", [b'{"playlists": []}', b"not json"])
def test_invalid_file_import(client, data_dir, auth_headers, payload):
    response = client.post(
        "/import", headers=auth_headers,
        data={"mode": "file", "file": (io.BytesIO(payload), "uploaded.json")},
    )
    assert response.status_code == 400
    assert b"Import failed" in response.data
    assert not (data_dir / "imported_playlists.json").exists()
