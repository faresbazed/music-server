import base64
import shutil
from pathlib import Path

import pytest
from mutagen.mp4 import MP4, MP4Cover


# A 1×1 PNG and 100 ms of silent AAC, both generated for this test.
COVER = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
)
SILENCE = Path(__file__).parent / "fixtures" / "silence.m4a"


@pytest.mark.parametrize("with_cover", [True, False], ids=["embedded-png", "no-cover"])
def test_m4a_upload_artwork(client, auth_headers, tmp_path, with_cover):
    track_path = tmp_path / "track.m4a"
    shutil.copyfile(SILENCE, track_path)
    audio = MP4(track_path)
    audio["\xa9alb"] = ["Test Album"]
    if with_cover:
        audio["covr"] = [MP4Cover(COVER, imageformat=MP4Cover.FORMAT_PNG)]
    audio.save()

    with track_path.open("rb") as track_file:
        response = client.post(
            "/upload?format=json",
            headers=auth_headers,
            data={"files": (track_file, track_path.name)},
        )
    assert response.status_code == 200
    assert response.get_json()["saved"] == 1

    catalog = client.get("/api/catalog", headers=auth_headers)
    assert catalog.status_code == 200
    track, = catalog.get_json()["library"]
    assert track["has_artwork"] is with_cover

    artwork = client.get(f"/api/artwork/{track['id']}", headers=auth_headers)
    if with_cover:
        assert artwork.status_code == 200
        assert artwork.mimetype == "image/png"
        assert artwork.data == COVER
    else:
        assert artwork.status_code == 404
