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


@pytest.mark.parametrize("payload,artist,duration_ms", [
    (b"Title,Artist\nHello,Adele\n", "Adele", None),
    (b" NAME , ARTIST , DURATION_MS \nHello,Adele,295000\n",
     "Adele", 295000),
    (b"Track Name,Artist Name(s),Duration (ms)\nHello,Adele,invalid\n",
     "Adele", None),
    (b'Track URI,Track Name,Artist Name(s),Album Name,Duration (ms)\n'
     b'spotify:track:123,Hello,"Adele, Guest",25,295000\n',
     "Adele, Guest", 295000),
])
def test_csv_header_aliases(client, data_dir, auth_headers, payload, artist,
                            duration_ms):
    response = client.post(
        "/import", headers=auth_headers,
        data={"mode": "file", "file": (io.BytesIO(payload), "uploaded.csv")},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    saved = json.loads((data_dir / "imported_playlists.json").read_text())
    tracks = saved["playlists"][0]["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["title"] == "Hello"
    assert tracks[0]["artist"] == artist
    assert tracks[0]["duration_ms"] == duration_ms


@pytest.mark.parametrize("payload", [
    b"", b"Title,Artist\n", b"Title,Artist\n,Adele\n",
    b"Unknown,Artist\nHello,Adele\n",
])
def test_csv_without_tracks(client, data_dir, auth_headers, payload):
    response = client.post(
        "/import", headers=auth_headers,
        data={"mode": "file", "file": (io.BytesIO(payload), "uploaded.csv")},
    )
    assert response.status_code == 400
    assert b"no_tracks" in response.data
    assert not (data_dir / "imported_playlists.json").exists()


@pytest.mark.parametrize("payload", [b'{"playlists": []}', b"not json"])
def test_invalid_file_import(client, data_dir, auth_headers, payload):
    response = client.post(
        "/import", headers=auth_headers,
        data={"mode": "file", "file": (io.BytesIO(payload), "uploaded.json")},
    )
    assert response.status_code == 400
    assert b"Import failed" in response.data
    assert not (data_dir / "imported_playlists.json").exists()
