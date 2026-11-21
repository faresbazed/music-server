import pytest

NON_ASCII = "سلام"


def test_non_ascii_header_is_rejected_not_crashing(client):
    response = client.get("/api/catalog", headers={"X-Library-Token": NON_ASCII})
    assert response.status_code == 401


def test_valid_query_token_wins_over_non_ascii_header(client, library_token):
    response = client.get(
        "/api/catalog",
        query_string={"token": library_token},
        headers={"X-Library-Token": NON_ASCII},
    )
    assert response.status_code == 200


@pytest.mark.parametrize("path, heading", [("/upload", "Upload music"), ("/import", "Import a playlist")])
def test_pages_accept_valid_token_beside_non_ascii_header(client, library_token, path, heading):
    response = client.get(
        path,
        query_string={"token": library_token},
        headers={"X-Library-Token": NON_ASCII},
    )
    assert response.status_code == 200
    assert heading in response.get_data(as_text=True)
    assert "library_token=" in response.headers.get("Set-Cookie", "")


@pytest.mark.parametrize("path", ["/upload", "/import"])
def test_pages_show_token_form_for_non_ascii_token(client, path):
    response = client.get(path, query_string={"token": NON_ASCII})
    assert response.status_code == 200
    assert "Music Library - token" in response.get_data(as_text=True)
    assert "Set-Cookie" not in response.headers


def test_non_ascii_configured_token(monkeypatch, app_factory):
    monkeypatch.setenv("LIBRARY_TOKEN", NON_ASCII)
    client = app_factory().test_client()
    assert client.get("/api/catalog", query_string={"token": NON_ASCII}).status_code == 200
    assert client.get("/api/catalog", query_string={"token": "wrong"}).status_code == 401
