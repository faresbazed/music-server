import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture
def data_dir(tmp_path):
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def library_token():
    return "test-library-token"


@pytest.fixture
def auth_headers(library_token):
    return {"X-Library-Token": library_token}


@pytest.fixture
def app_factory(monkeypatch, data_dir, library_token):
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("MUSIC_DIR", str(data_dir / "music"))
    monkeypatch.setenv("LIBRARY_TOKEN", library_token)

    def create_app():
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        spec = importlib.util.spec_from_file_location("app", app_path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        module.app.config["TESTING"] = True
        return module.app

    return create_app


@pytest.fixture
def app(app_factory):
    return app_factory()


@pytest.fixture
def client(app):
    return app.test_client()
