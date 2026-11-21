import io
import wave
import zipfile

import pytest


@pytest.fixture
def wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 800)
    return buffer.getvalue()


def upload_zip(client, auth_headers, entries, saved, tracks):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    buffer.seek(0)
    response = client.post(
        "/upload?format=json", headers=auth_headers, data={"files": (buffer, "music.zip")}
    )
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "saved": saved, "tracks": tracks}


@pytest.fixture
def lyrics_for(client, auth_headers, monkeypatch):
    import app as music_server

    monkeypatch.setattr(music_server, "lrclib_lookup", lambda _: {"source": "none"})

    def get_lyrics(filename):
        tid = music_server.track_id_for_rel(filename)
        response = client.get(f"/api/lyrics/{tid}", headers=auth_headers)
        assert response.status_code == 200
        return response.get_json()

    return get_lyrics


@pytest.mark.parametrize(
    ("audio_ext", "lyrics_ext", "lyrics_first"),
    [(".wav", ".lrc", False), (".wav", ".lrc", True), (".WAV", ".TXT", True)],
    ids=["lyrics-last", "lyrics-first", "uppercase-extensions"],
)
def test_zip_keeps_lyrics_with_their_album(
    client, auth_headers, wav, lyrics_for, audio_ext, lyrics_ext, lyrics_first
):
    synced = f"[00:00.00]Album B lyrics\n" if lyrics_ext == ".lrc" else None
    sidecar = (f"AlbumB/Repeat{lyrics_ext}", synced or "Album B lyrics")
    entries = [("AlbumA/Repeat.wav", wav), (f"AlbumB/Repeat{audio_ext}", wav)]
    entries.insert(0 if lyrics_first else len(entries), sidecar)

    upload_zip(client, auth_headers, entries, saved=2, tracks=2)

    assert lyrics_for("Repeat.wav") == {"plain": None, "synced": None, "source": "none"}
    assert lyrics_for(f"Repeat-2{audio_ext}") == {
        "plain": "Album B lyrics",
        "synced": synced,
        "source": "sidecar",
    }


def test_zip_does_not_overwrite_lyrics_on_repeat_upload(client, auth_headers, wav, lyrics_for):
    for count, text in enumerate(("Original lyrics", "New lyrics"), start=1):
        upload_zip(
            client, auth_headers, [("Repeat.wav", wav), ("Repeat.txt", text)],
            saved=1, tracks=count,
        )

    assert lyrics_for("Repeat.wav")["plain"] == "Original lyrics"
    assert lyrics_for("Repeat-2.wav")["plain"] == "New lyrics"


def test_zip_skips_sidecars_without_audio_in_the_same_folder(
    client, auth_headers, wav, lyrics_for
):
    upload_zip(
        client, auth_headers, [("AlbumA/Repeat.wav", wav), ("AlbumB/Repeat.txt", "Orphan")],
        saved=1, tracks=1,
    )

    assert lyrics_for("Repeat.wav")["source"] == "none"
