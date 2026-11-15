"""Deployment safeguards, without touching application databases or mail."""
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from flask import Flask, Response, session
import pytest

import avatar_upload
import password_reset
import production_config
import verification

ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET = "60e47c7d86b704a6920fc194ded00096fefa92ccfdbdbfccf8f6faea24c4ff15"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("VERCEL", "VERCEL_ENV", "STUDYMORE_ENV", "FLASK_SECRET_KEY",
                 "FLASK_DEBUG", "STUDYMORE_SHOW_CODES", "STUDYMORE_SMTP_HOST", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("name,value", [
    ("VERCEL", "1"), ("VERCEL_ENV", "preview"),
    ("VERCEL_ENV", "production"), ("STUDYMORE_ENV", "production"),
])
def test_deployed_settings_and_code_safety(monkeypatch, capsys, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setenv("FLASK_DEBUG", "1")
    monkeypatch.setenv("STUDYMORE_SHOW_CODES", "1")
    app = Flask(__name__)
    with pytest.raises(RuntimeError, match="FLASK_SECRET_KEY"):
        production_config.configure_app(app)
    monkeypatch.setenv("FLASK_SECRET_KEY", TEST_SECRET)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        production_config.configure_app(app)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@localhost/studymore_test_unused?sslmode=verify-full")
    production_config.configure_app(app)
    assert not app.debug
    assert app.config["SESSION_COOKIE_SECURE"]
    assert app.config["SESSION_COOKIE_HTTPONLY"]
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    with app.test_request_context("/verify"):
        session["demo_code"] = {"code": "654321", "purpose": "verification"}
        page = Response("<body>Verify</body>", mimetype="text/html")
        assert b"654321" not in verification.show_demo_code_banner(page).data
        assert "demo_code" not in session
        assert not verification.send_code_email("test@example.invalid", "654321")
        assert not password_reset.send_reset_email("test@example.invalid", "654321")
        verification.remember_demo_code("654321", "verification")
        assert "demo_code" not in session
    assert "654321" not in capsys.readouterr().out


def test_cloud_avatar_does_not_write(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    app = Flask(__name__, static_folder=str(tmp_path))
    with app.app_context():
        path, error = avatar_upload.save_avatar(io.BytesIO(b"\x89PNG\r\n\x1a\n"), 1)
        assert path is None and "persistent storage" in error
        with patch.object(avatar_upload.os, "remove") as remove:
            avatar_upload.delete_avatar_file("uploads/avatars/example.png")
            remove.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_entrypoint_without_database_or_working_directory_dependency(tmp_path):
    env = os.environ.copy()
    env.update(VERCEL="1", FLASK_SECRET_KEY=TEST_SECRET,
               DATABASE_URL="postgresql://unused@localhost/studymore_test_unused?sslmode=verify-full",
               FLASK_DEBUG="1", STUDYMORE_SHOW_CODES="1", PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, "-c", """
import sqlite3
from unittest.mock import patch
with patch.object(sqlite3, 'connect', side_effect=AssertionError('Database accessed')):
    from index import app
    assert not app.debug
    client = app.test_client()
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.json == {'status': 'ok'}
    assert response.headers['Cache-Control'] == 'no-store'
    assert client.get('/login').status_code == 200
    assert client.post('/create-group').status_code == 302
    assert client.get('/static/style.css').status_code == 200
    assert len(app.blueprints) == 13
    for template in app.jinja_env.list_templates():
        app.jinja_env.get_template(template)
"""], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("secret", ["short", "a" * 64, "change-me-to-a-longer-secret-key-before-deploying"])
def test_production_rejects_weak_secret(monkeypatch, secret):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("FLASK_SECRET_KEY", secret)
    with pytest.raises(RuntimeError, match="FLASK_SECRET_KEY") as caught:
        production_config.configure_app(Flask(__name__))
    assert secret not in str(caught.value)


def test_static_build_excludes_user_uploads(tmp_path):
    spec = importlib.util.spec_from_file_location("static_build", ROOT / "scripts/build_vercel_static.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    source = tmp_path / "src/static"
    (source / "uploads/avatars").mkdir(parents=True)
    (source / "style.css").write_text("body {}")
    (source / "uploads/avatars/private.png").write_bytes(b"private")
    module.build()
    assert (tmp_path / "public/static/style.css").read_text() == "body {}"
    assert not (tmp_path / "public/static/uploads").exists()
