"""
Student Dashboard Module for StudyMore.
Handles data aggregation, upcoming meetings, study requests, smart match integration hooks,
and rendering for the /dashboard route.
"""

from datetime import datetime
import sqlite3
from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    session,
    url_for,
)

dashboard_bp = Blueprint("dashboard", __name__, template_folder="templates")

DEFAULT_DATABASE = "study_more.db"


def get_db_connection():
    """Returns a SQLite connection configured for Row access."""
    db_name = current_app.config.get("DATABASE", DEFAULT_DATABASE)
    conn = sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    return conn


def format_location_display(meeting_type, location, meeting_link=None):
    """Formats the meeting location string consistently with project conventions."""
    loc = (location or "").strip()
    m_type = (meeting_type or "").strip()

    if m_type == "Online":
        return "Online"
    if m_type == "Hybrid":
        if loc:
            return f"{loc} and online"
        return "Hybrid (location TBA)"
    if m_type == "In-Person":
        return loc or "Location TBA"
    return loc or "TBA"


def derive_group_status(stored_status, member_count, max_members):
    """Derives clean capitalized status, handling full groups."""
    raw_status = (stored_status or "open").strip().lower()
    if raw_status == "open" and max_members and member_count >= max_members:
        return "Full"
    return raw_status.title()


def is_meeting_in_future(meeting_date, meeting_time, now=None):
    """
    Checks if a combined meeting date (YYYY-MM-DD) and meeting time (HH:MM)
    is strictly in the future compared to `now`.
    """
    if not meeting_date:
        return False
    if now is None:
        now = datetime.now()

    date_str = str(meeting_date).strip()
    time_str = (str(meeting_time).strip() if meeting_time else "23:59")

    # Attempt combined datetime parse
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            combined = f"{date_str} {time_str}"
            dt = datetime.strptime(combined, fmt)
            return dt > now
        except ValueError:
            continue

    # Fallback to date-only comparison if time format is unexpected
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.date() > now.date() or (dt.date() == now.date() and dt.time() > now.time())
    except ValueError:
        return False


def get_user_info(conn, user_id):
    """Retrieves basic profile info for the logged-in user."""
    if not user_id:
        return None
    row = conn.execute(
        "SELECT id, name, email FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row:
        return dict(row)
    return None


def get_user_joined_groups(conn, user_id):
    """
    Retrieves all study groups the user has joined, along with live member counts
    and formatted display fields.
    """
    if not user_id:
        return []

    query = """
        SELECT 
            sg.id AS group_id,
            sg.id,
            sg.creator_user_id,
            sg.group_name,
            sg.course_code,
            sg.section,
            sg.description,
            sg.meeting_type,
            sg.meeting_date,
            sg.meeting_time,
            sg.location,
            sg.meeting_link,
            sg.max_members,
            sg.status AS raw_status,
            COUNT(gm_all.id) AS member_count
        FROM study_groups sg
        JOIN group_memberships gm_user ON sg.id = gm_user.group_id
        LEFT JOIN group_memberships gm_all ON sg.id = gm_all.group_id
        WHERE gm_user.user_id = ?
        GROUP BY sg.id
        ORDER BY sg.meeting_date ASC, sg.meeting_time ASC
    """
    rows = conn.execute(query, (user_id,)).fetchall()

    groups = []
    for row in rows:
        g = dict(row)
        g["status"] = derive_group_status(g["raw_status"], g["member_count"], g["max_members"])
        g["location_display"] = format_location_display(
            g.get("meeting_type"), g.get("location"), g.get("meeting_link")
        )
        g["spots_left"] = max(0, g["max_members"] - g["member_count"])
        g["is_creator"] = (g.get("creator_user_id") == user_id)
        groups.append(g)

    return groups


def get_upcoming_meetings(conn, user_id, now=None):
    """
    Retrieves upcoming meetings from the user's joined study groups.
    Only includes meetings whose combined date and time are in the future,
    and whose status is not completed or cancelled.
    Ordered chronologically by meeting date and time.
    """
    if not user_id:
        return []

    query = """
        SELECT 
            sg.id AS group_id,
            sg.id,
            sg.group_name,
            sg.course_code,
            sg.section,
            sg.description,
            sg.meeting_type,
            sg.meeting_date,
            sg.meeting_time,
            sg.location,
            sg.meeting_link,
            sg.max_members,
            sg.status AS raw_status,
            COUNT(gm_all.id) AS member_count
        FROM study_groups sg
        JOIN group_memberships gm_user ON sg.id = gm_user.group_id
        LEFT JOIN group_memberships gm_all ON sg.id = gm_all.group_id
        WHERE gm_user.user_id = ?
          AND LOWER(sg.status) NOT IN ('cancelled', 'completed')
        GROUP BY sg.id
        ORDER BY sg.meeting_date ASC, sg.meeting_time ASC
    """
    rows = conn.execute(query, (user_id,)).fetchall()

    meetings = []
    for row in rows:
        m = dict(row)
        # Exclude meetings that have already passed in time
        if not is_meeting_in_future(m.get("meeting_date"), m.get("meeting_time"), now=now):
            continue

        m["status"] = derive_group_status(m["raw_status"], m["member_count"], m["max_members"])
        m["location_display"] = format_location_display(
            m.get("meeting_type"), m.get("location"), m.get("meeting_link")
        )
        meetings.append(m)

    return meetings


def get_user_study_requests(conn, user_id):
    """
    Integration hook for Raghad's Study Request functionality.
    Gracefully returns active study requests if the table exists,
    or an empty list if the feature table is not yet created.
    Filters out inactive, completed, cancelled, closed, or fulfilled requests.
    """
    if not user_id:
        return []

    # Check if study_requests table exists
    table_check = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='study_requests'"
    ).fetchone()
    if not table_check:
        return []

    inactive_statuses = ("inactive", "completed", "cancelled", "closed", "fulfilled", "matched", "expired")
    placeholders = ", ".join("?" for _ in inactive_statuses)

    try:
        rows = conn.execute(
            f"""
            SELECT * FROM study_requests 
            WHERE user_id = ? 
              AND (status IS NULL OR LOWER(TRIM(status)) NOT IN ({placeholders}))
            ORDER BY id DESC
            """,
            (user_id, *inactive_statuses),
        ).fetchall()
        
        # Double check in python for robust filtering
        active_requests = []
        for r in rows:
            req = dict(r)
            st = (req.get("status") or "open").strip().lower()
            if st not in inactive_statuses:
                active_requests.append(req)
        return active_requests
    except sqlite3.OperationalError:
        return []


def get_user_smart_matches(conn, user_id):
    """
    Integration hook for Raghad's Smart Study Match functionality.
    Does NOT implement the matching algorithm, but queries and surfaces
    potential matches/recommendations when available.
    """
    if not user_id:
        return []

    # Check for possible match tables
    table_check = conn.execute(
        """
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name IN ('smart_matches', 'study_matches', 'smart_study_matches', 'study_recommendations')
        """
    ).fetchone()
    if not table_check:
        return []

    table_name = table_check["name"]
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM {table_name}
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_dashboard_data(conn, user_id, now=None):
    """Aggregates all data needed for the student dashboard."""
    user = get_user_info(conn, user_id)
    joined_groups = get_user_joined_groups(conn, user_id)
    upcoming_meetings = get_upcoming_meetings(conn, user_id, now=now)
    study_requests = get_user_study_requests(conn, user_id)
    smart_matches = get_user_smart_matches(conn, user_id)

    return {
        "user": user,
        "joined_groups": joined_groups,
        "upcoming_meetings": upcoming_meetings,
        "study_requests": study_requests,
        "smart_matches": smart_matches,
        "stats": {
            "joined_groups_count": len(joined_groups),
            "upcoming_meetings_count": len(upcoming_meetings),
            "study_requests_count": len(study_requests),
            "smart_matches_count": len(smart_matches),
        },
    }


@dashboard_bp.route("/dashboard")
def dashboard():
    """Main dashboard route. Requires authentication."""
    user_id = session.get("user_id")
    if user_id is None:
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        data = get_dashboard_data(conn, user_id)
    finally:
        conn.close()

    # Fallback to session name if user record is missing in DB
    user_name = session.get("user_name", "Student")
    if data["user"] and data["user"].get("name"):
        user_name = data["user"]["name"]

    return render_template(
        "dashboard.html",
        user=data["user"],
        user_name=user_name,
        joined_groups=data["joined_groups"],
        upcoming_meetings=data["upcoming_meetings"],
        study_requests=data["study_requests"],
        smart_matches=data["smart_matches"],
        stats=data["stats"],
    )
