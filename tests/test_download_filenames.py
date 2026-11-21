import io
import json
import unicodedata
import zipfile
from urllib.parse import unquote

import pytest
from werkzeug.http import parse_options_header

from test_upload_filenames import wav_bytes


def test_arabic_playlist_download(client, auth_headers):
    response = client.post(
        "/upload?format=json", headers=auth_headers,
        data={"files": (io.BytesIO(wav_bytes()), "عمرو.wav")},
    )
    assert response.get_json()["saved"] == 1
    playlist = {"name": "أغاني عمرو", "tracks": [{"title": "عمرو", "duration_ms": 10}]}
    response = client.post(
        "/import", headers=auth_headers,
        data={"mode": "file", "file": (io.BytesIO(json.dumps(playlist).encode()), "list.json")},
    )
    assert response.status_code == 200
    response = client.get(
        "/api/download-playlist", query_string={"playlist": playlist["name"]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
    assert unquote(response.headers["Content-Disposition"].split("filename*=UTF-8''")[1]) == "أغاني عمرو.zip"
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert archive.namelist() == ["001 - عمرو.wav", "playlist.m3u"]
        assert archive.read("playlist.m3u").decode() == "#EXTM3U\n001 - عمرو.wav\n"


@pytest.mark.parametrize("name", ["../../etc/passwd", "a/b\\c", '\x00a\x01b\n<>:"|?*', "", " . \x00 ", "cafe\u0301", "ع" * 300])
def test_download_filename_sanitizer(app, name):
    sanitize = app.view_functions["download_playlist"].__globals__["_download_filename"]
    result = sanitize(name, "playlist")
    assert result
    assert len(result) <= 180
    assert len(result.encode("utf-8")) <= 180
    assert not any(char in result for char in '<>:"/\\|?*')
    assert not any(unicodedata.category(char).startswith("C") for char in result)
    assert result not in (".", "..")
    assert result == result.strip(" .")
    assert result == unicodedata.normalize("NFC", result)
    if name in ("", " . \x00 "):
        assert result == "playlist"


@pytest.mark.parametrize("name", ["../../etc/passwd", "a/b\\c", '\x00a\x01b\n<>:"|?*', "", " . \x00 "])
def test_unsafe_download_names(client, app, auth_headers, monkeypatch, name):
    client.post(
        "/upload?format=json", headers=auth_headers,
        data={"files": (io.BytesIO(wav_bytes()), "Song.wav")},
    )
    namespace = app.view_functions["download_playlist"].__globals__
    record = namespace["_snapshot"]()[1][0]
    monkeypatch.setitem(namespace, "_find_playlist", lambda _: {"name": name, "tracks": [{"title": name}]})
    monkeypatch.setitem(namespace, "_match_file_for_seed", lambda *_: record)
    response = client.get("/api/download-playlist?playlist=test", headers=auth_headers)
    assert response.status_code == 200
    filename = parse_options_header(response.headers["Content-Disposition"])[1]["filename"]
    assert filename == namespace["_download_filename"](name, "playlist") + ".zip"
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        song, m3u = archive.namelist()
        assert m3u == "playlist.m3u"
        assert not any(char in song for char in '<>:"/\\|?*\x00\n')
        assert not any(unicodedata.category(char).startswith("C") for char in song)
        assert song not in (".", "..")
        assert archive.read(m3u).decode() == f"#EXTM3U\n{song}\n"


def test_download_no_matches(client, auth_headers):
    client.post("/api/import/json", headers=auth_headers,
                json={"name": "Empty", "tracks": [{"title": "Absent"}]})
    response = client.get("/api/download-playlist?playlist=Empty", headers=auth_headers)
    assert response.status_code == 404
    assert response.get_json()["error"] == "no_matched_files"


def test_download_missing_playlist(client, auth_headers):
    response = client.get("/api/download-playlist?playlist=absent", headers=auth_headers)
    assert response.status_code == 404
    assert response.get_json() == {"error": "playlist_not_found"}
