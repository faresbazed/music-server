import io
import sys
import wave
import zipfile

import pytest
from mutagen.id3 import TIT2, TPE1
from mutagen.wave import WAVE


def tagged_audio(path, artist):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 8000)
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text="Lights"))
    audio.tags.add(TPE1(encoding=3, text=artist))
    audio.save()
    return path.read_bytes()


def test_import_and_zip_choose_requested_artist(client, auth_headers, tmp_path):
    uploads = [
        (io.BytesIO(tagged_audio(tmp_path / name, artist)), name)
        for name, artist in [("a-killers.wav", "The Killers"),
                             ("b-weeknd.wav", "The Weeknd")]
    ]
    expected = uploads[1][0].getvalue()
    response = client.post("/upload?format=json", headers=auth_headers,
                           data={"files": uploads})
    assert response.get_json()["tracks"] == 2
    response = client.post("/api/import/text", headers=auth_headers,
                           json={"name": "Lights test", "text": "Lights | The Weeknd"})
    assert response.status_code == 200
    catalog = client.get("/api/catalog", headers=auth_headers).get_json()
    track, = next(p["tracks"] for p in catalog["playlists"] if p["name"] == "Lights test")
    assert track["artist"] == "The Weeknd"
    weeknd = next(t for t in catalog["library"] if t["artist"] == "The Weeknd")
    for report in [response.get_json()["report"], client.get(
        "/api/missing?playlist=Lights+test", headers=auth_headers
    ).get_json()]:
        assert report["missing_count"] == 0
        assert report["matched_tracks"][0]["id"] == weeknd["id"]
    response = client.get("/api/download-playlist?playlist=Lights+test", headers=auth_headers)
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        files = [name for name in archive.namelist() if name.endswith(".wav")]
        assert len(files) == 1
        assert archive.read(files[0]) == expected


@pytest.mark.parametrize(("a", "b", "matches"), [
    ("The Killers", "The Weeknd", False),
    ("Earth and Fire", "Florence and Machine", False),
    ("Alpha feat Beta", "Gamma feat Delta", False),
    ("the", "The Weeknd", False),
    ("", "Drake", False),
    ("Drake", "Drake, Future", True),
    ("Weeknd", "The Weeknd", True),
    ("Ellissa", "Elisa", True),
])
def test_artist_matching(app, a, b, matches):
    assert sys.modules["app"]._artists_match(a, b) is matches


@pytest.mark.parametrize("weak_artist", ["Weeknd", "The Weeknd, Drake", "Other"])
def test_strong_artist_beats_earlier_match(app, weak_artist):
    module = sys.modules["app"]
    weak = {"title": "Lights", "artist": weak_artist, "duration_sec": 1}
    exact = {"title": "Lights", "artist": "The Weeknd"}
    seed = {"title": "Lights", "artist": "The Weeknd", "duration_ms": 1000}
    assert module._match_file_for_seed(seed, [weak, exact]) is exact
    assert module._match_file_for_seed(seed, [weak]) is weak
    assert module._match_file_for_seed(seed, []) is None
    assert module._match_file_for_seed({"title": "Other"}, [exact]) is None
