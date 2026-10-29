"""Shared interface pieces: nav avatar data, campus directions page, and the
"who else is free" counts that make the availability grid useful."""
import sqlite3

from flask import Blueprint, jsonify, redirect, render_template, session, url_for

from avatar_upload import avatar_url_for
from student_profile import AVATAR_COLORS, DEFAULT_AVATAR
from db import get_db_connection

ui_bp = Blueprint("ui", __name__)

CAMPUS = "University of Texas at Arlington, Arlington, TX"

# Building names only. Map positions are looked up when someone asks for directions.
BUILDINGS = [
    "Central Library",
    "University Center",
    "Nedderman Hall",
    "Engineering Research Building",
    "Science Hall",
    "Life Science Building",
    "Chemistry and Physics Building",
    "Business Building",
    "College Park Center",
    "Maverick Activities Center",
    "Fine Arts Building",
    "Preston Hall",
    "Davis Hall",
    "Ransom Hall",
    "Trimble Hall",
    "Carlisle Hall",
    "University Hall",
    "Woolf Hall",
    "Hammond Hall",
    "Texas Hall",
    "Pickard Hall",
]


def initials_for(name):
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def current_ui_user(conn, user_id):
    """Name, initials and photo URL for the signed-in person, or None."""
    row = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    photo = ""
    color = AVATAR_COLORS[DEFAULT_AVATAR]
    try:
        prof = conn.execute(
            "SELECT avatar_path, avatar_color FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        if prof is not None:
            if prof["avatar_path"]:
                photo = avatar_url_for(prof["avatar_path"])
            color = AVATAR_COLORS.get(prof["avatar_color"], color)
    except sqlite3.Error:
        photo = ""
    first = (row["name"] or "").split()[0] if (row["name"] or "").split() else ""
    return {
        "name": row["name"], "first": first, "initials": initials_for(row["name"]),
        "photo": photo, "color": color,
    }


@ui_bp.app_context_processor
def inject_ui_user():
    user_id = session.get("user_id")
    if user_id is None:
        return {"ui_user": None, "ui_campus": CAMPUS}
    try:
        conn = get_db_connection()
        return {"ui_user": current_ui_user(conn, user_id), "ui_campus": CAMPUS}
    except sqlite3.Error:
        return {"ui_user": None, "ui_campus": CAMPUS}


def overlap_counts(conn, user_id):
    """How many of your group mates are free in each slot.

    Counts only, never names, so nobody's schedule is exposed.
    """
    rows = conn.execute(
        """
        SELECT ua.day_of_week, ua.start_time, ua.end_time,
               COUNT(DISTINCT ua.user_id) AS n
        FROM user_availability ua
        WHERE ua.user_id != ?
          AND ua.user_id IN (
              SELECT gm2.user_id
              FROM group_memberships gm1
              JOIN group_memberships gm2 ON gm2.group_id = gm1.group_id
              WHERE gm1.user_id = ?
          )
        GROUP BY ua.day_of_week, ua.start_time, ua.end_time
        """,
        (user_id, user_id),
    ).fetchall()
    return {f"{r['day_of_week']}|{r['start_time']}|{r['end_time']}": r["n"] for r in rows}


@ui_bp.route("/api/availability/overlap")
def availability_overlap():
    user_id = session.get("user_id")
    if user_id is None:
        return jsonify({"error": "login required"}), 401
    conn = get_db_connection()
    return jsonify({"counts": overlap_counts(conn, user_id)})


@ui_bp.route("/campus")
def campus():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("campus.html", buildings=BUILDINGS, campus=CAMPUS)
