"""Student profile page. Registered in app.py as a blueprint."""

import re
import sqlite3

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from db import get_db_connection

from avatar_upload import (
    ensure_avatar_column, save_avatar, delete_avatar_file,
    set_avatar_path, avatar_url_for,
)

profile_bp = Blueprint("profile", __name__, template_folder="templates")

DEFAULT_DATABASE = "study_more.db"

CLASS_YEARS = ("Freshman", "Sophomore", "Junior", "Senior", "Graduate")
STUDY_STYLES = (
    "Quiet focus",
    "Group discussion",
    "Practice problems",
    "Teaching each other",
    "Flashcards and quizzes",
    "A mix of everything",
)
MEETING_PREFERENCES = ("In-Person", "Online", "Hybrid", "No preference")
GROUP_SIZES = ("2 to 3 students", "4 to 5 students", "6 or more", "No preference")
STUDY_GOALS = ("Exam", "Assignment / Project", "Final Exam", "Weekly Study", "Other")

AVATAR_COLORS = {
    "purple": "#6c4ef5",
    "blue": "#2f7df6",
    "teal": "#0f8d74",
    "pink": "#e0486a",
    "orange": "#d9730d",
    "slate": "#4b5563",
}
DEFAULT_AVATAR = "purple"


MAX_NAME = 60
MAX_BIO = 300
MAX_SHORT = 80
MAX_LIST_ITEMS = 12
MAX_ITEM_LENGTH = 40
MAX_COURSE_LENGTH = 20


def get_connection():
    return get_db_connection()


def ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            bio TEXT,
            major TEXT,
            minor TEXT,
            class_year TEXT,
            grad_term TEXT,
            courses TEXT,
            study_style TEXT,
            meeting_pref TEXT,
            group_size TEXT,
            study_goals TEXT,
            can_help TEXT,
            need_help TEXT,
            interests TEXT,
            avatar_color TEXT DEFAULT 'purple',
            show_in_groups INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(user_profiles)")}
    if "avatar_color" not in existing:
        conn.execute("ALTER TABLE user_profiles ADD COLUMN avatar_color TEXT DEFAULT 'purple'")
    if "show_in_groups" not in existing:
        conn.execute("ALTER TABLE user_profiles ADD COLUMN show_in_groups INTEGER DEFAULT 1")
    ensure_avatar_column(conn)

def split_list(text):
    """'a, b ,, a' -> ['a', 'b'] (blanks dropped, repeats dropped, order kept)."""
    items = []
    seen = set()
    for part in (text or "").replace("\n", ",").split(","):
        item = " ".join(part.split())
        if item and item.lower() not in seen:
            seen.add(item.lower())
            items.append(item)
    return items


def normalize_course(value):
    """'cse3311' and 'CSE  3311' both become 'CSE 3311'."""
    text = " ".join(value.upper().split())
    match = re.fullmatch(r"([A-Z]{2,5}) ?(\d{3,4}[A-Z]?)", text)
    return f"{match.group(1)} {match.group(2)}" if match else text


def empty_values(name=""):
    return {
        "display_name": name,
        "bio": "",
        "major": "",
        "minor": "",
        "class_year": "",
        "grad_term": "",
        "courses": "",
        "study_style": "",
        "meeting_pref": "",
        "group_size": "",
        "study_goals": [],
        "can_help": "",
        "need_help": "",
        "interests": "",
        "avatar_color": DEFAULT_AVATAR,
        "avatar_path": "",
        "show_in_groups": True,
    }


def load_profile(conn, user):
    values = empty_values(user["name"])
    row = conn.execute(
        "SELECT * FROM user_profiles WHERE user_id = ?", (user["id"],)
    ).fetchone()
    if row is not None:
        for key in values:
            if key in row.keys() and key not in (
                "study_goals", "avatar_color", "show_in_groups"
            ):
                values[key] = row[key] or ""
        values["study_goals"] = split_list(row["study_goals"])
        if row["avatar_color"] in AVATAR_COLORS:
            values["avatar_color"] = row["avatar_color"]
        values["show_in_groups"] = row["show_in_groups"] != 0
    return values


def clean_form(form):
    """Validate the submitted form. Returns (values, errors)."""
    errors = []

    def text_of(key):
        return " ".join((form.get(key) or "").split())

    values = empty_values()
    values["display_name"] = text_of("display_name")
    values["bio"] = (form.get("bio") or "").strip()
    values["major"] = text_of("major")
    values["minor"] = text_of("minor")
    values["class_year"] = text_of("class_year")
    values["grad_term"] = text_of("grad_term")
    values["study_style"] = text_of("study_style")
    values["meeting_pref"] = text_of("meeting_pref")
    values["group_size"] = text_of("group_size")
    values["avatar_color"] = text_of("avatar_color") or DEFAULT_AVATAR
    values["show_in_groups"] = form.get("show_in_groups") == "on"

    if values["avatar_color"] not in AVATAR_COLORS:
        errors.append("Please choose a valid avatar color.")

    if not values["display_name"]:
        errors.append("Please enter a display name.")
    elif len(values["display_name"]) > MAX_NAME:
        errors.append(f"Display name must be {MAX_NAME} characters or fewer.")

    if len(values["bio"]) > MAX_BIO:
        errors.append(f"Bio must be {MAX_BIO} characters or fewer.")

    for key, label in (("major", "Major"), ("minor", "Minor"), ("grad_term", "Expected graduation")):
        if len(values[key]) > MAX_SHORT:
            errors.append(f"{label} must be {MAX_SHORT} characters or fewer.")

    for key, allowed, label in (
        ("class_year", CLASS_YEARS, "class year"),
        ("study_style", STUDY_STYLES, "study style"),
        ("meeting_pref", MEETING_PREFERENCES, "meeting preference"),
        ("group_size", GROUP_SIZES, "group size"),
    ):
        if values[key] and values[key] not in allowed:
            errors.append(f"Please choose a valid {label}.")

    goals = form.getlist("study_goals")
    if any(goal not in STUDY_GOALS for goal in goals):
        errors.append("Please choose valid study goals.")
    values["study_goals"] = [goal for goal in STUDY_GOALS if goal in goals]

    courses = [normalize_course(item) for item in split_list(form.get("courses"))]
    courses = split_list(", ".join(courses))
    if len(courses) > MAX_LIST_ITEMS:
        errors.append(f"Please list {MAX_LIST_ITEMS} courses or fewer.")
    if any(len(item) > MAX_COURSE_LENGTH for item in courses):
        errors.append(f"Each course must be {MAX_COURSE_LENGTH} characters or fewer.")
    values["courses"] = ", ".join(courses)

    for key, label in (
        ("can_help", "Subjects you can help with"),
        ("need_help", "Subjects you want help with"),
        ("interests", "Clubs and interests"),
    ):
        items = split_list(form.get(key))
        if len(items) > MAX_LIST_ITEMS:
            errors.append(f"{label}: please list {MAX_LIST_ITEMS} items or fewer.")
        if any(len(item) > MAX_ITEM_LENGTH for item in items):
            errors.append(f"{label}: each item must be {MAX_ITEM_LENGTH} characters or fewer.")
        values[key] = ", ".join(items)

    return values, errors


def save_profile(conn, user_id, values):
    conn.execute("UPDATE users SET name = ? WHERE id = ?", (values["display_name"], user_id))
    conn.execute(
        """
        INSERT INTO user_profiles
            (user_id, bio, major, minor, class_year, grad_term, courses,
             study_style, meeting_pref, group_size, study_goals,
             can_help, need_help, interests, avatar_color, show_in_groups)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            bio = excluded.bio,
            major = excluded.major,
            minor = excluded.minor,
            class_year = excluded.class_year,
            grad_term = excluded.grad_term,
            courses = excluded.courses,
            study_style = excluded.study_style,
            meeting_pref = excluded.meeting_pref,
            group_size = excluded.group_size,
            study_goals = excluded.study_goals,
            can_help = excluded.can_help,
            need_help = excluded.need_help,
            interests = excluded.interests,
            avatar_color = excluded.avatar_color,
            show_in_groups = excluded.show_in_groups
        """,
        (
            user_id,
            values["bio"],
            values["major"],
            values["minor"],
            values["class_year"],
            values["grad_term"],
            values["courses"],
            values["study_style"],
            values["meeting_pref"],
            values["group_size"],
            ", ".join(values["study_goals"]),
            values["can_help"],
            values["need_help"],
            values["interests"],
            values["avatar_color"],
            1 if values["show_in_groups"] else 0,
        ),
    )
    conn.commit()


def count_rows(conn, sql, params):
    try:
        return conn.execute(sql, params).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def initials_of(name):
    words = [word for word in (name or "").split() if word]
    if not words:
        return "?"
    return "".join(word[0] for word in words[:2]).upper()


def get_member_cards(conn, group_id, creator_id):
    """
    The people in a group, for the group page. Only name, avatar and (when the
    student allows it) class year, major and study style are returned. Never
    an email address.
    """
    ensure_table(conn)
    rows = conn.execute(
        """
        SELECT u.id, u.name, p.class_year, p.major, p.study_style,
               p.avatar_color, p.avatar_path, p.show_in_groups
        FROM group_memberships gm
        JOIN users u ON u.id = gm.user_id
        LEFT JOIN user_profiles p ON p.user_id = u.id
        WHERE gm.group_id = ?
        ORDER BY (u.id = ?) DESC, u.name
        """,
        (group_id, creator_id),
    ).fetchall()

    cards = []
    for row in rows:
        visible = row["show_in_groups"] != 0
        headline = " · ".join(
            part for part in (row["class_year"], row["major"]) if part
        ) if visible else ""
        cards.append(
            {
                "id": row["id"],
                "name": row["name"],
                "initials": initials_of(row["name"]),
                "color": AVATAR_COLORS.get(row["avatar_color"], AVATAR_COLORS[DEFAULT_AVATAR]),
                "photo_url": avatar_url_for(row["avatar_path"]),
                "headline": headline,
                "study_style": (row["study_style"] or "") if visible else "",
                "is_creator": row["id"] == creator_id,
            }
        )
    return cards


@profile_bp.route("/profile", methods=["GET", "POST"])
def profile():
    user_id = session.get("user_id")
    if user_id is None:
        return redirect(url_for("login"))

    conn = get_connection()
    try:
        ensure_table(conn)
        user = conn.execute(
            "SELECT id, name, email FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            session.clear()
            return redirect(url_for("login"))

        errors = []
        saved = False
        status_code = 200
        values = load_profile(conn, user)
        current_avatar = values["avatar_path"]

        if request.method == "POST":
            values, errors = clean_form(request.form)
            values["avatar_path"] = current_avatar

            new_avatar = None
            upload = request.files.get("avatar")
            if not errors and upload and upload.filename:
                new_avatar, photo_error = save_avatar(upload, user_id)
                if photo_error:
                    errors.append(photo_error)

            if errors:
                status_code = 400
            else:
                save_profile(conn, user_id, values)
                if new_avatar:
                    set_avatar_path(conn, user_id, new_avatar)
                    delete_avatar_file(current_avatar)
                    values["avatar_path"] = new_avatar
                elif request.form.get("remove_photo") == "on" and current_avatar:
                    set_avatar_path(conn, user_id, "")
                    delete_avatar_file(current_avatar)
                    values["avatar_path"] = ""
                session["user_name"] = values["display_name"]
                saved = True

        stats = {
            "groups": count_rows(
                conn, "SELECT COUNT(*) FROM group_memberships WHERE user_id = ?", (user_id,)
            ),
            "requests": count_rows(
                conn,
                "SELECT COUNT(*) FROM study_requests WHERE user_id = ? AND status = 'active'",
                (user_id,),
            ),
            "blocks": count_rows(
                conn, "SELECT COUNT(*) FROM user_availability WHERE user_id = ?", (user_id,)
            ),
        }
    finally:
        conn.close()

    headline_parts = [part for part in (values["class_year"], values["major"]) if part]

    return (
        render_template(
            "profile.html",
            values=values,
            errors=errors,
            saved=saved,
            stats=stats,
            email=user["email"],
            initials=initials_of(values["display_name"] or user["name"]),
            headline=" · ".join(headline_parts),
            class_years=CLASS_YEARS,
            study_styles=STUDY_STYLES,
            meeting_preferences=MEETING_PREFERENCES,
            group_sizes=GROUP_SIZES,
            study_goals=STUDY_GOALS,
            max_bio=MAX_BIO,
            avatar_colors=AVATAR_COLORS,
            avatar_hex=AVATAR_COLORS.get(values["avatar_color"], AVATAR_COLORS[DEFAULT_AVATAR]),
            avatar_url=avatar_url_for(values["avatar_path"]),
        ),
        status_code,
    )
