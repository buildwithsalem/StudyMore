"""Forgot password: email a 6-digit code, then let the student choose a new password.

Works like sign-up verification (verification.py):
  - Demo mode (default): the code is printed in the terminal running the app.
  - Real email: set STUDYMORE_SMTP_HOST (plus the same optional settings).

Safety rules:
  - The pages never say whether an email has an account.
  - Only a hash of the code is stored; it expires after 10 minutes.
  - 5 wrong tries lock the code; a code works once.
  - A new code can be sent at most once a minute.
"""

import os
import secrets
import smtplib
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
from verification import (
    CODE_LENGTH,
    CODE_LIFETIME_SECONDS,
    MAX_ATTEMPTS,
    RESEND_COOLDOWN_SECONDS,
    ensure_verification_schema,
    mask_email,
)

reset_bp = Blueprint("reset", __name__, template_folder="templates")

MIN_PASSWORD = 8
SENT_NOTICE = (
    "If that email has a StudyMore account, we sent a code to it. "
    "You can ask for another one after a minute."
)
BAD_CODE = "That code isn't right or has expired. Check it, or ask for a new one below."


def ensure_reset_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
            user_id INTEGER PRIMARY KEY,
            code_hash TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_sent_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )


def send_reset_email(email, code):
    """Returns True if sent (or printed in demo mode), False if sending failed."""
    host = os.environ.get("STUDYMORE_SMTP_HOST")
    if not host:
        print(f"[StudyMore DEMO] Password reset code for {email}: {code}", flush=True)
        return True

    message = EmailMessage()
    message["Subject"] = "Your StudyMore password reset code"
    message["From"] = os.environ.get("STUDYMORE_MAIL_FROM", "no-reply@studymore.local")
    message["To"] = email
    message.set_content(
        f"Your StudyMore password reset code is {code}.\n"
        f"It expires in {CODE_LIFETIME_SECONDS // 60} minutes.\n"
        "If you didn't ask to reset your password, you can ignore this email."
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
        current_app.logger.error("Could not send password reset email: %s", exc)
        return False


def issue_reset_code(conn, user_id, email):
    code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO password_resets (user_id, code_hash, expires_at, attempts, last_sent_at)
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
    return send_reset_email(email, code)


def _send_if_due(conn, email):
    """Send a code if the email has an account and none went out in the last minute."""
    user = conn.execute("SELECT id, email FROM users WHERE email = ?", (email,)).fetchone()
    if user is None:
        return
    row = conn.execute(
        "SELECT last_sent_at FROM password_resets WHERE user_id = ?", (user["id"],)
    ).fetchone()
    if row is not None and int(time.time()) - row["last_sent_at"] < RESEND_COOLDOWN_SECONDS:
        return
    issue_reset_code(conn, user["id"], user["email"])


def _render_forgot(error=None, email="", status=200):
    return render_template("forgot_password.html", error=error, email=email), status


def _render_reset(email, error=None, info=None, status=200):
    return (
        render_template(
            "reset_password.html",
            masked_email=mask_email(email),
            code_length=CODE_LENGTH,
            min_password=MIN_PASSWORD,
            error=error,
            info=info,
        ),
        status,
    )


@reset_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return _render_forgot()

    email = (request.form.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return _render_forgot("Please enter the email you signed up with.", email, 400)

    conn = get_db_connection()
    ensure_reset_schema(conn)
    _send_if_due(conn, email)

    # The same thing happens whether or not the email has an account.
    session["reset_email"] = email
    return redirect(url_for("reset.reset_password"))


@reset_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    email = session.get("reset_email")
    if not email:
        return redirect(url_for("reset.forgot_password"))

    if request.method == "GET":
        return _render_reset(email, info=SENT_NOTICE)

    code = "".join((request.form.get("code") or "").split())
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""

    if len(password) < MIN_PASSWORD:
        return _render_reset(email, error=f"Your new password must be at least {MIN_PASSWORD} characters.", status=400)
    if password != confirm:
        return _render_reset(email, error="The two passwords do not match.", status=400)

    conn = get_db_connection()
    ensure_reset_schema(conn)
    ensure_verification_schema(conn)
    user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    row = None
    if user is not None:
        row = conn.execute(
            "SELECT * FROM password_resets WHERE user_id = ?", (user["id"],)
        ).fetchone()

    if user is None or row is None or row["expires_at"] < int(time.time()):
        return _render_reset(email, error=BAD_CODE, status=400)
    if row["attempts"] >= MAX_ATTEMPTS:
        return _render_reset(email, error="Too many wrong tries. Ask for a new code below.", status=400)
    if not (code.isdigit() and len(code) == CODE_LENGTH) or not check_password_hash(row["code_hash"], code):
        conn.execute(
            "UPDATE password_resets SET attempts = attempts + 1 WHERE user_id = ?",
            (user["id"],),
        )
        conn.commit()
        return _render_reset(email, error=BAD_CODE, status=400)

    # Getting the code at their UTA mailbox also proves they control it, so an
    # unverified account becomes verified.
    conn.execute(
        "UPDATE users SET password = ?, email_verified = 1 WHERE id = ?",
        (generate_password_hash(password), user["id"]),
    )
    conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
    conn.commit()

    session.pop("reset_email", None)
    session.pop("pending_verify_user_id", None)
    session["login_info"] = "Your password was changed. Log in with your new password."
    return redirect(url_for("login"))


@reset_bp.route("/reset-password/resend", methods=["POST"])
def reset_resend():
    email = session.get("reset_email")
    if not email:
        return redirect(url_for("reset.forgot_password"))
    conn = get_db_connection()
    ensure_reset_schema(conn)
    _send_if_due(conn, email)
    return _render_reset(email, info=SENT_NOTICE)