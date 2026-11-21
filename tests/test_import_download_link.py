import html
import io
import json
import re
import zipfile

import pytest

from test_upload_filenames import wav_bytes


@pytest.mark.parametrize(
    "name",
    ["Road & Rail + Faves #1", "50% off?", "Rock/Pop", "حفلة & سهرة", "Plain"],
)
def test_import_download_link_finds_playlist(client, auth_headers, name):
    client.post(
        "/upload?format=json",
        headers=auth_headers,
        data={"files": (io.BytesIO(wav_bytes()), "Song.wav")},
    )
    playlist = {"name": name, "tracks": [{"title": "Song", "duration_ms": 10}]}
    response = client.post(
        "/import",
        headers=auth_headers,
        data={"mode": "file", "file": (io.BytesIO(json.dumps(playlist).encode()), "list.json")},
    )
    assert response.status_code == 200

    match = re.search(r'href="(/api/download-playlist\?[^"]*)"', response.get_data(as_text=True))
    assert match
    download = client.get(html.unescape(match.group(1)), headers=auth_headers)

    assert download.status_code == 200, download.get_json()
    with zipfile.ZipFile(io.BytesIO(download.data)) as zf:
        assert any(n.endswith("Song.wav") for n in zf.namelist())
