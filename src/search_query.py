"""Query logic for searching and filtering study groups."""

import re
import sqlite3

MEETING_TYPES = ("In-Person", "Online", "Hybrid")
GROUP_STATUSES = ("Open", "Full", "Completed", "Cancelled")
CLOSED_STATUSES = ("Completed", "Cancelled")
MAX_SEARCH_TERM_LENGTH = 100


class SearchValidationError(Exception):
    """Raised when the search request cannot be answered. Message is shown to the user."""


def normalize_course_code(value):
    """'CSE 3311', 'cse3311', 'CSE  3311' -> 'CSE3311'."""
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value)).upper()


def tidy_status(value):
    """Normalize a stored status for display. app.py stores 'open' in lowercase."""
    text = (value or "").strip().title()
    return text if text in GROUP_STATUSES else "Open"


def escape_like(value):
    """Escape % and _ so they are matched literally inside a LIKE pattern."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def contains_pattern(value):
    return "%" + escape_like(value) + "%"


def starts_with_pattern(value):
    return escape_like(value) + "%"


def clean_filters(raw):
    """
    Validate and normalize the raw form values.

    Returns a dict with keys term, course_code, section, meeting_type, status,
    available_only and is_browse. Raises SearchValidationError for input that
    cannot be searched on.
    """
    raw = raw or {}

    def text_of(key):
        value = raw.get(key)
        return "" if value is None else str(value).strip()

    term = text_of("term")
    if len(term) > MAX_SEARCH_TERM_LENGTH:
        raise SearchValidationError(
            "Your search is too long. Please use "
            f"{MAX_SEARCH_TERM_LENGTH} characters or fewer."
        )

    meeting_type = text_of("meeting_type")
    if meeting_type and meeting_type not in MEETING_TYPES:
        raise SearchValidationError(
            f"{meeting_type} is not a valid meeting type. "
            "Please choose In-Person, Online, or Hybrid."
        )

    status = text_of("status").title()
    if status and status not in GROUP_STATUSES:
        raise SearchValidationError(
            f"{status} is not a valid group status. "
            "Please choose Open, Full, Completed, or Cancelled."
        )

    available_raw = raw.get("available_only")
    available_only = (
        str(available_raw).lower() in ("on", "true", "1", "yes")
        if available_raw
        else False
    )

    # "Available only" means Open, so combining it with any other status can
    # never match anything. Say so instead of returning an empty list.
    if available_only and status in ("Full",) + CLOSED_STATUSES:
        raise SearchValidationError(
            f'"Available groups only" cannot be combined with a status of '
            f"{status}, because a {status.lower()} group is not available. "
            "Please clear one of those two filters."
        )

    course_code = normalize_course_code(text_of("course_code"))
    section = text_of("section").upper()

    filters = {
        "term": term,
        "course_code": course_code,
        "section": section,
        "meeting_type": meeting_type,
        "status": status,
        "available_only": available_only,
    }

    # An empty search is not an error. Treat it as browsing all groups.
    filters["is_browse"] = not any(
        (term, course_code, section, meeting_type, status, available_only)
    )

    return filters


def build_search_query(filters):
    """
    Build the SELECT statement and parameter list for a cleaned filter dict.

    Member counts come from group_memberships rather than a stored column, so
    conditions on the count go in HAVING. All user values are bound parameters.
    """
    # LEFT JOIN so a group with no members still appears.
    sql = [
        "SELECT",
        "    g.id AS group_id,",
        "    g.group_name,",
        "    g.course_code,",
        "    g.section,",
        "    g.description,",
        "    g.meeting_type,",
        "    g.meeting_date,",
        "    g.meeting_time,",
        "    g.location,",
        "    g.meeting_link,",
        "    g.max_members,",
        "    g.status,",
        "    COUNT(m.id) AS current_members",
        "FROM study_groups AS g",
        "LEFT JOIN group_memberships AS m ON m.group_id = g.id",
    ]

    where = []
    having = []
    params = []

    if filters["term"]:
        where.append(
            "("
            " REPLACE(UPPER(g.course_code), ' ', '') LIKE ? ESCAPE '\\'"
            " OR UPPER(g.group_name) LIKE ? ESCAPE '\\'"
            " OR UPPER(COALESCE(g.description, '')) LIKE ? ESCAPE '\\'"
            ")"
        )
        params.append(contains_pattern(normalize_course_code(filters["term"])))
        params.append(contains_pattern(filters["term"].upper()))
        params.append(contains_pattern(filters["term"].upper()))

    # Prefix match so "CSE" lists every CSE course.
    if filters["course_code"]:
        where.append("REPLACE(UPPER(g.course_code), ' ', '') LIKE ? ESCAPE '\\'")
        params.append(starts_with_pattern(filters["course_code"]))

    if filters["section"]:
        where.append("UPPER(TRIM(COALESCE(g.section, ''))) = ?")
        params.append(filters["section"])

    if filters["meeting_type"]:
        where.append("g.meeting_type = ?")
        params.append(filters["meeting_type"])

    # Status is derived: Completed/Cancelled from the column, otherwise Full if
    # the count has reached max_members, otherwise Open. Nothing in app.py
    # writes a status other than the default, so the column alone is not enough.
    wanted_status = filters["status"]
    if filters["available_only"] and not wanted_status:
        wanted_status = "Open"

    if wanted_status in CLOSED_STATUSES:
        where.append("UPPER(g.status) = ?")
        params.append(wanted_status.upper())
    elif wanted_status == "Open":
        where.append("UPPER(g.status) NOT IN ('COMPLETED', 'CANCELLED')")
        having.append("COUNT(m.id) < g.max_members")
    elif wanted_status == "Full":
        where.append("UPPER(g.status) NOT IN ('COMPLETED', 'CANCELLED')")
        having.append("COUNT(m.id) >= g.max_members")

    if where:
        sql.append("WHERE " + "\n  AND ".join(where))

    sql.append("GROUP BY g.id")

    if having:
        sql.append("HAVING " + " AND ".join(having))

    # Joinable groups first, then full, then closed. Soonest meeting within each.
    sql.append(
        "ORDER BY\n"
        "    CASE\n"
        "        WHEN UPPER(g.status) IN ('COMPLETED', 'CANCELLED') THEN 2\n"
        "        WHEN COUNT(m.id) >= g.max_members THEN 1\n"
        "        ELSE 0\n"
        "    END ASC,\n"
        "    g.meeting_date ASC,\n"
        "    g.meeting_time ASC,\n"
        "    g.group_name ASC"
    )

    return "\n".join(sql), params


def shape_result_row(row):
    """Turn a result row into the dict the template displays."""
    current_members = int(row["current_members"])
    max_members = int(row["max_members"])
    stored_status = tidy_status(row["status"])

    # Same rules as the status filter in build_search_query().
    if stored_status in CLOSED_STATUSES:
        display_status = stored_status
    elif current_members >= max_members:
        display_status = "Full"
    else:
        display_status = "Open"

    if display_status == "Open":
        join_blocked_reason = None
    elif display_status == "Full":
        join_blocked_reason = "This group is full."
    elif display_status == "Completed":
        join_blocked_reason = "This group has already finished meeting."
    else:
        join_blocked_reason = "This group has been cancelled."

    location = (row["location"] or "").strip()
    meeting_link = (row["meeting_link"] or "").strip()
    if row["meeting_type"] == "Online":
        location_display = "Online"
    elif row["meeting_type"] == "Hybrid":
        location_display = f"{location} and online" if location else "Online"
    else:
        location_display = location or "Location to be announced"

    return {
        "group_id": row["group_id"],
        "group_name": row["group_name"],
        "course_code": row["course_code"],
        "section": row["section"] or "",
        "description": row["description"] or "",
        "meeting_type": row["meeting_type"],
        "meeting_date": row["meeting_date"],
        "meeting_time": row["meeting_time"],
        "location": location,
        "meeting_link": meeting_link,
        "location_display": location_display,
        "current_members": current_members,
        "max_members": max_members,
        "spots_left": max(max_members - current_members, 0),
        "status": display_status,
        "is_joinable": join_blocked_reason is None,
        "join_blocked_reason": join_blocked_reason,
    }


def search_study_groups(conn, raw_filters):
    """
    Run a search. Returns (results, notice).

    notice is a message for the empty search and no results cases, else None.
    SearchValidationError from clean_filters() is left for the caller.
    """
    filters = clean_filters(raw_filters)
    sql, params = build_search_query(filters)

    cursor = conn.execute(sql, params)
    results = [shape_result_row(row) for row in cursor.fetchall()]

    notice = None
    if not results:
        notice = (
            "No study groups match your search. Try a different course code, "
            "remove a filter, or create a group for this course yourself."
        )
    elif filters["is_browse"]:
        notice = (
            "Showing every study group. Enter a course code or choose a filter "
            "to narrow the list."
        )

    return results, notice


def connect(database_path):
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    return conn
