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

from db import get_db_connection  # one shared database setting for the whole app
from matching import find_group_matches, find_partner_matches

dashboard_bp = Blueprint("dashboard", __name__, template_folder="templates")

# Most matches shown on the dashboard.
MAX_DASHBOARD_MATCHES = 5


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
    Active study requests for the user (newest first).
    Filters out inactive, completed, cancelled, closed, or fulfilled requests.

    study_requests is a core table created by init_db(), so a database error
    here is a real problem and is raised, not reported as "no requests".
    """
    if not user_id:
        return []

    # The excluded statuses are fixed literals; user_id is the only bound value.
    rows = conn.execute(
        """
        SELECT * FROM study_requests
        WHERE user_id = ?
          AND (
                status IS NULL
                OR LOWER(TRIM(status)) NOT IN (
                    'inactive', 'completed', 'cancelled', 'closed',
                    'fulfilled', 'matched', 'expired'
                )
              )
        ORDER BY id DESC
        """,
        (user_id,),
    ).fetchall()

    return [dict(r) for r in rows]


def get_user_smart_matches(conn, user_id, limit=MAX_DASHBOARD_MATCHES):
    """
    Smart Study Matches for the logged-in user, computed with the real matching
    logic in matching.py (the same code behind /study-matches/<request_id>).

    Matches are built from the user's own active study requests only, so one
    user never sees matches generated for another user's requests. Groups the
    user already belongs to are not recommended. Each item has the keys the
    dashboard template reads: group_name/title, course_code, match_score,
    reason, plus group_id (group match) or request_id (study partner match).
    A user with no active requests gets an empty list.
    """
    if not user_id:
        return []

    requests = conn.execute(
        """
        SELECT * FROM study_requests
        WHERE user_id = ? AND status = 'active'
        ORDER BY id DESC
        """,
        (user_id,),
    ).fetchall()
    if not requests:
        return []

    joined_group_ids = {
        row["group_id"]
        for row in conn.execute(
            "SELECT group_id FROM group_memberships WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    }

    best_by_key = {}

    def keep_best(key, item):
        current = best_by_key.get(key)
        if current is None or item["match_score"] > current["match_score"]:
            best_by_key[key] = item

    for study_request in requests:
        for match in find_group_matches(conn, study_request):
            if match["id"] in joined_group_ids:
                continue
            keep_best(("group", match["id"]), {
                "group_id": match["id"],
                "group_name": match["group_name"],
                "course_code": match["course_code"],
                "match_score": match["score"],
                "reason": ", ".join(match["reasons"]),
            })

        for match in find_partner_matches(conn, user_id, study_request):
            keep_best(("partner", match["user_id"]), {
                "request_id": study_request["id"],
                "title": f"Study partner: {match['name']}",
                "course_code": match["course_code"],
                "match_score": match["score"],
                "reason": ", ".join(match["reasons"]),
            })

    ranked = sorted(
        best_by_key.values(), key=lambda item: item["match_score"], reverse=True
    )
    return ranked[:limit]


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


@dashboard_bp.errorhandler(sqlite3.Error)
def handle_database_error(error):
    """
    A database failure on the dashboard (missing table or column, bad query) is
    logged with its traceback and answered with a generic page. SQL and
    database details are never sent to the browser. In debug mode the error is
    re-raised so the developer sees the full traceback.
    """
    current_app.logger.exception("Database error while building the dashboard")
    if current_app.debug:
        raise error
    return "Something went wrong loading your dashboard. Please try again later.", 500
