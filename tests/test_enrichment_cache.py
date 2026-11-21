import json
import sys
from unittest.mock import Mock

import pytest
import requests


def response(data=None, status=200, content=b""):
    return Mock(status_code=status, json=Mock(return_value=data), content=content)


@pytest.fixture(params=["itunes", "lrclib", "artist"])
def lookup(request, app, client, auth_headers):
    module = sys.modules["app"]
    if request.param == "itunes":
        call = lambda: module.itunes_lookup("Artist", "Song")
        cached = lambda: json.loads(module.ENRICH_CACHE_PATH.read_text()) if module.ENRICH_CACHE_PATH.exists() else {}
        return call, cached, [response({"results": [{"collectionName": "Album"}]})], [response({"results": []})], None, {"album": "Album", "artwork_url": None}
    if request.param == "lrclib":
        call = lambda: module.lrclib_lookup({"id": "track", "artist": "Artist", "title": "Song"})
        cached = lambda: json.loads(module.LYRICS_CACHE_PATH.read_text()) if module.LYRICS_CACHE_PATH.exists() else {}
        empty = {"plain": None, "synced": None, "source": "none"}
        return call, cached, [response({"plainLyrics": "Lyrics"})], [response(status=404), response([])], empty, {"plain": "Lyrics", "synced": None, "source": "lrclib"}
    call = lambda: client.get("/api/artist-image?artist=Artist", headers=auth_headers).status_code
    cached = lambda: list(module.ARTIST_DIR.glob("*.none"))
    return call, cached, [response({"data": [{"picture_medium": "https://example.com/image.jpg"}]}), response(content=b"\xff\xd8\xffimage")], [response({"data": []})], 404, 200


@pytest.mark.parametrize("failure", [requests.Timeout("outage"), response(status=503)])
def test_failed_lookup_retries_without_persisting_negative(monkeypatch, lookup, failure):
    call, cached, success, _, empty, expected = lookup
    get = Mock(side_effect=[failure, *success])
    monkeypatch.setattr(requests, "get", get)

    assert call() == empty
    assert not cached()
    assert call() == expected
    assert get.call_count == 1 + len(success)


def test_successful_not_found_is_cached(monkeypatch, lookup):
    call, cached, _, missing, empty, _ = lookup
    get = Mock(side_effect=missing)
    monkeypatch.setattr(requests, "get", get)

    assert call() == empty
    assert cached()
    assert call() == empty
    assert get.call_count == len(missing)


@pytest.mark.parametrize("stage", ["lyrics_search", "image_download"])
@pytest.mark.parametrize("failure", [requests.Timeout("outage"), response(status=503)])
def test_secondary_request_failure_retries(app, client, auth_headers, monkeypatch, stage, failure):
    module = sys.modules["app"]
    if stage == "lyrics_search":
        call = lambda: module.lrclib_lookup({"id": "track"})
        first = response(status=404)
        success = [response({"plainLyrics": "Lyrics"})]
        empty = {"plain": None, "synced": None, "source": "none"}
        expected = {"plain": "Lyrics", "synced": None, "source": "lrclib"}
        cache_path = module.LYRICS_CACHE_PATH
    else:
        call = lambda: client.get("/api/artist-image?artist=Artist", headers=auth_headers).status_code
        first = response({"data": [{"picture": "https://example.com/image.jpg"}]})
        success = [first, response(content=b"\xff\xd8\xffimage")]
        empty, expected = 404, 200
        cache_path = next(iter(module.ARTIST_DIR.glob("*.none")), None)
    get = Mock(side_effect=[first, failure, *success])
    monkeypatch.setattr(requests, "get", get)

    assert call() == empty
    if cache_path:
        assert not cache_path.exists()
    assert not list(module.ARTIST_DIR.glob("*.none"))
    assert call() == expected
    assert get.call_count == 2 + len(success)
