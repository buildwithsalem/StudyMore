from flask import Flask, render_template, request, redirect, url_for

import sqlite3

app = Flask(__name__)


def init_db():
    conn = sqlite3.connect("study_more.db")
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

    conn.commit()
    conn.close()


def create_test_user():
    conn = sqlite3.connect("study_more.db")

    conn.execute("""
        INSERT OR IGNORE INTO users (name, email, password)
        VALUES (?, ?, ?)
    """, ("Test User", "test@example.com", "test123"))

    conn.commit()
    conn.close()



@app.route("/", methods=["GET", "POST"])
def create_group():
    if request.method == "POST":
        group_name = request.form.get("group_name")
        course_code = request.form.get("course_code")
        section = request.form.get("section")
        description = request.form.get("description")
        meeting_type = request.form.get("meeting_type")
        meeting_date = request.form.get("meeting_date")
        meeting_time = request.form.get("meeting_time")
        location = request.form.get("location")
        meeting_link = request.form.get("meeting_link")
        max_members = request.form.get("max_members")

        if any(value is None for value in (
            group_name, course_code, section, description, meeting_type,
            meeting_date, meeting_time, location, meeting_link, max_members
        )):
            return "Missing required form field", 400

        try: 
            max_members = int(max_members)
        except (TypeError, ValueError):
            return "Max members must be a number", 400

        if max_members < 2:
            return "Max members must be at least 2", 400
        if meeting_type == "In-Person" and not location.strip():
            return "Location is required for in-person groups", 400
        if meeting_type == "Online" and not meeting_link.strip():
                    return "Meeting link is required for online groups", 400
        if meeting_type == "Hybrid" and (not location.strip() or not meeting_link.strip()):
                            return "Location and meeting link are required for hybrid groups", 400
        
        
        connection = sqlite3.connect("study_more.db")

        connection.execute("""
            INSERT INTO study_groups (
            group_name,
            course_code,
            section,
            description,
            meeting_type,
            meeting_date,
            meeting_time,
            location,
            meeting_link,
            max_members
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        group_name,
        course_code,
        section, 
        description,
        meeting_type,
        meeting_date,
        meeting_time,
        location,
        meeting_link,
        max_members
    ))
        connection.commit()
        connection.close()

        print("Group Name:", group_name)
        print("Course Code:", course_code)
        print("Meeting Type:", meeting_type)

        return redirect(url_for("create_group"))
    
    return render_template("create_group.html")



@app.route("/group/<int:group_id>")
def group_details(group_id):
    conn = sqlite3.connect("study_more.db")
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

    conn.close()

    return render_template(
        "group_details.html",
        group=group,
        member_count=member_count
    )


@app.route("/group/<int:group_id>/join", methods=["POST"])
def join_group(group_id):
    user_id = request.form.get("user_id")

    if user_id is None:
        return "Missing user_id", 400

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return "user_id must be a number", 400

    # 1. Connect to DB
    conn = sqlite3.connect("study_more.db")
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
    # 1. Get user_id from the form
    user_id = request.form.get("user_id")

    # 2. Validate user_id
    if user_id is None:
        return "Missing user_id", 400

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return "user_id must be a number", 400

    # 3. Connect to database
    conn = sqlite3.connect("study_more.db")
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

    # 7. DELETE their membership
    conn.execute("""
        DELETE FROM group_memberships
        WHERE group_id = ? AND user_id = ?
    """, (group_id, user_id))

    # 8. Commit
    conn.commit()

    # 9. Close database
    conn.close()

    # 10. Redirect back to group details
    return redirect(url_for("group_details", group_id=group_id))



@app.route("/my-groups", methods=["GET"])
def my_groups():
    # For now, hardcode the user
    user_id = 1

    conn = sqlite3.connect("study_more.db")
    conn.row_factory = sqlite3.Row

    groups = conn.execute("""
        SELECT study_groups.*
        FROM study_groups
        JOIN group_memberships ON study_groups.id = group_memberships.group_id
        WHERE group_memberships.user_id = ?
    """, (user_id,)).fetchall()

    conn.close()

    return render_template("my_groups.html", groups=groups)



if __name__ == "__main__":
    init_db()
    create_test_user()
    app.run(debug=True)
