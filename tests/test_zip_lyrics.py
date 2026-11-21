import io
import wave
import zipfile

import pytest
from mutagen.id3 import TPE1
from mutagen.wave import WAVE


def make_wav(seconds):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * int(8000 * seconds))
    return buffer.getvalue()


@pytest.fixture
def wav():
    return make_wav(0.1)


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
    synced = "[00:00.00]Album B lyrics\n" if lyrics_ext == ".lrc" else None
    sidecar = (f"AlbumB/Repeat{lyrics_ext}", synced or "Album B lyrics")
    entries = [("AlbumA/Repeat.wav", wav), (f"AlbumB/Repeat{audio_ext}", wav)]
    entries.insert(0 if lyrics_first else len(entries), sidecar)

    upload_zip(client, auth_headers, entries, saved=2, tracks=2)

    assert lyrics_for("Repeat.wav") == {"plain": None, "synced": None, "source": "none"}
    assert lyrics_for(f"Repeat-2/Repeat{audio_ext}") == {
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
    assert lyrics_for("Repeat-2/Repeat.wav")["plain"] == "New lyrics"


def test_zip_skips_sidecars_without_audio_in_the_same_folder(
    client, auth_headers, wav, lyrics_for
):
    upload_zip(
        client, auth_headers, [("AlbumA/Repeat.wav", wav), ("AlbumB/Repeat.txt", "Orphan")],
        saved=1, tracks=1,
    )

    assert lyrics_for("Repeat.wav")["source"] == "none"


def test_zip_matches_unicode_paths_with_different_separators(
    client, auth_headers, wav, lyrics_for
):
    upload_zip(
        client, auth_headers,
        [("Álbum\\cafe\u0301.wav", wav), ("Álbum/cafe\u0301.txt", "Lyrics")],
        saved=1, tracks=1,
    )

    assert lyrics_for("café.wav")["plain"] == "Lyrics"


def test_zip_keeps_unicode_equivalent_folders_separate(
    client, auth_headers, wav, lyrics_for
):
    upload_zip(
        client, auth_headers,
        [
            ("Álbum/Repeat.wav", wav),
            ("A\u0301lbum/Repeat.wav", wav),
            ("Álbum/Repeat.txt", "First album"),
            ("A\u0301lbum/Repeat.txt", "Second album"),
        ],
        saved=2, tracks=2,
    )

    assert lyrics_for("Repeat.wav")["plain"] == "First album"
    assert lyrics_for("Repeat-2/Repeat.wav")["plain"] == "Second album"


@pytest.mark.parametrize(
    ("fallback_ext", "exact_lyrics"),
    [(".txt", None), (".txt", "Exact lyrics"), (".lrc", "Exact lyrics")],
)
def test_zip_matches_unambiguous_unicode_stem(
    client, auth_headers, wav, lyrics_for, fallback_ext, exact_lyrics
):
    fallback = "[00:00.00]Fallback lyrics" if fallback_ext == ".lrc" else "Fallback lyrics"
    entries = [("Album/cafe\u0301.wav", wav), (f"Album/café{fallback_ext}", fallback)]
    if exact_lyrics:
        entries.append(("Album/cafe\u0301.txt", exact_lyrics))
    upload_zip(client, auth_headers, entries, saved=1, tracks=1)

    assert lyrics_for("café.wav")["plain"] == (exact_lyrics or "Fallback lyrics")


def test_zip_does_not_guess_among_unicode_stem_matches(
    client, auth_headers, wav, lyrics_for
):
    upload_zip(
        client, auth_headers,
        [("Album/ǻ.wav", wav), ("Album/å\u0301.wav", wav), ("Album/a\u030a\u0301.txt", "Ambiguous")],
        saved=2, tracks=2,
    )

    assert lyrics_for("ǻ.wav")["source"] == "none"
    assert lyrics_for("ǻ-2/ǻ.wav")["source"] == "none"


def test_zip_does_not_guess_among_unicode_sidecar_matches(
    client, auth_headers, wav, lyrics_for
):
    upload_zip(
        client, auth_headers,
        [("Album/ǻ.wav", wav), ("Album/å\u0301.txt", "First"), ("Album/a\u030a\u0301.txt", "Second")],
        saved=1, tracks=1,
    )

    assert lyrics_for("ǻ.wav")["source"] == "none"


def test_zip_does_not_reassign_lyrics_from_skipped_audio(
    client, auth_headers, wav, lyrics_for, monkeypatch
):
    import app as music_server

    monkeypatch.setattr(music_server, "_MAX_ZIP_FILE", len(wav))
    upload_zip(
        client, auth_headers,
        [
            ("Album/cafe\u0301.wav", wav + b"\0"),
            ("Album/café.wav", wav),
            ("Album/cafe\u0301.txt", "Skipped track lyrics"),
        ],
        saved=1, tracks=1,
    )

    assert lyrics_for("café.wav")["source"] == "none"


def test_zip_keeps_filename_title_for_playlist_matching(client, auth_headers, wav):
    upload_zip(
        client, auth_headers,
        [("AlbumA/Repeat.wav", wav), ("AlbumB/Repeat.WAV", make_wav(5))],
        saved=2, tracks=2,
    )

    response = client.post(
        "/api/import/json", headers=auth_headers,
        json={"name": "Album B", "tracks": [{"title": "Repeat", "duration_ms": 5000}]},
    )
    assert response.status_code == 200
    assert response.get_json()["report"]["matched"] == 1


def test_zip_playlist_prefers_tagged_artist_over_duration(client, auth_headers, wav):
    buffer = io.BytesIO(wav)
    tagged = WAVE(buffer)
    tagged.add_tags()
    tagged.tags.add(TPE1(encoding=3, text=["Original Artist"]))
    tagged.save(buffer)
    original = buffer.getvalue()
    upload_zip(
        client, auth_headers, [("AlbumA/Repeat.wav", original), ("AlbumB/Repeat.wav", wav)],
        saved=2, tracks=2,
    )

    response = client.post(
        "/api/import/json", headers=auth_headers,
        json={"name": "Original", "tracks": [
            {"title": "Repeat", "artist": "Original Artist", "duration_ms": 100},
        ]},
    )
    assert response.status_code == 200
    track, = response.get_json()["report"]["matched_tracks"]
    download = client.get(f"/api/download/{track['id']}", headers=auth_headers)
    assert download.data == original
