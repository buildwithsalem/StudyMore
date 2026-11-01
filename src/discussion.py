"""Private, membership-gated discussion backend (server-rendered pages)."""
from flask import Blueprint, render_template, redirect, request, session, url_for
from db import get_db_connection
from invitations import create_notification

discussion_bp = Blueprint("discussion", __name__)
MAX_CONTENT_LENGTH = 5000


def ensure_discussion_schema(conn):
    """Idempotent additive setup; the caller owns the transaction."""
    conn.execute("""CREATE TABLE IF NOT EXISTS discussion_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL REFERENCES study_groups(id),
        author_id INTEGER NOT NULL REFERENCES users(id),
        content TEXT NOT NULL CHECK(length(trim(content)) BETWEEN 1 AND 5000),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS discussion_replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL REFERENCES discussion_posts(id),
        author_id INTEGER NOT NULL REFERENCES users(id),
        content TEXT NOT NULL CHECK(length(trim(content)) BETWEEN 1 AND 5000),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS discussion_posts_group_order ON discussion_posts(group_id, created_at, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS discussion_replies_post_order ON discussion_replies(post_id, created_at, id)")


@discussion_bp.before_request
def require_login():
    if session.get("user_id") is None:
        return redirect(url_for("login"))


def _access(conn, group_id):
    if conn.execute("SELECT id FROM study_groups WHERE id = ?", (group_id,)).fetchone() is None:
        return _error("Study group not found", 404)
    if conn.execute(
        "SELECT 1 FROM group_memberships m JOIN users u ON u.id = m.user_id "
        "WHERE m.group_id = ? AND m.user_id = ?", (group_id, session["user_id"])
    ).fetchone() is None:
        return _error("Group membership required", 403)


def _content():
    content = request.form.get("content", "").strip()
    if not content or len(content) > MAX_CONTENT_LENGTH:
        return None
    return content


@discussion_bp.route("/group/<int:group_id>/discussions", methods=["GET", "POST"])
def posts(group_id):
    conn = _connection()
    error = _access(conn, group_id)
    if error is not None:
        return error
    validation_error = None
    if request.method == "POST":
        content = _content()
        if content is None:
            validation_error = "Content must contain 1–5000 characters."
        else:
            cursor = conn.execute(
                "INSERT INTO discussion_posts (group_id, author_id, content) VALUES (?, ?, ?)",
                (group_id, session["user_id"], content),
            )
            conn.commit()
            return redirect(url_for("discussion.post", group_id=group_id, post_id=cursor.lastrowid))
    rows = conn.execute(
        "SELECT p.*, u.name AS author_name, "
        "(SELECT COUNT(*) FROM discussion_replies r WHERE r.post_id=p.id) AS reply_count "
        "FROM discussion_posts p JOIN users u ON u.id=p.author_id "
        "WHERE p.group_id = ? ORDER BY p.created_at DESC, p.id DESC",
        (group_id,),
    ).fetchall()
    return _page(conn, group_id, "discussion_board.html", posts=rows, error=validation_error)


@discussion_bp.route("/group/<int:group_id>/discussions/<int:post_id>")
def post(group_id, post_id):
    return _post(group_id, post_id)


@discussion_bp.route("/group/<int:group_id>/discussions/<int:post_id>/replies", methods=["POST"])
def reply(group_id, post_id):
    return _post(group_id, post_id)


def _post(group_id, post_id):
    conn = _connection()
    error = _access(conn, group_id)
    if error is not None:
        return error
    row = conn.execute(
        "SELECT p.*, u.name AS author_name FROM discussion_posts p "
        "JOIN users u ON u.id=p.author_id WHERE p.id = ? AND p.group_id = ?", (post_id, group_id)
    ).fetchone()
    if row is None:
        return _error("Discussion post not found", 404)
    validation_error = None
    if request.method == "POST":
        content = _content()
        if content is None:
            validation_error = "Content must contain 1–5000 characters."
        else:
            # The connection context commits both inserts, or rolls both back.
            with conn:
                cursor = conn.execute(
                    "INSERT INTO discussion_replies (post_id, author_id, content) VALUES (?, ?, ?)",
                    (post_id, session["user_id"], content),
                )
                if row["author_id"] != session["user_id"]:
                    author = conn.execute("SELECT name FROM users WHERE id = ?",
                                          (session["user_id"],)).fetchone()
                    group = conn.execute("SELECT group_name FROM study_groups WHERE id = ?",
                                         (group_id,)).fetchone()
                    create_notification(
                        conn, row["author_id"], "discussion_reply",
                        f"{author['name']} replied to your discussion in {group['group_name']}.",
                        link_url=url_for("discussion.post", group_id=group_id, post_id=post_id),
                        group_id=group_id, related_id=cursor.lastrowid,
                    )
            return redirect(url_for("discussion.post", group_id=group_id, post_id=post_id))
    replies = conn.execute(
        "SELECT r.*, u.name AS author_name FROM discussion_replies r "
        "JOIN users u ON u.id=r.author_id WHERE r.post_id = ? ORDER BY r.created_at ASC, r.id ASC",
        (post_id,),
    ).fetchall()
    return _page(conn, group_id, "discussion_post.html", post=row, replies=replies, error=validation_error)


def _connection():
    # Enforce references on this feature's connections without changing legacy
    # write behavior in other blueprints. Must run before any transaction.
    conn = get_db_connection()
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _error(message, status):
    return render_template("discussion_error.html", error=message), status


def _page(conn, group_id, template, **context):
    group = conn.execute("SELECT * FROM study_groups WHERE id = ?", (group_id,)).fetchone()
    return render_template(
        template, group=group, content=request.form.get("content", ""),
        content_limit=MAX_CONTENT_LENGTH, **context
    ), 400 if context.get("error") else 200
