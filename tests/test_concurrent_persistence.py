import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def test_concurrent_imports_survive_reload(app, app_factory, auth_headers,
                                         monkeypatch):
    module = sys.modules[app.import_name]
    serialized = threading.Event()
    second_done = threading.Event()
    dumps = json.dumps

    def delayed_dumps(obj, *args, **kwargs):
        result = dumps(obj, *args, **kwargs)
        if (threading.current_thread().name.startswith("first-import")
                and isinstance(obj, dict) and "playlists" in obj):
            serialized.set()
            second_done.wait(2)
        return result

    def post(name):
        with app.test_client() as client:
            response = client.post("/api/import/text", headers=auth_headers,
                                   json={"name": name,
                                         "text": "Song | Artist"})
            assert response.status_code == 200

    monkeypatch.setattr(module.json, "dumps", delayed_dumps)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="first-import") as pool:
        first = pool.submit(post, "First")
        assert serialized.wait(5)
        with ThreadPoolExecutor(max_workers=1) as other:
            def second():
                try:
                    post("Second")
                finally:
                    second_done.set()

            other.submit(second).result(timeout=10)
        first.result(timeout=10)

    expected = {"First", "Second"}
    assert {p["name"] for p in module.IMPORTED_PLAYLISTS} == expected
    persisted = json.loads(module.IMPORTED_PATH.read_text())
    assert {p["name"] for p in persisted["playlists"]} == expected
    app_factory()
    assert {p["name"] for p in sys.modules["app"].IMPORTED_PLAYLISTS} == expected


@pytest.mark.parametrize("document", [{}, {"playlists": []}, {"name": "Café"}])
def test_json_save_uses_unique_temporary_files(app, monkeypatch, document):
    module = sys.modules[app.import_name]
    path = module.DATA_DIR / "store.json"
    temporary_paths = []
    replace = Path.replace

    def record_replace(source, target):
        temporary_paths.append(source)
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", record_replace)
    module._json_save(path, document)
    module._json_save(path, document)

    assert json.loads(path.read_text()) == document
    assert len(set(temporary_paths)) == 2
    assert all(p.parent == path.parent for p in temporary_paths)
    assert not list(path.parent.glob("*.tmp"))


def test_json_save_failure_preserves_previous_file(app, monkeypatch, capsys):
    module = sys.modules[app.import_name]
    path = module.DATA_DIR / "store.json"
    module._json_save(path, {"previous": True})

    def fail_replace(source, target):
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    module._json_save(path, {"new": True})

    assert json.loads(path.read_text()) == {"previous": True}
    assert not list(path.parent.glob("*.tmp"))
    assert "cache write failed" in capsys.readouterr().out
