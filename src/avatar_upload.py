
import os
import secrets
from pathlib import Path

from flask import current_app, url_for

AVATAR_DIR = "uploads/avatars"
MAX_AVATAR_BYTES = 3 * 1024 * 1024  # 3 MB


def ensure_avatar_column(conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(user_profiles)")}
    if "avatar_path" not in existing:
        conn.execute("ALTER TABLE user_profiles ADD COLUMN avatar_path TEXT")


def image_extension(data):
    """File extension for JPG/PNG/WebP bytes, or None for anything else."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def save_avatar(upload, user_id):
    """Store an uploaded photo. Returns (relative_path, None) or (None, error)."""
    data = upload.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        return None, "Profile photo must be 3 MB or smaller."
    extension = image_extension(data)
    if extension is None:
        return None, "Profile photo must be a JPG, PNG, or WebP image."
    folder = Path(current_app.static_folder) / AVATAR_DIR
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{user_id}-{secrets.token_hex(6)}{extension}"
    (folder / name).write_bytes(data)
    return f"{AVATAR_DIR}/{name}", None


def delete_avatar_file(path):
    """Remove a stored photo. Ignores anything outside the avatar folder."""
    if not path or not path.startswith(AVATAR_DIR + "/") or ".." in path:
        return
    try:
        os.remove(Path(current_app.static_folder) / path)
    except OSError:
        pass


def set_avatar_path(conn, user_id, path):
    conn.execute(
        "UPDATE user_profiles SET avatar_path = ? WHERE user_id = ?",
        (path or None, user_id),
    )
    conn.commit()


def avatar_url_for(path):
    return url_for("static", filename=path) if path else ""
