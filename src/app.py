from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
from search import search_bp
from dashboard import dashboard_bp
from db import get_db_connection, init_app as init_database_config
from matching import (
    find_group_matches,
    find_partner_matches,
    get_user_study_request,
)
from validators import safe_meeting_url, validate_meeting_link

import sqlite3

app = Flask(__name__)
# One database setting (app.config["DATABASE"]) shared by every route and blueprint.
init_database_config(app)
# Only http(s) meeting links are ever rendered as hrefs, even for old rows.
app.add_template_filter(safe_meeting_url, "safe_meeting_url")

import os
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))
app.register_blueprint(search_bp)
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

    conn.close()

    return render_template(
        "group_details.html",
        group=group,
        member_count=member_count,
        is_member=is_member,
        is_creator=is_creator
    )


@app.route("/group/<int:group_id>/join", methods=["POST"])
def join_group(group_id):
    user_id = session.get("user_id")

    if user_id is None:
        return redirect(url_for("login"))

    # 1. Connect to DB
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    # 2. Find group
    group = conn.execute("""
        SELECT *
        FROM study_groups
        WHERE id = ?
    """, (group_id,)).fetchone()

    # 3. If group doesn't exist -> 404
    if group is None:
        conn.close()
        return "Study group not found", 404

    # 4. Count members
    member_count = conn.execute("""
        SELECT COUNT(*)
        FROM group_memberships
        WHERE group_id = ?
    """, (group_id,)).fetchone()[0]

    # 5. Check if user already joined
    already_joined = conn.execute("""
        SELECT 1
        FROM group_memberships
        WHERE group_id = ? AND user_id = ?
    """, (group_id, user_id)).fetchone()

    if already_joined is not None:
        conn.close()
        return "User already joined this group", 400

    # 6. Check if group is full
    if member_count >= group["max_members"]:
        conn.close()
        return "Study group is full", 400

    # 7. Check status
    if group["status"] != "open":
        conn.close()
        return "Study group is not open", 400

    # 8. INSERT membership
    conn.execute("""
        INSERT INTO group_memberships (user_id, group_id)
        VALUES (?, ?)
    """, (user_id, group_id))

    # 9. Commit
    conn.commit()

    # 10. Close DB
    conn.close()

    # 11. Redirect to group details
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


@app.route("/my-groups", methods=["GET"])
def my_groups():
    user_id = session.get("user_id")

    if user_id is None:
        return redirect(url_for("login"))   

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    groups = conn.execute("""
        SELECT study_groups.*
        FROM study_groups
        JOIN group_memberships ON study_groups.id = group_memberships.group_id
        WHERE group_memberships.user_id = ?
    """, (user_id,)).fetchall()

    conn.close()

    return render_template("my_groups.html", groups=groups)

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

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not name or not email or not password or not confirm_password:
            return "Name, email, password, and confirm password are required", 400

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
