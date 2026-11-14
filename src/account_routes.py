"""Delete-my-account page: password + typed confirmation, then full cleanup."""

from flask import Blueprint, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from account_deletion import _columns, _tables, delete_user_account
from student_profile import delete_avatar_file, get_connection

account_bp = Blueprint("account", __name__)


def _page(error=None, deleted=False, status=200):
    return render_template("account_delete.html", error=error, deleted=deleted), status


@account_bp.route("/account/delete", methods=["GET", "POST"])
def delete_account():
    user_id = session.get("user_id")
    if user_id is None:
        return redirect(url_for("login"))

    conn = get_connection()
    try:
        user = conn.execute(
            "SELECT id, email, password FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            session.clear()
            return redirect(url_for("login"))

        if request.method == "GET":
            return _page()

        if (request.form.get("confirm") or "").strip() != "DELETE":
            return _page("Type DELETE in capital letters to confirm.", status=400)
        if not check_password_hash(user[2], request.form.get("password") or ""):
            return _page("That password is not correct.", status=400)

        avatar = None
        try:
            row = conn.execute(
                "SELECT avatar_path FROM user_profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
            avatar = row[0] if row else None
        except Exception:
            avatar = None

        email = user[1]
        delete_user_account(conn, user_id)
        # Leftovers keyed by email (pending codes, etc.) so the address can sign up again.
        for table in _tables(conn):
            if table != "users" and "email" in _columns(conn, table):
                conn.execute(
                    f"DELETE FROM {table} WHERE LOWER(email) = LOWER(?)", (email,)
                )
        conn.commit()
    finally:
        conn.close()

    if avatar:
        delete_avatar_file(avatar)
    session.clear()
    return _page(deleted=True)