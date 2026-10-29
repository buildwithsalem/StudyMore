"""Tests for the forgot-password flow."""

import sqlite3
import time

import pytest
from werkzeug.security import check_password_hash


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    import password_reset
    from app import app, create_test_user, init_db

    path = str(tmp_path / "test.db")
    # monkeypatch puts the old settings back after each test, so other test
    # files (like test_search.py) are not affected.
    monkeypatch.setitem(app.config, "DATABASE", path)
    monkeypatch.setitem(app.config, "TESTING", True)
    init_db(path)
    create_test_user(path)

    sent = []
    monkeypatch.setattr(password_reset, "send_reset_email", lambda email, code: sent.append((email, code)) or True)
    return app.test_client(), path, sent


def db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ask(client, email="test@example.com"):
    return client.post("/forgot-password", data={"email": email})


def reset(client, code, password="newpass123", confirm=None):
    return client.post(
        "/reset-password",
        data={"code": code, "password": password, "confirm_password": confirm or password},
    )


def test_forgot_page_loads(ctx):
    client, _, _ = ctx
    resp = client.get("/forgot-password")
    assert resp.status_code == 200 and b"Forgot your password" in resp.data


def test_known_email_gets_a_code_and_unknown_does_not(ctx):
    client, _, sent = ctx
    resp = ask(client)
    assert resp.status_code == 302 and "/reset-password" in resp.headers["Location"]
    assert len(sent) == 1 and sent[0][0] == "test@example.com" and len(sent[0][1]) == 6

    other = ask(client, "nobody@mavs.uta.edu")
    assert other.status_code == 302 and other.headers["Location"] == resp.headers["Location"]
    assert len(sent) == 1  # nothing sent, and the answer looks the same


def test_blank_email_is_an_error(ctx):
    client, _, _ = ctx
    resp = ask(client, "  ")
    assert resp.status_code == 400 and b'role="alert"' in resp.data


def test_reset_page_needs_the_first_step(ctx):
    client, _, _ = ctx
    resp = client.get("/reset-password")
    assert resp.status_code == 302 and "/forgot-password" in resp.headers["Location"]


def test_reset_page_never_reveals_account(ctx):
    client, _, _ = ctx
    ask(client, "nobody@mavs.uta.edu")
    resp = client.get("/reset-password")
    assert resp.status_code == 200 and b"If that email has a StudyMore account" in resp.data


def test_correct_code_changes_password(ctx):
    client, path, sent = ctx
    ask(client)
    resp = reset(client, sent[0][1])
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    conn = db(path)
    row = conn.execute("SELECT password FROM users WHERE email = 'test@example.com'").fetchone()
    assert check_password_hash(row["password"], "newpass123")
    assert not check_password_hash(row["password"], "test123")
    assert conn.execute("SELECT COUNT(*) FROM password_resets").fetchone()[0] == 0
    with client.session_transaction() as s:
        assert "reset_email" not in s and s["login_info"]


def test_code_works_only_once(ctx):
    client, _, sent = ctx
    ask(client)
    code = sent[0][1]
    reset(client, code)
    client.post("/forgot-password", data={"email": "test@example.com"})  # asks again, no new code (cooldown)
    again = reset(client, code, "another456")
    assert again.status_code == 400


def test_wrong_code_counts_and_locks_after_five(ctx):
    client, path, sent = ctx
    ask(client)
    good = sent[0][1]
    bad = "000000" if good != "000000" else "111111"
    for _ in range(5):
        assert reset(client, bad).status_code == 400
    assert db(path).execute("SELECT attempts FROM password_resets").fetchone()[0] == 5
    locked = reset(client, good)
    assert locked.status_code == 400 and b"Too many wrong tries" in locked.data


def test_expired_code_is_refused(ctx):
    client, path, sent = ctx
    ask(client)
    conn = db(path)
    conn.execute("UPDATE password_resets SET expires_at = ?", (int(time.time()) - 5,))
    conn.commit()
    assert reset(client, sent[0][1]).status_code == 400


def test_bad_new_password_does_not_use_up_the_code(ctx):
    client, path, sent = ctx
    ask(client)
    assert reset(client, sent[0][1], "short").status_code == 400
    assert reset(client, sent[0][1], "longenough1", "different99").status_code == 400
    assert db(path).execute("SELECT attempts FROM password_resets").fetchone()[0] == 0
    assert reset(client, sent[0][1]).status_code == 302


def test_resend_is_rate_limited(ctx):
    client, path, sent = ctx
    ask(client)
    resp = client.post("/reset-password/resend")
    assert resp.status_code == 200 and len(sent) == 1  # within a minute: no second email
    conn = db(path)
    conn.execute("UPDATE password_resets SET last_sent_at = ?", (int(time.time()) - 120,))
    conn.commit()
    client.post("/reset-password/resend")
    assert len(sent) == 2


def test_reset_marks_account_verified(ctx):
    from verification import ensure_verification_schema

    client, path, sent = ctx
    conn = db(path)
    ensure_verification_schema(conn)
    conn.execute("UPDATE users SET email_verified = 0")
    conn.commit()
    ask(client)
    reset(client, sent[0][1])
    assert db(path).execute("SELECT email_verified FROM users").fetchone()[0] == 1