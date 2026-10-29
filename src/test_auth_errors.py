"""Login and register show a friendly error on the page (not a blank text page)."""

from app import app, init_db


def test_wrong_password_shows_error_on_login_page(monkeypatch, tmp_path):
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "DATABASE", str(tmp_path / "auth.db"))
    init_db()
    client = app.test_client()
    resp = client.post("/login", data={"email": "nobody@example.com", "password": "nope"})
    assert resp.status_code == 400
    assert b"Invalid email or password" in resp.data
    assert b'role="alert"' in resp.data and b"<form" in resp.data
    assert b"nobody@example.com" in resp.data  # what they typed is kept


def test_login_page_links_to_forgot_password(monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", True)
    resp = app.test_client().get("/login")
    assert resp.status_code == 200 and b"/forgot-password" in resp.data


def test_register_mismatch_shows_error_on_register_page(monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", True)
    resp = app.test_client().post(
        "/register",
        data={"name": "A", "email": "a@mavs.uta.edu", "password": "x1", "confirm_password": "x2"},
    )
    assert resp.status_code == 400
    assert b"Password and confirmation do not match" in resp.data
    assert b'role="alert"' in resp.data and b"<form" in resp.data