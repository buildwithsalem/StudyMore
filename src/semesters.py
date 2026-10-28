"""Semesters: choose the semester you are working in, add course deadlines,
and see them on a monthly calendar or a whole-semester overview."""

import calendar
import sqlite3
from datetime import date, datetime, timedelta

from flask import Blueprint, redirect, render_template, request, session, url_for

from db import get_db_connection
from student_profile import normalize_course

semesters_bp = Blueprint("semesters", __name__, template_folder="templates")

TERMS = ("Spring", "Summer", "Fall", "Winter")
ITEM_TYPES = ("Exam", "Quiz", "Assignment", "Project", "Reading", "Other")
MAX_TITLE = 120
MAX_NOTES = 300


def ensure_semester_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semesters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            term TEXT NOT NULL,
            year INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semester_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            semester_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            course_code TEXT,
            title TEXT NOT NULL,
            item_type TEXT NOT NULL,
            due_date TEXT NOT NULL,
            notes TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (semester_id) REFERENCES semesters(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )


def parse_date(text):
    try:
        return datetime.strptime((text or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def get_owned_semester(conn, semester_id, user_id):
    return conn.execute(
        "SELECT * FROM semesters WHERE id = ? AND user_id = ?",
        (semester_id, user_id),
    ).fetchone()


def user_courses(conn, user_id):
    """Course codes from the student's profile, for the course drop-down."""
    try:
        row = conn.execute(
            "SELECT courses FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return []
    if row is None or not row["courses"]:
        return []
    return [c.strip() for c in row["courses"].split(",") if c.strip()]


# ---------------------------------------------------------------- events

def session_dates(session_row, cancelled, edited):
    """Every (date, start, end, title) a study session occurs on."""
    start = parse_date(session_row["session_date"])
    if start is None:
        return []
    end = parse_date(session_row["recurrence_end_date"])
    recurrence = session_row["recurrence"]
    if recurrence == "daily":
        step = timedelta(days=1)
    elif recurrence == "weekly":
        step = timedelta(weeks=1)
    else:
        step = None

    days = [start]
    if step and end:
        current = start + step
        while current <= end and current <= start + timedelta(days=365):
            days.append(current)
            current += step

    out = []
    for day in days:
        key = (session_row["id"], day.isoformat())
        if key in cancelled:
            continue
        if key in edited:
            e = edited[key]
            moved = parse_date(e["session_date"]) or day
            out.append((moved, e["start_time"], e["end_time"], e["title"]))
        else:
            out.append((day, session_row["start_time"], session_row["end_time"], session_row["title"]))
    return out


def load_events(conn, user_id, semester_id, range_start, range_end):
    """{date: [event, ...]} for items, study sessions and group meetings."""
    events = {}

    def add(day, kind, label, sub="", when=""):
        if range_start <= day <= range_end:
            events.setdefault(day, []).append(
                {"kind": kind, "label": label, "sub": sub, "when": when}
            )

    for row in conn.execute(
        "SELECT * FROM semester_items WHERE semester_id = ? AND user_id = ?",
        (semester_id, user_id),
    ):
        day = parse_date(row["due_date"])
        if day:
            sub = " · ".join(p for p in (row["course_code"], row["item_type"]) if p)
            add(day, "item", row["title"], sub)

    cancelled = {
        (r["session_id"], r["occurrence_date"])
        for r in conn.execute("SELECT session_id, occurrence_date FROM cancelled_session_occurrences")
    }
    edited = {
        (r["session_id"], r["original_occurrence_date"]): dict(r)
        for r in conn.execute("SELECT * FROM edited_session_occurrences")
    }
    sessions = conn.execute(
        """
        SELECT ss.*, sg.group_name, sg.course_code
        FROM study_sessions ss
        JOIN study_groups sg ON sg.id = ss.group_id
        JOIN group_memberships gm ON gm.group_id = ss.group_id
        WHERE gm.user_id = ? AND ss.status = 'scheduled'
        """,
        (user_id,),
    ).fetchall()
    for s in sessions:
        for day, start_t, end_t, title in session_dates(s, cancelled, edited):
            add(day, "session", title, s["group_name"], f"{start_t}-{end_t}")

    for g in conn.execute(
        """
        SELECT sg.group_name, sg.meeting_date, sg.meeting_time, sg.status
        FROM study_groups sg
        JOIN group_memberships gm ON gm.group_id = sg.id
        WHERE gm.user_id = ?
        """,
        (user_id,),
    ):
        if (g["status"] or "").lower() == "cancelled":
            continue
        day = parse_date(g["meeting_date"])
        if day:
            add(day, "meeting", g["group_name"], "Group meeting", g["meeting_time"])

    for day in events:
        events[day].sort(key=lambda e: e["when"] or "99")
    return events


# ---------------------------------------------------------------- routes

def login_required_user():
    return session.get("user_id")


@semesters_bp.route("/semesters", methods=["GET", "POST"])
def semesters():
    user_id = login_required_user()
    if user_id is None:
        return redirect(url_for("login"))

    conn = get_db_connection()
    ensure_semester_schema(conn)
    error = None
    status_code = 200

    if request.method == "POST":
        term = (request.form.get("term") or "").strip()
        year_text = (request.form.get("year") or "").strip()
        start = parse_date(request.form.get("start_date"))
        end = parse_date(request.form.get("end_date"))

        if term not in TERMS:
            error = "Please choose a term."
        elif not year_text.isdigit() or not 2000 <= int(year_text) <= 2100:
            error = "Please enter a valid year."
        elif start is None or end is None:
            error = "Please enter a start date and an end date."
        elif end <= start:
            error = "The end date must be after the start date."
        elif (end - start).days > 400:
            error = "A semester can be at most 400 days long."
        else:
            year = int(year_text)
            name = f"{term} {year}"
            duplicate = conn.execute(
                "SELECT 1 FROM semesters WHERE user_id = ? AND name = ?", (user_id, name)
            ).fetchone()
            if duplicate:
                error = f"You already have a {name} semester."

        if error is None:
            has_active = conn.execute(
                "SELECT 1 FROM semesters WHERE user_id = ? AND is_active = 1", (user_id,)
            ).fetchone()
            conn.execute(
                "INSERT INTO semesters (user_id, name, term, year, start_date, end_date, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, name, term, year, start.isoformat(), end.isoformat(), 0 if has_active else 1),
            )
            conn.commit()
            return redirect(url_for("semesters.semesters"))
        status_code = 400

    rows = conn.execute(
        """
        SELECT s.*, (SELECT COUNT(*) FROM semester_items i WHERE i.semester_id = s.id) AS item_count
        FROM semesters s WHERE s.user_id = ? ORDER BY s.start_date DESC
        """,
        (user_id,),
    ).fetchall()
    return (
        render_template(
            "semesters.html",
            semesters=[dict(r) for r in rows],
            terms=TERMS,
            this_year=date.today().year,
            error=error,
            form=request.form,
        ),
        status_code,
    )


@semesters_bp.route("/semesters/<int:semester_id>/activate", methods=["POST"])
def activate_semester(semester_id):
    user_id = login_required_user()
    if user_id is None:
        return redirect(url_for("login"))
    conn = get_db_connection()
    ensure_semester_schema(conn)
    if get_owned_semester(conn, semester_id, user_id) is None:
        return "Semester not found", 404
    conn.execute("UPDATE semesters SET is_active = 0 WHERE user_id = ?", (user_id,))
    conn.execute("UPDATE semesters SET is_active = 1 WHERE id = ?", (semester_id,))
    conn.commit()
    return redirect(url_for("semesters.semesters"))


@semesters_bp.route("/semesters/<int:semester_id>/delete", methods=["POST"])
def delete_semester(semester_id):
    user_id = login_required_user()
    if user_id is None:
        return redirect(url_for("login"))
    conn = get_db_connection()
    ensure_semester_schema(conn)
    sem = get_owned_semester(conn, semester_id, user_id)
    if sem is None:
        return "Semester not found", 404
    conn.execute("DELETE FROM semester_items WHERE semester_id = ?", (semester_id,))
    conn.execute("DELETE FROM semesters WHERE id = ?", (semester_id,))
    if sem["is_active"]:
        newest = conn.execute(
            "SELECT id FROM semesters WHERE user_id = ? ORDER BY start_date DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if newest:
            conn.execute("UPDATE semesters SET is_active = 1 WHERE id = ?", (newest["id"],))
    conn.commit()
    return redirect(url_for("semesters.semesters"))


def month_start_of(text, fallback):
    try:
        d = datetime.strptime(text, "%Y-%m").date()
        return d.replace(day=1)
    except (TypeError, ValueError):
        return fallback.replace(day=1)


def shift_month(first_of_month, delta):
    index = first_of_month.year * 12 + first_of_month.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)


@semesters_bp.route("/semesters/<int:semester_id>")
def semester_view(semester_id):
    user_id = login_required_user()
    if user_id is None:
        return redirect(url_for("login"))
    conn = get_db_connection()
    ensure_semester_schema(conn)
    sem = get_owned_semester(conn, semester_id, user_id)
    if sem is None:
        return "Semester not found", 404

    sem_start = parse_date(sem["start_date"])
    sem_end = parse_date(sem["end_date"])
    today = date.today()
    view = "semester" if request.args.get("view") == "semester" else "month"

    items = conn.execute(
        "SELECT * FROM semester_items WHERE semester_id = ? AND user_id = ? ORDER BY due_date, title",
        (semester_id, user_id),
    ).fetchall()
    upcoming = [i for i in items if i["due_date"] >= today.isoformat()]

    context = {
        "semester": dict(sem),
        "view": view,
        "items": [dict(i) for i in items],
        "upcoming_count": len(upcoming),
        "next_item": dict(upcoming[0]) if upcoming else None,
        "days_left": max((sem_end - today).days, 0),
        "total_days": (sem_end - sem_start).days or 1,
        "days_done": min(max((today - sem_start).days, 0), (sem_end - sem_start).days or 1),
        "item_types": ITEM_TYPES,
        "courses": user_courses(conn, user_id),
        "today": today,
        "error": request.args.get("error"),
    }

    if view == "month":
        default = today if sem_start <= today <= sem_end else sem_start
        first = month_start_of(request.args.get("month"), default)
        weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(first.year, first.month)
        events = load_events(conn, user_id, semester_id, weeks[0][0], weeks[-1][-1])
        context.update(
            month_label=first.strftime("%B %Y"),
            prev_month=shift_month(first, -1).strftime("%Y-%m"),
            next_month=shift_month(first, 1).strftime("%Y-%m"),
            weeks=weeks,
            events=events,
            current_month=first.month,
        )
    else:
        events = load_events(conn, user_id, semester_id, sem_start, sem_end)
        months = []
        cursor = sem_start.replace(day=1)
        while cursor <= sem_end:
            days = sorted(d for d in events if d.year == cursor.year and d.month == cursor.month)
            months.append(
                {
                    "label": cursor.strftime("%B %Y"),
                    "key": cursor.strftime("%Y-%m"),
                    "days": [(d, events[d]) for d in days],
                    "count": sum(len(events[d]) for d in days),
                }
            )
            cursor = shift_month(cursor, 1)
        context["months"] = months

    return render_template("semester_view.html", **context)


@semesters_bp.route("/semesters/<int:semester_id>/items", methods=["POST"])
def add_item(semester_id):
    user_id = login_required_user()
    if user_id is None:
        return redirect(url_for("login"))
    conn = get_db_connection()
    ensure_semester_schema(conn)
    sem = get_owned_semester(conn, semester_id, user_id)
    if sem is None:
        return "Semester not found", 404

    title = " ".join((request.form.get("title") or "").split())
    item_type = (request.form.get("item_type") or "").strip()
    due = parse_date(request.form.get("due_date"))
    course = normalize_course(request.form.get("course_code") or "") if request.form.get("course_code") else ""
    notes = (request.form.get("notes") or "").strip()

    error = None
    if not title or len(title) > MAX_TITLE:
        error = f"Please enter a title of {MAX_TITLE} characters or fewer."
    elif item_type not in ITEM_TYPES:
        error = "Please choose a type."
    elif due is None:
        error = "Please enter a due date."
    elif not parse_date(sem["start_date"]) <= due <= parse_date(sem["end_date"]):
        error = "The due date must fall inside this semester."
    elif len(notes) > MAX_NOTES:
        error = f"Notes must be {MAX_NOTES} characters or fewer."

    if error:
        return redirect(url_for("semesters.semester_view", semester_id=semester_id, error=error))

    conn.execute(
        "INSERT INTO semester_items (semester_id, user_id, course_code, title, item_type, due_date, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (semester_id, user_id, course, title, item_type, due.isoformat(), notes),
    )
    conn.commit()
    return redirect(
        url_for("semesters.semester_view", semester_id=semester_id, month=due.strftime("%Y-%m"))
    )


@semesters_bp.route("/semesters/<int:semester_id>/items/<int:item_id>/delete", methods=["POST"])
def delete_item(semester_id, item_id):
    user_id = login_required_user()
    if user_id is None:
        return redirect(url_for("login"))
    conn = get_db_connection()
    ensure_semester_schema(conn)
    conn.execute(
        "DELETE FROM semester_items WHERE id = ? AND semester_id = ? AND user_id = ?",
        (item_id, semester_id, user_id),
    )
    conn.commit()
    return redirect(url_for("semesters.semester_view", semester_id=semester_id))