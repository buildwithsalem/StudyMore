from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
from search import search_bp
from duplicate_check import duplicate_bp
from dashboard import dashboard_bp
from db import get_db_connection, init_app as init_database_config
from matching import (
    find_group_matches,
    find_partner_matches,
    get_user_study_request,
)
from validators import safe_meeting_url, validate_meeting_link
from search_query import derive_display_status
from search_query import meeting_has_passed
from datetime import datetime, timedelta

import sqlite3

app = Flask(__name__)
# One database setting (app.config["DATABASE"]) shared by every route and blueprint.
init_database_config(app)
# Only http(s) meeting links are ever rendered as hrefs, even for old rows.
app.add_template_filter(safe_meeting_url, "safe_meeting_url")

import os
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))
app.register_blueprint(search_bp)
from student_profile import profile_bp
app.register_blueprint(profile_bp)
app.register_blueprint(duplicate_bp)
app.register_blueprint(dashboard_bp)


def init_db(database=None):
    """Create any missing tables in `database` (default: app.config["DATABASE"])."""
    conn = sqlite3.connect(database or app.config["DATABASE"])
    try:
        _create_tables(conn)
        conn.commit()
    finally:
        conn.close()


def _create_tables(conn):
    cursor = conn.cursor()

    # Users table - Salem's login/account feature
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
    """)

    # Study groups table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS study_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_user_id INTEGER,
            group_name TEXT NOT NULL,
            course_code TEXT NOT NULL,
            section TEXT,
            description TEXT NOT NULL,
            meeting_type TEXT NOT NULL,
            meeting_date TEXT NOT NULL,
            meeting_time TEXT NOT NULL,
            location TEXT,
            meeting_link TEXT,
            max_members INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            FOREIGN KEY (creator_user_id) REFERENCES users(id)
        )
    """)

    # Add study_goal to existing study_groups databases
    existing_columns = [
        column[1]
        for column in cursor.execute("PRAGMA table_info(study_groups)").fetchall()
    ]

    if "study_goal" not in existing_columns:
        cursor.execute("""
            ALTER TABLE study_groups
            ADD COLUMN study_goal TEXT
        """)

    if "creator_user_id" not in existing_columns:
        cursor.execute("""
            ALTER TABLE study_groups
            ADD COLUMN creator_user_id INTEGER
        """)

    # Group memberships - Join/Leave/My Groups
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_memberships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            UNIQUE(user_id, group_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (group_id) REFERENCES study_groups(id)
        )
    """)

    # Study Requests
    # Allows students to find study partners when no suitable group exists.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS study_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_code TEXT NOT NULL,
            section TEXT,
            study_goal TEXT NOT NULL,
            meeting_preference TEXT NOT NULL,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # User Availability
    # Each row represents one selected one-hour block in the weekly availability grid.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_availability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day_of_week TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            UNIQUE(user_id, day_of_week, start_time, end_time),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    # Study Sessions - Iteration 3 Study Schedule & Reminders
    # Stores one-time and recurring study sessions created for a study group.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS study_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            created_by_user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            session_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            recurrence TEXT NOT NULL DEFAULT 'none',
            recurrence_end_date TEXT,
            status TEXT NOT NULL DEFAULT 'scheduled',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES study_groups(id),
            FOREIGN KEY (created_by_user_id) REFERENCES users(id)
        )
    """)
# Cancelled individual occurrences from recurring study sessions
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cancelled_session_occurrences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        occurrence_date TEXT NOT NULL,
        UNIQUE(session_id, occurrence_date),
        FOREIGN KEY (session_id) REFERENCES study_sessions(id)
    )
""")
        # Stores edits made to only one occurrence of a recurring session
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS edited_session_occurrences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            original_occurrence_date TEXT NOT NULL,
            title TEXT NOT NULL,
            session_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            UNIQUE(session_id, original_occurrence_date),
            FOREIGN KEY (session_id) REFERENCES study_sessions(id)
        )
    """)


def create_test_user(database=None):
    conn = sqlite3.connect(database or app.config["DATABASE"])
    try:
        hashed_password = generate_password_hash("test123")

        conn.execute("""
            INSERT OR IGNORE INTO users (name, email, password)
            VALUES (?, ?, ?)
        """, ("Test User", "test@example.com", hashed_password))

        conn.commit()
    finally:
        conn.close()



@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard.dashboard"))
    return redirect(url_for("login"))


@app.route("/create-group", methods=["GET", "POST"])
def create_group():
    user_id = session.get("user_id")

    if request.method == "POST":
        partner_id = request.form.get("partner_id", type=int)
        request_id = request.form.get("request_id", type=int)
        partner_request_id = request.form.get(
            "partner_request_id",
            type=int
        )
    else:
        partner_id = request.args.get("partner_id", type=int)
        request_id = request.args.get("request_id", type=int)
        partner_request_id = request.args.get(
            "partner_request_id",
            type=int
        )

    if user_id is None:
        return redirect(url_for("login"))

    if request.method == "POST":
        group_name = request.form.get("group_name")
        course_code = request.form.get("course_code")
        section = request.form.get("section")
        study_goal = request.form.get("study_goal")
        description = request.form.get("description")
        meeting_type = request.form.get("meeting_type")
        meeting_date = request.form.get("meeting_date")
        meeting_time = request.form.get("meeting_time")
        location = request.form.get("location")
        meeting_link = request.form.get("meeting_link")
        max_members = request.form.get("max_members")

        if any(value is None for value in (
            group_name, course_code, section, description,
            meeting_type, meeting_date, meeting_time,
            location, meeting_link, max_members
        )):
            return "Missing required form field", 400

        try:
            max_members = int(max_members)
        except (TypeError, ValueError):
            return "Max members must be a number", 400

        if max_members < 2:
            return "Max members must be at least 2", 400

        if meeting_has_passed(meeting_date, meeting_time):
            return render_template(
                "create_group.html",
                error="Meeting date and time must be in the future.",
                form=request.form,
                partner_id=partner_id,
                request_id=request_id,
                partner_request_id=partner_request_id
            )

        if meeting_type == "In-Person" and not location.strip():
            return render_template(
                "create_group.html",
                error="Location is required for in-person groups.",
                form=request.form,
                partner_id=partner_id,
                request_id=request_id,
                partner_request_id=partner_request_id
            )

        if meeting_type == "Online" and not meeting_link.strip():
            return render_template(
                "create_group.html",
                error="Meeting link is required for online groups.",
                form=request.form,
                partner_id=partner_id,
                request_id=request_id,
                partner_request_id=partner_request_id
            )

        if meeting_type == "Hybrid" and (
            not location.strip() or not meeting_link.strip()
        ):
            return render_template(
                "create_group.html",
                error="Location and meeting link are required for hybrid groups.",
                form=request.form,
                partner_id=partner_id,
                request_id=request_id,
                partner_request_id=partner_request_id
            )

        # A link is optional for In-Person groups (required cases are handled
        # above), but whenever one is given it must be a real http(s) URL.
        meeting_link, link_error = validate_meeting_link(meeting_link)
        if link_error:
            return render_template(
                "create_group.html",
                error=link_error,
                form=request.form,
                partner_id=partner_id,
                request_id=request_id,
                partner_request_id=partner_request_id
            ), 400

        connection = get_db_connection()
        connection.row_factory = sqlite3.Row

        # The partner is only added when the Study Request match is valid.
        validated_partner_id = None

        # Validate the Study Request match before adding the partner.
        if partner_id and request_id and partner_request_id:
            my_request = connection.execute("""
                SELECT *
                FROM study_requests
                WHERE id = ?
                  AND user_id = ?
                  AND status = 'active'
            """, (request_id, user_id)).fetchone()

            partner_request = connection.execute("""
                SELECT *
                FROM study_requests
                WHERE id = ?
                  AND user_id = ?
                  AND status = 'active'
            """, (partner_request_id, partner_id)).fetchone()

            if (
                my_request is None
                or partner_request is None
                or my_request["course_code"].upper()
                != partner_request["course_code"].upper()
            ):
                connection.close()
                return (
                    "This study match is no longer active or valid.",
                    400
                )

            validated_partner_id = partner_id

        cursor = connection.execute("""
            INSERT INTO study_groups (
                creator_user_id,
                group_name,
                course_code,
                section,
                study_goal,
                description,
                meeting_type,
                meeting_date,
                meeting_time,
                location,
                meeting_link,
                max_members
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            group_name,
            course_code,
            section,
            study_goal,
            description,
            meeting_type,
            meeting_date,
            meeting_time,
            location,
            meeting_link,
            max_members
        ))

        new_group_id = cursor.lastrowid

        # The creator automatically becomes a member.
        connection.execute("""
            INSERT INTO group_memberships (user_id, group_id)
            VALUES (?, ?)
        """, (user_id, new_group_id))

        # Add the matched study partner. Only a partner whose active Study
        # Request was validated above is added; a bare partner_id in the form
        # must never enroll an arbitrary user.
        if validated_partner_id and validated_partner_id != user_id:
            connection.execute("""
                INSERT OR IGNORE INTO group_memberships (user_id, group_id)
                VALUES (?, ?)
            """, (validated_partner_id, new_group_id))

        # The two Study Requests are no longer active after
        # the students create a group together.
        if request_id and partner_request_id:
            connection.execute("""
                UPDATE study_requests
                SET status = 'matched'
                WHERE id IN (?, ?)
            """, (request_id, partner_request_id))

        connection.commit()
        connection.close()

        return redirect(
            url_for("group_details", group_id=new_group_id)
        )

    # GET request: prefill the form when coming from Smart Study Match.
    prefill = {
        "course_code": request.args.get("course_code", ""),
        "section": request.args.get("section", ""),
        "study_goal": request.args.get("study_goal", ""),
        "meeting_type": request.args.get("meeting_type", "")
    }

    return render_template(
        "create_group.html",
        form=prefill,
        partner_id=partner_id,
        request_id=request_id,
        partner_request_id=partner_request_id
    )


@app.route("/group/<int:group_id>")
def group_details(group_id):
    user_id = session.get("user_id")

    if user_id is None:
        return redirect(url_for("login"))

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    group = conn.execute("""
        SELECT *
        FROM study_groups
        WHERE id = ?
    """, (group_id,)).fetchone()

    if group is None:
        conn.close()
        return "Study group not found", 404

    member_count = conn.execute("""
        SELECT COUNT(*)
        FROM group_memberships
        WHERE group_id = ?
    """, (group_id,)).fetchone()[0]

    membership = conn.execute("""
        SELECT 1
        FROM group_memberships
        WHERE group_id = ? AND user_id = ?
    """, (group_id, user_id)).fetchone()

    is_member = membership is not None
    is_creator = group["creator_user_id"] == user_id

    display_status = derive_display_status(
        group["status"], member_count, group["max_members"],
        group["meeting_date"], group["meeting_time"]
    )

    conn.close()

    return render_template(
        "group_details.html",
        group=group,
        member_count=member_count,
        is_member=is_member,
        is_creator=is_creator,
        display_status=display_status
    )


@app.route("/group/<int:group_id>/status", methods=["POST"])
def set_group_status(group_id):
    user_id = session.get("user_id")

    if user_id is None:
        return redirect(url_for("login"))

    new_status = request.form.get("status", "").strip()
    if new_status not in ("Open", "Completed", "Cancelled"):
        return "Invalid status", 400

    conn = sqlite3.connect("study_more.db")
    conn.row_factory = sqlite3.Row

    group = conn.execute(
        "SELECT * FROM study_groups WHERE id = ?", (group_id,)
    ).fetchone()

    if group is None:
        conn.close()
        return "Study group not found", 404

    if group["creator_user_id"] != user_id:
        conn.close()
        return "Only the group creator can change its status", 403

    stored_value = "open" if new_status == "Open" else new_status

    conn.execute(
        "UPDATE study_groups SET status = ? WHERE id = ?",
        (stored_value, group_id)
    )

    conn.commit()
    conn.close()

    return redirect(url_for("group_details", group_id=group_id))


@app.route("/group/<int:group_id>/join", methods=["POST"])
def join_group(group_id):
    user_id = session.get("user_id")

    if user_id is None:
        return redirect(url_for("login"))

    # 1. Connect to DB
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    group = conn.execute("""
        SELECT *
        FROM study_groups
        WHERE id = ?
    """, (group_id,)).fetchone()

    if group is None:
        conn.close()
        return "Study group not found", 404

    member_count = conn.execute("""
        SELECT COUNT(*)
        FROM group_memberships
        WHERE group_id = ?
    """, (group_id,)).fetchone()[0]

    already_joined = conn.execute("""
        SELECT 1
        FROM group_memberships
        WHERE group_id = ? AND user_id = ?
    """, (group_id, user_id)).fetchone()

    if already_joined is not None:
        conn.close()
        return "User already joined this group", 400

    display_status = derive_display_status(
        group["status"], member_count, group["max_members"],
        group["meeting_date"], group["meeting_time"]
    )
    if display_status != "Open":
        conn.close()
        return f"Study group is {display_status.lower()}", 400

    conn.execute("""
        INSERT INTO group_memberships (user_id, group_id)
        VALUES (?, ?)
    """, (user_id, group_id))

    conn.commit()
    conn.close()

    return redirect(url_for("group_details", group_id=group_id))


@app.route("/group/<int:group_id>/leave", methods=["POST"])
def leave_group(group_id):
    user_id = session.get("user_id")

    if user_id is None:
        return redirect(url_for("login"))

    # 3. Connect to database
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    # 4. Check that the group exists
    group = conn.execute("""
        SELECT *
        FROM study_groups
        WHERE id = ?
    """, (group_id,)).fetchone()

    if group is None:
        conn.close()
        return "Study group not found", 404

    # 5. Check whether the user is actually a member
    membership = conn.execute("""
        SELECT 1
        FROM group_memberships
        WHERE group_id = ? AND user_id = ?
    """, (group_id, user_id)).fetchone()

    # 6. If they aren't a member -> return an error
    if membership is None:
        conn.close()
        return "User is not a member of this group", 400

    # 7. delete their membership
    conn.execute("""
        DELETE FROM group_memberships
        WHERE group_id = ? AND user_id = ?
    """, (group_id, user_id))

    # 8. Commit
    conn.commit()

    # 9. Close database
    conn.close()

    # 10. Redirect based on where the user left the group
    if request.form.get("next") == "my_groups":
        return redirect(url_for("my_groups"))

    return redirect(url_for("group_details", group_id=group_id))


from search_query import GROUP_STATUSES, MEETING_TYPES, normalize_course_code


@app.route("/my-groups", methods=["GET"])
def my_groups():
    # my-groups-filters
    user_id = session.get("user_id")

    if user_id is None:
        return redirect(url_for("login"))

    conn = get_db_connection()

    rows = conn.execute("""
        SELECT
            sg.*,
            (SELECT COUNT(*) FROM group_memberships gm2
             WHERE gm2.group_id = sg.id) AS current_members
        FROM study_groups sg
        JOIN group_memberships gm ON sg.id = gm.group_id
        WHERE gm.user_id = ?
    """, (user_id,)).fetchall()

    conn.close()

    all_groups = []
    for row in rows:
        group = dict(row)
        group["display_status"] = derive_display_status(
            row["status"], row["current_members"], row["max_members"],
            row["meeting_date"], row["meeting_time"]
        )
        group["is_creator"] = row["creator_user_id"] == user_id
        all_groups.append(group)

    courses = sorted({group["course_code"] for group in all_groups})

    status = (request.args.get("status") or "").strip().title()
    meeting_type = (request.args.get("meeting_type") or "").strip()
    course = (request.args.get("course") or "").strip()
    created_by_me = (request.args.get("created_by_me") or "").lower() in (
        "on", "true", "1", "yes"
    )

    # Values that are not on the lists are ignored instead of causing an error.
    if status not in GROUP_STATUSES:
        status = ""
    if meeting_type not in MEETING_TYPES:
        meeting_type = ""

    groups = all_groups
    if status:
        groups = [g for g in groups if g["display_status"] == status]
    if meeting_type:
        groups = [g for g in groups if g["meeting_type"] == meeting_type]
    if course:
        wanted = normalize_course_code(course)
        groups = [g for g in groups if normalize_course_code(g["course_code"]) == wanted]
    if created_by_me:
        groups = [g for g in groups if g["is_creator"]]

    return render_template(
        "my_groups.html",
        groups=groups,
        total_count=len(all_groups),
        courses=courses,
        group_statuses=GROUP_STATUSES,
        meeting_types=MEETING_TYPES,
        filters={
            "status": status,
            "meeting_type": meeting_type,
            "course": course,
            "created_by_me": created_by_me,
        },
    )


@app.route("/availability", methods=["GET", "POST"])
def availability():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    time_slots = [
        {"label": "9–10 AM", "start": "09:00", "end": "10:00"},
        {"label": "10–11 AM", "start": "10:00", "end": "11:00"},
        {"label": "11 AM–12 PM", "start": "11:00", "end": "12:00"},
        {"label": "12–1 PM", "start": "12:00", "end": "13:00"},
        {"label": "1–2 PM", "start": "13:00", "end": "14:00"},
        {"label": "2–3 PM", "start": "14:00", "end": "15:00"},
        {"label": "3–4 PM", "start": "15:00", "end": "16:00"},
        {"label": "4–5 PM", "start": "16:00", "end": "17:00"},
        {"label": "5–6 PM", "start": "17:00", "end": "18:00"},
        {"label": "6–7 PM", "start": "18:00", "end": "19:00"},
        {"label": "7–8 PM", "start": "19:00", "end": "20:00"},
    ]

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    message = None

    if request.method == "POST":
        selected = request.form.getlist("availability")

        # Replace the user's old availability with the newly selected schedule.
        conn.execute(
            "DELETE FROM user_availability WHERE user_id = ?",
            (user_id,)
        )

        for slot in selected:
            try:
                day, start_time, end_time = slot.split("|")
            except ValueError:
                continue

            # Only save values that came from our approved grid.
            valid_slot = (
                day in days
                and any(
                    item["start"] == start_time and item["end"] == end_time
                    for item in time_slots
                )
            )

            if valid_slot:
                conn.execute(
                    """
                    INSERT INTO user_availability
                    (user_id, day_of_week, start_time, end_time)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, day, start_time, end_time)
                )

        conn.commit()
        message = "Your availability has been saved."

    saved_rows = conn.execute(
        """
        SELECT day_of_week, start_time, end_time
        FROM user_availability
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchall()

    selected_slots = {
        f"{row['day_of_week']}|{row['start_time']}|{row['end_time']}"
        for row in saved_rows
    }

    conn.close()

    return render_template(
        "availability.html",
        days=days,
        time_slots=time_slots,
        selected_slots=selected_slots,
        message=message
    )

@app.route("/schedule", methods=["GET", "POST"])
def schedule():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    # Only groups the logged-in student belongs to can be scheduled.
    groups = conn.execute(
        """
        SELECT sg.id, sg.group_name, sg.course_code
        FROM study_groups sg
        JOIN group_memberships gm ON gm.group_id = sg.id
        WHERE gm.user_id = ?
        ORDER BY sg.course_code, sg.group_name
        """,
        (user_id,)
    ).fetchall()
    message = None
    error = None

    if request.method == "POST":
        group_id = request.form.get("group_id", "").strip()
        title = request.form.get("title", "").strip()
        session_date = request.form.get("session_date", "").strip()
        start_time = request.form.get("start_time", "").strip()
        end_time = request.form.get("end_time", "").strip()
        recurrence = request.form.get("recurrence", "none").strip()
        recurrence_end_date = request.form.get(
            "recurrence_end_date", ""
        ).strip()

        # Make sure all required fields were provided.
        if not group_id or not title or not session_date or not start_time or not end_time:
            error = "Please complete all required session fields."

        elif recurrence not in ("none", "daily", "weekly"):
            error = "Invalid recurrence option."

        elif recurrence in ("daily", "weekly") and not recurrence_end_date:
            error = "Please select an end date for the recurring session."

        elif (
            recurrence in ("daily", "weekly")
            and recurrence_end_date < session_date
        ):
            error = "The repeat-until date cannot be before the session date."
        elif (
    recurrence in ("daily", "weekly")
    and (
        datetime.strptime(recurrence_end_date, "%Y-%m-%d")
        - datetime.strptime(session_date, "%Y-%m-%d")
    ).days > 365
):
            error = "Recurring study sessions cannot extend more than one year."

        elif end_time <= start_time:
            error = "The session end time must be after the start time."
            
        elif datetime.strptime(
            f"{session_date} {start_time}",
            "%Y-%m-%d %H:%M"
        ) <= datetime.now():
            error = "The study session must be scheduled for a future date and time."

        else:
            # A student may only schedule a session for a group they belong to.
            membership = conn.execute(
                """
                SELECT 1
                FROM group_memberships
                WHERE user_id = ? AND group_id = ?
                """,
                (user_id, group_id)
            ).fetchone()

            if membership is None:
                error = "You must be a member of this group to schedule a session."

            else:
                # Build every date on which the new session would occur.
                proposed_dates = []

                current_date = datetime.strptime(
                    session_date, "%Y-%m-%d"
                )

                if recurrence == "none":
                    proposed_dates.append(session_date)

                else:
                    repeat_until = datetime.strptime(
                        recurrence_end_date, "%Y-%m-%d"
                    )

                    if recurrence == "daily":
                        step = timedelta(days=1)
                    else:
                        step = timedelta(weeks=1)

                    while (
                        current_date <= repeat_until
                        and current_date <= datetime.strptime(
                        session_date, "%Y-%m-%d"
                        ) + timedelta(days=365)
                    ):
                        proposed_dates.append(
                            current_date.strftime("%Y-%m-%d")
                        )
                        current_date += step
                        conn.execute("BEGIN IMMEDIATE")
                        # Get all scheduled sessions from groups this student belongs to.
                        existing_sessions = conn.execute(
                        """
                        SELECT
                            ss.session_date,
                            ss.start_time,
                            ss.end_time,
                            ss.recurrence,
                            ss.recurrence_end_date
                            FROM study_sessions ss
                            JOIN group_memberships gm
                            ON gm.group_id = ss.group_id
                            WHERE gm.user_id = ?
                            AND ss.status = 'scheduled'
                        """,
                        (user_id,)
                ).fetchall()
                group_meetings = conn.execute(
                    """
                    SELECT
                        sg.meeting_date,
                        sg.meeting_time
                    FROM study_groups sg
                    JOIN group_memberships gm
                        ON gm.group_id = sg.id
                    WHERE gm.user_id = ?
                    """,
                    (user_id,)
                ).fetchall()
                
                conflict_found = False

                for group_meeting in group_meetings:
                    meeting_data = dict(group_meeting)

                    if (
                        meeting_data["meeting_date"] in proposed_dates
                        and start_time <= meeting_data["meeting_time"] < end_time
                    ):
                        conflict_found = True
                        break

                for existing_session in existing_sessions:
                    existing_data = dict(existing_session)

                    existing_start_date = datetime.strptime(
                        existing_data["session_date"], "%Y-%m-%d"
                    )

                    existing_recurrence = existing_data["recurrence"]
                    existing_end_date = existing_data["recurrence_end_date"]

                    # Build the occurrence dates for this existing session.
                    existing_dates = []

                    if existing_recurrence == "none" or not existing_end_date:
                        existing_dates.append(
                            existing_data["session_date"]
                        )
                    else:
                        repeat_until = datetime.strptime(
                            existing_end_date, "%Y-%m-%d"
                        )

                        if existing_recurrence == "daily":
                            existing_step = timedelta(days=1)
                        elif existing_recurrence == "weekly":
                            existing_step = timedelta(weeks=1)
                        else:
                            existing_step = None

                        if existing_step is not None:
                            current_existing_date = existing_start_date

                            while (
                                current_existing_date <= repeat_until
                                and current_existing_date <= existing_start_date + timedelta(days=365)
                            ):
                                existing_dates.append(
                                    current_existing_date.strftime("%Y-%m-%d")
                                )
                                current_existing_date += existing_step

                    # A conflict exists when the dates match and the times overlap.
                    for proposed_date in proposed_dates:
                        if (
                            proposed_date in existing_dates
                            and start_time < existing_data["end_time"]
                            and end_time > existing_data["start_time"]
                        ):
                            conflict_found = True
                            break

                    if conflict_found:
                        break

                if conflict_found:
                    conn.rollback()
                    error = (
                        "You already have a study session that overlaps "
                        "with this time."
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO study_sessions (
                            group_id,
                            created_by_user_id,
                            title,
                            session_date,
                            start_time,
                            end_time,
                            recurrence,
                            recurrence_end_date
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            group_id,
                            user_id,
                            title,
                            session_date,
                            start_time,
                            end_time,
                            recurrence,
                            recurrence_end_date or None,
                        )
                    )

                    conn.commit()
                    message = "Study session scheduled successfully."

    # Sessions from groups the student belongs to.
    sessions = conn.execute(
        """
        SELECT
            ss.id,
            ss.group_id,
            ss.title,
            ss.session_date,
            ss.start_time,
            ss.end_time,
            ss.recurrence,
            ss.recurrence_end_date,
            ss.status,
            sg.group_name,
            sg.course_code
        FROM study_sessions ss
        JOIN study_groups sg ON sg.id = ss.group_id
        JOIN group_memberships gm ON gm.group_id = ss.group_id
        WHERE gm.user_id = ?
          AND ss.status = 'scheduled'
        ORDER BY ss.session_date, ss.start_time
        """,
        (user_id,)
    ).fetchall()

            # Expand recurring sessions into individual occurrences for display
    cancelled_occurrences = conn.execute(
        """
        SELECT session_id, occurrence_date
        FROM cancelled_session_occurrences
        """
    ).fetchall()

    cancelled_occurrence_set = {
        (row["session_id"], row["occurrence_date"])
        for row in cancelled_occurrences
    }

    edited_occurrences = conn.execute(
        """
        SELECT *
        FROM edited_session_occurrences
        """
    ).fetchall()

    edited_occurrence_map = {
        (
            row["session_id"],
            row["original_occurrence_date"]
        ): dict(row)
        for row in edited_occurrences
    }

    session_occurrences = []

    for study_session in sessions:
        session_data = dict(study_session)

        start_date = datetime.strptime(
            session_data["session_date"], "%Y-%m-%d"
        )

        recurrence = session_data["recurrence"]
        recurrence_end = session_data["recurrence_end_date"]

        # One-time session
        if recurrence == "none" or not recurrence_end:
            session_start = datetime.strptime(
                f"{session_data['session_date']} {session_data['start_time']}",
                "%Y-%m-%d %H:%M"
            )

            if session_start > datetime.now():
                session_occurrences.append(session_data)

            continue

        end_date = datetime.strptime(
            recurrence_end, "%Y-%m-%d"
        )

        if recurrence == "daily":
            step = timedelta(days=1)
        elif recurrence == "weekly":
            step = timedelta(weeks=1)
        else:
            session_occurrences.append(session_data)
            continue

        current_date = start_date

        while (
            current_date <= end_date
            and current_date <= start_date + timedelta(days=365)
        ):
            occurrence_date = current_date.strftime("%Y-%m-%d")

            if (
                session_data["id"],
                occurrence_date
            ) not in cancelled_occurrence_set:

                edit_key = (
                    session_data["id"],
                    occurrence_date
                )

                if edit_key in edited_occurrence_map:
                    edited_data = edited_occurrence_map[edit_key]

                    occurrence = session_data.copy()
                    occurrence["title"] = edited_data["title"]
                    occurrence["session_date"] = edited_data["session_date"]
                    occurrence["start_time"] = edited_data["start_time"]
                    occurrence["end_time"] = edited_data["end_time"]
                    occurrence["original_occurrence_date"] = occurrence_date
                else:
                    occurrence = session_data.copy()
                    occurrence["session_date"] = occurrence_date
                    occurrence["original_occurrence_date"] = occurrence_date

                occurrence_start = datetime.strptime(
                    f"{occurrence['session_date']} {occurrence['start_time']}",
                    "%Y-%m-%d %H:%M"
                )

                if occurrence_start > datetime.now():
                    session_occurrences.append(occurrence)

            current_date += step

    session_occurrences.sort(
        key=lambda item: (
            item["session_date"],
            item["start_time"]
        )
    )
    grouped_sessions = {}
    # Keep recurring series visible even when all occurrences are cancelled.
    for study_session in sessions:
        session_data = dict(study_session)

        if session_data["recurrence"] in ("daily", "weekly"):
            grouped_sessions[session_data["id"]] = {
                "session": session_data.copy(),
                "occurrences": []
            }

    for occurrence in session_occurrences:
        session_id = occurrence["id"]

    if session_id not in grouped_sessions:
        grouped_sessions[session_id] = {
            "session": occurrence.copy(),
            "occurrences": []
        }

    grouped_sessions[session_id]["occurrences"].append(occurrence)

    session_groups = list(grouped_sessions.values())
    

    conn.close()

    return render_template(
        "schedule.html",
        groups=groups,
        sessions=session_occurrences,
        session_groups=session_groups,
        message=message,
        error=error
    )

@app.route("/schedule/<int:session_id>/cancel", methods=["POST"])
def cancel_study_session(session_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    study_session = conn.execute(
        """
        SELECT id, created_by_user_id
        FROM study_sessions
        WHERE id = ?
          AND status = 'scheduled'
        """,
        (session_id,)
    ).fetchone()

    if study_session is None:
        conn.close()
        return redirect(url_for("schedule"))

    if study_session["created_by_user_id"] != user_id:
        conn.close()
        return redirect(url_for("schedule"))

    conn.execute(
        """
        UPDATE study_sessions
        SET status = 'cancelled'
        WHERE id = ?
        """,
        (session_id,)
    )
    conn.commit()
    conn.close()

    return redirect(url_for("schedule"))

@app.route(
    "/schedule/<int:session_id>/cancel-occurrence/<occurrence_date>",
    methods=["POST"]
)
def cancel_session_occurrence(session_id, occurrence_date):
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    study_session = conn.execute(
        """
        SELECT id, created_by_user_id, recurrence
        FROM study_sessions
        WHERE id = ?
          AND status = 'scheduled'
        """,
        (session_id,)
    ).fetchone()

    if study_session is None:
        conn.close()
        return redirect(url_for("schedule"))

    if study_session["created_by_user_id"] != user_id:
        conn.close()
        return redirect(url_for("schedule"))

    if study_session["recurrence"] == "none":
        conn.close()
        return redirect(url_for("schedule"))

    conn.execute(
        """
        INSERT OR IGNORE INTO cancelled_session_occurrences (
            session_id,
            occurrence_date
        )
        VALUES (?, ?)
        """,
        (session_id, occurrence_date)
    )

    conn.commit()
    conn.close()

    return redirect(url_for("schedule"))

@app.route("/schedule/<int:session_id>/edit", methods=["GET"])
def edit_study_session(session_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    study_session = conn.execute(
        """
        SELECT
            ss.*,
            sg.group_name,
            sg.course_code
        FROM study_sessions ss
        JOIN study_groups sg ON sg.id = ss.group_id
        WHERE ss.id = ?
          AND ss.status = 'scheduled'
          AND ss.created_by_user_id = ?
        """,
        (session_id, user_id)
    ).fetchone()

    conn.close()

    if study_session is None:
        return redirect(url_for("schedule"))

    return render_template(
        "edit_study_session.html",
        study_session=study_session
    )

@app.route("/schedule/<int:session_id>/update", methods=["POST"])
def update_study_session(session_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    title = request.form.get("title", "").strip()
    session_date = request.form.get("session_date", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()
    recurrence = request.form.get("recurrence", "none").strip()
    recurrence_end_date = request.form.get(
        "recurrence_end_date", ""
    ).strip()

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    study_session = conn.execute(
        """
        SELECT *
        FROM study_sessions
        WHERE id = ?
          AND created_by_user_id = ?
          AND status = 'scheduled'
        """,
        (session_id, user_id)
    ).fetchone()

    if study_session is None:
        conn.close()
        return redirect(url_for("schedule"))

    if not title or not session_date or not start_time or not end_time:
        conn.close()
        return "Please complete all required session fields.", 400

    if recurrence not in ("none", "daily", "weekly"):
        conn.close()
        return "Invalid recurrence option.", 400

    if recurrence in ("daily", "weekly") and not recurrence_end_date:
        conn.close()
        return "Please select an end date for the recurring session.", 400

    if (
        recurrence in ("daily", "weekly")
        and recurrence_end_date < session_date
    ):
        conn.close()
        return "The repeat-until date cannot be before the session date.", 400

    if (
        recurrence in ("daily", "weekly")
        and (
            datetime.strptime(recurrence_end_date, "%Y-%m-%d")
            - datetime.strptime(session_date, "%Y-%m-%d")
        ).days > 365
    ):
        conn.close()
        return "Recurring study sessions cannot extend more than one year.", 400

    if end_time <= start_time:
        conn.close()
        return "The session end time must be after the start time.", 400

    session_start = datetime.strptime(
        f"{session_date} {start_time}",
        "%Y-%m-%d %H:%M"
    )

    if session_start <= datetime.now():
        conn.close()
        return (
            "The study session must be scheduled for a future date and time.",
            400
        )

    # Build all proposed dates for the edited session.
    proposed_dates = []
    current_date = datetime.strptime(session_date, "%Y-%m-%d")

    if recurrence == "none":
        proposed_dates.append(session_date)
    else:
        repeat_until = datetime.strptime(
            recurrence_end_date, "%Y-%m-%d"
        )

        if recurrence == "daily":
            step = timedelta(days=1)
        else:
            step = timedelta(weeks=1)

        while (
            current_date <= repeat_until
            and current_date <= datetime.strptime(
            session_date, "%Y-%m-%d"
            ) + timedelta(days=365)
        ):
            proposed_dates.append(
                current_date.strftime("%Y-%m-%d")
            )
            current_date += step

    # Check the user's OTHER sessions for conflicts.
    conn.execute("BEGIN IMMEDIATE")
    existing_sessions = conn.execute(
        """
        SELECT
            ss.id,
            ss.session_date,
            ss.start_time,
            ss.end_time,
            ss.recurrence,
            ss.recurrence_end_date
        FROM study_sessions ss
        JOIN group_memberships gm
            ON gm.group_id = ss.group_id
        WHERE gm.user_id = ?
          AND ss.status = 'scheduled'
          AND ss.id != ?
        """,
        (user_id, session_id)
    ).fetchall()
    group_meetings = conn.execute(
        """
        SELECT
            sg.meeting_date,
            sg.meeting_time
        FROM study_groups sg
        JOIN group_memberships gm
            ON gm.group_id = sg.id
        WHERE gm.user_id = ?
        """,
        (user_id,)
    ).fetchall()

    conflict_found = False

    for group_meeting in group_meetings:
        meeting_data = dict(group_meeting)

        if (
            meeting_data["meeting_date"] in proposed_dates
            and start_time <= meeting_data["meeting_time"] < end_time
        ):
            conflict_found = True
            break

    for existing_session in existing_sessions:
        existing_data = dict(existing_session)
        existing_dates = []

        existing_start_date = datetime.strptime(
            existing_data["session_date"],
            "%Y-%m-%d"
        )

        existing_recurrence = existing_data["recurrence"]
        existing_end_date = existing_data["recurrence_end_date"]

        if existing_recurrence == "none" or not existing_end_date:
            existing_dates.append(existing_data["session_date"])
        else:
            repeat_until = datetime.strptime(
                existing_end_date,
                "%Y-%m-%d"
            )

            if existing_recurrence == "daily":
                existing_step = timedelta(days=1)
            elif existing_recurrence == "weekly":
                existing_step = timedelta(weeks=1)
            else:
                existing_step = None

            if existing_step is not None:
                current_existing_date = existing_start_date

                while (
                    current_existing_date <= repeat_until
                    and current_existing_date <= existing_start_date + timedelta(days=365)
                ):
                    existing_dates.append(
                        current_existing_date.strftime("%Y-%m-%d")
                    )
                    current_existing_date += existing_step

        for proposed_date in proposed_dates:
            if (
                proposed_date in existing_dates
                and start_time < existing_data["end_time"]
                and end_time > existing_data["start_time"]
            ):
                conflict_found = True
                break

        if conflict_found:
            break

    if conflict_found:
        conn.rollback()
        conn.close()
        return (
            "You already have a study session that overlaps with this time.",
            400
        )

    if proposed_dates:
        cancelled_rows = conn.execute(
            """
            SELECT occurrence_date
            FROM cancelled_session_occurrences
            WHERE session_id = ?
            """,
            (session_id,)
        ).fetchall()

        for row in cancelled_rows:
            if row["occurrence_date"] not in proposed_dates:
                conn.execute(
                    """
                    DELETE FROM cancelled_session_occurrences
                    WHERE session_id = ?
                    AND occurrence_date = ?
                  """,
                  (session_id, row["occurrence_date"])
                )
    if proposed_dates:
        edited_rows = conn.execute(
            """
            SELECT original_occurrence_date
            FROM edited_session_occurrences
            WHERE session_id = ?
            """,
            (session_id,)
        ).fetchall()

        for row in edited_rows:
            if row["original_occurrence_date"] not in proposed_dates:
                conn.execute(
                    """
                    DELETE FROM edited_session_occurrences
                    WHERE session_id = ?
                    AND original_occurrence_date = ?
                    """,
                    (session_id, row["original_occurrence_date"])
                )

    conn.execute(
        """
        UPDATE study_sessions
        SET title = ?,
            session_date = ?,
            start_time = ?,
            end_time = ?,
            recurrence = ?,
            recurrence_end_date = ?
        WHERE id = ?
        """,
        (
            title,
            session_date,
            start_time,
            end_time,
            recurrence,
            recurrence_end_date or None,
            session_id,
        )
    )

    conn.commit()
    conn.close()

    return redirect(url_for("schedule"))

@app.route(
    "/schedule/<int:session_id>/edit-occurrence/<occurrence_date>",
    methods=["GET"]
)
def edit_session_occurrence(session_id, occurrence_date):
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    study_session = conn.execute(
        """
        SELECT
            ss.*,
            sg.group_name,
            sg.course_code
        FROM study_sessions ss
        JOIN study_groups sg ON sg.id = ss.group_id
        WHERE ss.id = ?
          AND ss.status = 'scheduled'
          AND ss.created_by_user_id = ?
        """,
        (session_id, user_id)
    ).fetchone()

    if study_session is None:
        conn.close()
        return redirect(url_for("schedule"))

    if study_session["recurrence"] == "none":
        conn.close()
        return redirect(url_for("schedule"))

    edited_occurrence = conn.execute(
        """
        SELECT *
        FROM edited_session_occurrences
        WHERE session_id = ?
          AND original_occurrence_date = ?
        """,
        (session_id, occurrence_date)
    ).fetchone()

    conn.close()

    return render_template(
        "edit_session_occurrence.html",
        study_session=study_session,
        occurrence_date=occurrence_date,
        edited_occurrence=edited_occurrence
    )
@app.route(
    "/schedule/<int:session_id>/update-occurrence/<occurrence_date>",
    methods=["POST"]
)
def update_session_occurrence(session_id, occurrence_date):
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    title = request.form.get("title", "").strip()
    session_date = request.form.get("session_date", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    study_session = conn.execute(
        """
        SELECT *
        FROM study_sessions
        WHERE id = ?
          AND created_by_user_id = ?
          AND status = 'scheduled'
        """,
        (session_id, user_id)
    ).fetchone()

    if study_session is None:
        conn.close()
        return redirect(url_for("schedule"))

    if study_session["recurrence"] == "none":
        conn.close()
        return redirect(url_for("schedule"))

    if not title or not session_date or not start_time or not end_time:
            conn.close()
            return "Please complete all required session fields.", 400

    if end_time <= start_time:
        conn.close()
        return "The session end time must be after the start time.", 400

    session_start = datetime.strptime(
        f"{session_date} {start_time}",
        "%Y-%m-%d %H:%M"
    )

    if session_start <= datetime.now():
        conn.close()
        return (
            "The study session must be scheduled for a future date and time.",
            400
        )
    conn.execute("BEGIN IMMEDIATE")

    # Check the user's other scheduled sessions for a conflict.
    existing_sessions = conn.execute(
        """
        SELECT
            ss.id,
            ss.session_date,
            ss.start_time,
            ss.end_time,
            ss.recurrence,
            ss.recurrence_end_date
        FROM study_sessions ss
        JOIN group_memberships gm
            ON gm.group_id = ss.group_id
        WHERE gm.user_id = ?
  AND ss.status = 'scheduled'
""",
(user_id,)
    ).fetchall()
    group_meetings = conn.execute(
        """
        SELECT
            sg.meeting_date,
            sg.meeting_time
        FROM study_groups sg
        JOIN group_memberships gm
            ON gm.group_id = sg.id
        WHERE gm.user_id = ?
        """,
        (user_id,)
    ).fetchall()
    cancelled_rows = conn.execute(
        """
        SELECT session_id, occurrence_date
        FROM cancelled_session_occurrences
        """
    ).fetchall()

    cancelled_occurrences = {
        (row["session_id"], row["occurrence_date"])
        for row in cancelled_rows
    }
    edited_rows = conn.execute(
        """
        SELECT
            session_id,
            original_occurrence_date,
            session_date,
            start_time,
            end_time
        FROM edited_session_occurrences
        """
    ).fetchall()

    edited_occurrences = {
        (row["session_id"], row["original_occurrence_date"]): dict(row)
        for row in edited_rows
    }

    conflict_found = False
    for group_meeting in group_meetings:
        meeting_data = dict(group_meeting)

        if (
            meeting_data["meeting_date"] == session_date
            and start_time <= meeting_data["meeting_time"] < end_time
        ):
            conflict_found = True
            break

    for existing_session in existing_sessions:
        existing_data = dict(existing_session)
        existing_dates = []

        existing_start_date = datetime.strptime(
            existing_data["session_date"],
            "%Y-%m-%d"
        )

        existing_recurrence = existing_data["recurrence"]
        existing_end_date = existing_data["recurrence_end_date"]

        if existing_recurrence == "none" or not existing_end_date:
            existing_dates.append(existing_data["session_date"])
        else:
            repeat_until = datetime.strptime(
                existing_end_date,
                "%Y-%m-%d"
            )

            if existing_recurrence == "daily":
                existing_step = timedelta(days=1)
            elif existing_recurrence == "weekly":
                existing_step = timedelta(weeks=1)
            else:
                existing_step = None

            if existing_step is not None:
                current_existing_date = existing_start_date

                while (
                    current_existing_date <= repeat_until
                    and current_existing_date <= existing_start_date + timedelta(days=365)
                ):
                    existing_dates.append(
                        current_existing_date.strftime("%Y-%m-%d")
                    )
                    current_existing_date += existing_step
        # Canceled occurrences should not count as scheduling conflicts.
        existing_dates = [
            date
            for date in existing_dates
            if (existing_data["id"], date) not in cancelled_occurrences
        ]
                # Check individually edited/moved occurrences using their new
        # date and time instead of their original occurrence.
        for original_date in list(existing_dates):
            edit_key = (existing_data["id"], original_date)

            if edit_key in edited_occurrences:
                edited = edited_occurrences[edit_key]

                if (
                    edited["session_date"] == session_date
                    and start_time < edited["end_time"]
                    and end_time > edited["start_time"]
                ):
                    # Do not compare the occurrence currently being edited
                    # against its own saved edit.
                    if not (
                        existing_data["id"] == session_id
                        and original_date == occurrence_date
                    ):
                        conflict_found = True
                        break

                existing_dates.remove(original_date)

        if conflict_found:
            break
        # When checking this same recurring series, ignore only the
        # original occurrence currently being edited.
        if existing_data["id"] == session_id:
            existing_dates = [
                date
                for date in existing_dates
                if date != occurrence_date
            ]

        if (
            session_date in existing_dates
            and start_time < existing_data["end_time"]
            and end_time > existing_data["start_time"]
        ):
            conflict_found = True
            break

    if conflict_found:
        conn.rollback()
        conn.close()
        return (
            "You already have a study session that overlaps with this time.",
            400
        )

    conn.execute(
        """
        INSERT INTO edited_session_occurrences (
            session_id,
            original_occurrence_date,
            title,
            session_date,
            start_time,
            end_time
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id, original_occurrence_date)
        DO UPDATE SET
            title = excluded.title,
            session_date = excluded.session_date,
            start_time = excluded.start_time,
            end_time = excluded.end_time
        """,
        (
            session_id,
            occurrence_date,
            title,
            session_date,
            start_time,
            end_time,
        )
    )

    conn.commit()
    conn.close()

    return redirect(url_for("schedule"))


@app.route("/group/<int:group_id>/heatmap")
def availability_heatmap(group_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    user_id = session["user_id"]

    membership = conn.execute(
        """
        SELECT 1
        FROM group_memberships
        WHERE group_id = ? AND user_id = ?
        """,
        (group_id, user_id)
    ).fetchone()

    if membership is None:
        conn.close()
        return redirect(url_for("group_details", group_id=group_id))

    members = conn.execute(
        """
        SELECT u.id, u.name
        FROM group_memberships gm
        JOIN users u ON u.id = gm.user_id
        WHERE gm.group_id = ?
        """,
        (group_id,)
    ).fetchall()

    availability_rows = conn.execute(
        """
        SELECT ua.user_id, ua.day_of_week, ua.start_time, ua.end_time
        FROM user_availability ua
        JOIN group_memberships gm ON gm.user_id = ua.user_id
        WHERE gm.group_id = ?
        """,
        (group_id,)
    ).fetchall()
    conn.close()

    members_with_availability = len({
        row["user_id"] for row in availability_rows
    })

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    time_slots = [
        {"label": "9–10 AM", "start": "09:00", "end": "10:00"},
        {"label": "10–11 AM", "start": "10:00", "end": "11:00"},
        {"label": "11 AM–12 PM", "start": "11:00", "end": "12:00"},
        {"label": "12–1 PM", "start": "12:00", "end": "13:00"},
        {"label": "1–2 PM", "start": "13:00", "end": "14:00"},
        {"label": "2–3 PM", "start": "14:00", "end": "15:00"},
        {"label": "3–4 PM", "start": "15:00", "end": "16:00"},
        {"label": "4–5 PM", "start": "16:00", "end": "17:00"},
        {"label": "5–6 PM", "start": "17:00", "end": "18:00"},
        {"label": "6–7 PM", "start": "18:00", "end": "19:00"},
        {"label": "7–8 PM", "start": "19:00", "end": "20:00"},
    ]

    heatmap = {}

    for day in days:
        for slot in time_slots:
            count = sum(
                1
                for row in availability_rows
                if row["day_of_week"] == day
                and row["start_time"] == slot["start"]
                and row["end_time"] == slot["end"]
            )

            heatmap[(day, slot["start"], slot["end"])] = count

    return render_template(
        "availability_heatmap.html",
        group_id=group_id,
        members=members,
        days=days,
        time_slots=time_slots,
        heatmap=heatmap,
        total_members=len(members),
        members_with_availability=members_with_availability
    )


@app.route("/study-request", methods=["GET", "POST"])
def study_request():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    study_goals = [
        "Exam",
        "Assignment / Project",
        "Final Exam",
        "Weekly Study",
        "Other"
    ]

    meeting_preferences = [
        "In-Person",
        "Online",
        "Hybrid"
    ]

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    availability_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM user_availability
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()["count"]

    error = None

    if request.method == "POST":
        course_code = request.form.get("course_code", "").strip().upper()
        section = request.form.get("section", "").strip()
        study_goal = request.form.get("study_goal", "").strip()
        meeting_preference = request.form.get(
            "meeting_preference", ""
        ).strip()
        note = request.form.get("note", "").strip()

        if not course_code or not study_goal or not meeting_preference:
            error = "Please complete all required fields."

        elif study_goal not in study_goals:
            error = "Please select a valid study goal."

        elif meeting_preference not in meeting_preferences:
            error = "Please select a valid meeting preference."

        elif availability_count == 0:
            error = (
                "Please add your weekly availability before "
                "finding study matches."
            )

        else:
            cursor = conn.execute(
                """
                INSERT INTO study_requests
                (
                    user_id,
                    course_code,
                    section,
                    study_goal,
                    meeting_preference,
                    note,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    user_id,
                    course_code,
                    section,
                    study_goal,
                    meeting_preference,
                    note
                )
            )

            conn.commit()
            request_id = cursor.lastrowid
            conn.close()

            return redirect(
                url_for("study_matches", request_id=request_id)
            )

    conn.close()

    return render_template(
        "study_request.html",
        study_goals=study_goals,
        meeting_preferences=meeting_preferences,
        availability_count=availability_count,
        error=error,
        form=request.form
    )


@app.route("/study-matches/<int:request_id>")
def study_matches(request_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    # Get this user's Study Request.
    study_request = get_user_study_request(conn, request_id, user_id)

    if study_request is None:
        conn.close()
        return "Study Request not found.", 404

    # Matching rules live in matching.py so the dashboard uses the same ones.
    partner_matches = find_partner_matches(conn, user_id, study_request)
    group_matches = find_group_matches(conn, study_request)

    conn.close()

    return render_template(
        "study_matches.html",
        study_request=study_request,
        partner_matches=partner_matches,
        group_matches=group_matches
    )


@app.route("/study-request/<int:request_id>/close", methods=["POST"])
def close_study_request(request_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    study_request = get_user_study_request(conn, request_id, user_id)

    if study_request is None:
        conn.close()
        return "Study Request not found.", 404

    conn.execute(
        "UPDATE study_requests SET status = 'closed' WHERE id = ?",
        (request_id,)
    )
    conn.commit()
    conn.close()

    return redirect(url_for("dashboard.dashboard"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not name or not email or not password or not confirm_password:
            return "Name, email, password, and confirm password are required", 400
        
        UTA_EMAIL_DOMAIN = "@mavs.uta.edu"
        if not email.endswith(UTA_EMAIL_DOMAIN):
            return (
                f"Please register with your UTA student email address "
                f"(must end in {UTA_EMAIL_DOMAIN})",
                400
            )

        if password != confirm_password:
            return "Password and confirmation do not match", 400

        conn = get_db_connection()
        conn.row_factory = sqlite3.Row

        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()

        if existing is not None:
            conn.close()
            return "An account with that email already exists", 400

        hashed_password = generate_password_hash(password)

        try:
            conn.execute(
                "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                (name, email, hashed_password),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            conn.close()
            return "An account with that email already exists", 400

        conn.commit()
        conn.close()

        return redirect(url_for("login"))

    return render_template("register.html")



@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return "Email and password are required", 400

        email = email.strip().lower()

        conn = get_db_connection()
        conn.row_factory = sqlite3.Row

        user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        conn.close()

        if user is None or not check_password_hash(user["password"], password):
            return "Invalid email or password", 400

        session["user_id"] = user["id"]
        session["user_name"] = user["name"]

        return redirect(url_for("dashboard.dashboard"))

    return render_template("login.html")



@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))



if __name__ == "__main__":
    init_db()
    create_test_user()
    app.run(debug=True)