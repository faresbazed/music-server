import io
import wave
import zipfile

import pytest


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 80)
    return output.getvalue()


@pytest.mark.parametrize("zipped", [False, True], ids=["direct", "zip"])
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("سلام.wav", "سلام.wav"),
        ("folder/سلام.wav", "سلام.wav"),
        ("../../evil.wav", "evil.wav"),
        ("..\\evil.wav", "evil.wav"),
        (".hidden.wav", "hidden.wav"),
        (" .. hidden.wav", "hidden.wav"),
        ("cafe\u0301.wav", "café.wav"),
        ("", None),
        ("...", None),
        (".wav", None),
        ("   ", None),
    ],
)
def test_upload_filenames(client, data_dir, auth_headers, zipped, filename, expected):
    audio = wav_bytes()
    if zipped:
        if not filename:
            pytest.skip("ZIP entries cannot have empty names")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(filename, audio)
        archive.seek(0)
        upload = (archive, "music.zip")
    else:
        upload = (io.BytesIO(audio), filename)

    response = client.post(
        "/upload?format=json", headers=auth_headers, data={"files": upload}
    )

    assert response.status_code == 200
    assert response.get_json()["saved"] == int(expected is not None)
    music_dir = data_dir / "music"
    assert sorted(path.name for path in music_dir.iterdir()) == (
        [expected] if expected else []
    )
    if expected:
        assert (music_dir / expected).read_bytes() == audio
    assert sorted(path.relative_to(data_dir).as_posix() for path in data_dir.rglob("*.wav")) == (
        [f"music/{expected}"] if expected else []
    )


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("../..\\سلام.wav", "سلام.wav"),
        (" \t..\u2003سلام.wav", "سلام.wav"),
        ("\x00sa\x01la\x7fm.wav", "salam.wav"),
        ("\x00\x01 .\t", ""),
        ("folder/", ""),
        ("cafe\u0301.wav", "café.wav"),
    ],
)
def test_upload_filename_sanitizer(app, filename, expected):
    assert app.view_functions["upload_files"].__globals__["_upload_filename"](filename) == expected


def test_invalid_zip(client, data_dir, auth_headers):
    response = client.post(
        "/upload?format=json",
        headers=auth_headers,
        data={"files": (io.BytesIO(b"not a zip"), "music.zip")},
    )
    assert response.status_code == 200
    assert response.get_json()["saved"] == 0
    assert list((data_dir / "music").iterdir()) == []
