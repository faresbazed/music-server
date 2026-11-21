import json
import sys
import wave

import pytest
from mutagen.id3 import TIT2
from mutagen.wave import WAVE


@pytest.mark.parametrize(("text", "expected"), [
    ("Hello, Goodbye", [("Hello, Goodbye", None)]),
    ("Hello, Goodbye\nYesterday - The Beatles",
     [("Hello, Goodbye", None), ("Yesterday", "The Beatles")]),
    ("Hello, Goodbye\nSomeone Like You",
     [("Hello, Goodbye", None), ("Someone Like You", None)]),
    ("Yesterday, The Beatles\nLet It Be, The Beatles",
     [("Yesterday", "The Beatles"), ("Let It Be", "The Beatles")]),
    ("\n# tracks\nYesterday, The Beatles\n\nLet It Be, The Beatles\n",
     [("Yesterday", "The Beatles"), ("Let It Be", "The Beatles")]),
    ("# comment, ignored\nHello, Goodbye\n", [("Hello, Goodbye", None)]),
    ("Hello, Goodbye | The Beatles\nYesterday\tThe Beatles\nLet It Be - The Beatles",
     [("Hello, Goodbye", "The Beatles"), ("Yesterday", "The Beatles"),
      ("Let It Be", "The Beatles")]),
    ("", []),
    ("\n # comment\n", []),
])
def test_parse_track_text(app, text, expected):
    tracks = sys.modules["app"]._parse_track_text(text)
    assert [(t["title"], t["artist"]) for t in tracks] == expected


def test_comma_title_import_with_local_file(client, auth_headers, tmp_path):
    path = tmp_path / "hello.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 8000)
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text="Hello, Goodbye"))
    audio.save()
    with path.open("rb") as upload:
        response = client.post("/upload?format=json", headers=auth_headers,
                               data={"files": (upload, path.name)})
    assert response.get_json()["tracks"] == 1
    response = client.post("/api/import/text", headers=auth_headers,
                           json={"name": "Comma title", "text": "Hello, Goodbye"})
    assert response.status_code == 200
    report = response.get_json()["report"]
    assert report["matched"] == 0
    assert report["missing_count"] == 1
    assert report["missing_tracks"][0]["title"] == "Hello, Goodbye"
    assert report["missing_tracks"][0]["artist"] is None
    module = sys.modules["app"]
    stored = json.loads(module.IMPORTED_PATH.read_text())["playlists"]
    track, = next(p["tracks"] for p in stored if p["name"] == "Comma title")
    assert track["title"] == "Hello, Goodbye"
    assert track["artist"] is None


@pytest.mark.parametrize("text", ["", "\n# no tracks\n"])
def test_empty_text_import(client, auth_headers, text):
    response = client.post("/api/import/text", headers=auth_headers,
                           json={"text": text})
    assert response.status_code == 400
    assert response.get_json()["error"] == "no_tracks"
