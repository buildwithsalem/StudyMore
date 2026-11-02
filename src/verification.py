"""
UTA email verification.

A new account can't log in until the student enters a 6-digit code that was
sent to their @mavs.uta.edu address. That proves they control a UTA mailbox.
(It does not prove enrollment. That would need UTA single sign-on.)

Sending the code:
  - Demo mode (default): the code is printed in the terminal running the app.
  - Real email: set STUDYMORE_SMTP_HOST (and usually STUDYMORE_SMTP_PORT,
    STUDYMORE_SMTP_USER, STUDYMORE_SMTP_PASSWORD, STUDYMORE_MAIL_FROM).

Accounts that existed before this feature are marked verified.
"""

import os
import re
import secrets
import smtplib
import sqlite3
import time
from email.message import EmailMessage

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db_connection

verification_bp = Blueprint("verification", __name__, template_folder="templates")

UTA_EMAIL_RE = re.compile(r"^[^@\s]+@mavs\.uta\.edu$")
CODE_LENGTH = 6
CODE_LIFETIME_SECONDS = 10 * 60
RESEND_COOLDOWN_SECONDS = 60
MAX_ATTEMPTS = 5


def is_uta_email(email):
    return bool(UTA_EMAIL_RE.match(email or ""))


def ensure_verification_schema(conn):
    """Idempotent. Adds the verified flag and the codes table when missing."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if columns and "email_verified" not in columns:
        # DEFAULT 1 so everyone who signed up before this feature stays verified.
        conn.execute(
            "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_verifications (
            user_id INTEGER PRIMARY KEY,
            code_hash TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_sent_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )


def mask_email(email):
    local, _, domain = (email or "").partition("@")
    if len(local) <= 2:
        return f"{local[:1]}***@{domain}"
    return f"{local[:2]}***@{domain}"


def remember_demo_code(code, purpose):
    """Beta testing only: keep the code in the visitor's own session so a banner can show it."""
    session["demo_code"] = {"code": code, "purpose": purpose}


@verification_bp.after_app_request
def show_demo_code_banner(response):
    info = session.get("demo_code")
    if not info or not (request.endpoint or "").startswith(("verification.", "reset.")):
        return response
    if response.mimetype != "text/html" or response.direct_passthrough:
        return response
    html = response.get_data(as_text=True)
    match = re.search(r"<body[^>]*>", html)
    if not match:
        return response
    banner = (
        '<div style="background:#fff3cd;color:#222;padding:12px 16px;text-align:center;'
        'font:600 15px system-ui,sans-serif;border-bottom:2px solid #e0a800">'
        "Beta test mode: no email is sent. Your %s code is "
        '<span style="font-size:20px;letter-spacing:2px">%s</span></div>'
        % (info["purpose"], info["code"])
    )
    response.set_data(html[: match.end()] + banner + html[match.end():])
    return response


def send_code_email(email, code):
    """Returns True if sent (or printed in demo mode), False if sending failed."""
    host = os.environ.get("STUDYMORE_SMTP_HOST")
    if not host:
        print(f"[StudyMore DEMO] Verification code for {email}: {code}", flush=True)
        if os.environ.get("STUDYMORE_SHOW_CODES") == "1":
            remember_demo_code(code, "verification")
        return True

    message = EmailMessage()
    message["Subject"] = "Your StudyMore verification code"
    message["From"] = os.environ.get("STUDYMORE_MAIL_FROM", "no-reply@studymore.local")
    message["To"] = email
    message.set_content(
        f"Your StudyMore verification code is {code}.\n"
        f"It expires in {CODE_LIFETIME_SECONDS // 60} minutes.\n"
        "If you didn't sign up, you can ignore this email."
    )
    try:
        port = int(os.environ.get("STUDYMORE_SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.starttls()
            user = os.environ.get("STUDYMORE_SMTP_USER")
            if user:
                smtp.login(user, os.environ.get("STUDYMORE_SMTP_PASSWORD", ""))
            smtp.send_message(message)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        current_app.logger.error("Could not send verification email: %s", exc)
        return False


def issue_code(conn, user_id, email):
    """Make a new code, store only its hash, and send it. Returns True if sent."""
    ensure_verification_schema(conn)
    code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO email_verifications (user_id, code_hash, expires_at, attempts, last_sent_at)
        VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            code_hash = excluded.code_hash,
            expires_at = excluded.expires_at,
            attempts = 0,
            last_sent_at = excluded.last_sent_at
        """,
        (user_id, generate_password_hash(code), now + CODE_LIFETIME_SECONDS, now),
    )
    conn.commit()
    return send_code_email(email, code)


def issue_code_if_due(conn, user_id, email):
    """Send a fresh code unless one went out in the last minute (stops email spam)."""
    ensure_verification_schema(conn)
    row = conn.execute(
        "SELECT last_sent_at FROM email_verifications WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is not None and int(time.time()) - row["last_sent_at"] < RESEND_COOLDOWN_SECONDS:
        return True
    return issue_code(conn, user_id, email)


def _pending_user(conn):
    user_id = session.get("pending_verify_user_id")
    if user_id is None:
        return None
    ensure_verification_schema(conn)
    return conn.execute(
        "SELECT id, name, email, email_verified FROM users WHERE id = ?", (user_id,)
    ).fetchone()


def _render(user, error=None, info=None, status=200):
    return (
        render_template(
            "verify.html",
            masked_email=mask_email(user["email"]),
            code_length=CODE_LENGTH,
            error=error,
            info=info,
        ),
        status,
    )


@verification_bp.route("/verify", methods=["GET", "POST"])
def verify():
    conn = get_db_connection()
    user = _pending_user(conn)
    if user is None:
        return redirect(url_for("login"))
    if user["email_verified"]:
        session.pop("pending_verify_user_id", None)
        return redirect(url_for("login"))

    if request.method == "GET":
        return _render(user, info=session.pop("verify_info", None))

    entered = "".join((request.form.get("code") or "").split())
    row = conn.execute(
        "SELECT * FROM email_verifications WHERE user_id = ?", (user["id"],)
    ).fetchone()

    if row is None or row["expires_at"] < int(time.time()):
        return _render(user, error="That code has expired. Request a new one below.", status=400)
    if row["attempts"] >= MAX_ATTEMPTS:
        return _render(user, error="Too many wrong tries. Request a new code below.", status=400)
    if not (entered.isdigit() and len(entered) == CODE_LENGTH) or not check_password_hash(
        row["code_hash"], entered
    ):
        conn.execute(
            "UPDATE email_verifications SET attempts = attempts + 1 WHERE user_id = ?",
            (user["id"],),
        )
        conn.commit()
        return _render(user, error="That code isn't right. Check it and try again.", status=400)

    conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user["id"],))
    conn.execute("DELETE FROM email_verifications WHERE user_id = ?", (user["id"],))
    conn.commit()

    session.pop("pending_verify_user_id", None)
    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    return redirect(url_for("dashboard.dashboard"))


@verification_bp.route("/verify/resend", methods=["POST"])
def resend():
    conn = get_db_connection()
    user = _pending_user(conn)
    if user is None or user["email_verified"]:
        return redirect(url_for("login"))

    row = conn.execute(
        "SELECT last_sent_at FROM email_verifications WHERE user_id = ?", (user["id"],)
    ).fetchone()
    if row is not None:
        wait = RESEND_COOLDOWN_SECONDS - (int(time.time()) - row["last_sent_at"])
        if wait > 0:
            return _render(user, error=f"Please wait {wait} seconds before asking for another code.", status=429)

    if issue_code(conn, user["id"], user["email"]):
        return _render(user, info="We sent a new code.")
    return _render(user, error="We couldn't send the email. Try again in a minute.", status=502)
