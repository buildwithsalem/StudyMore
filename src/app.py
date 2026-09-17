from flask import Flask, render_template, request, redirect, url_for
import sqlite3
app = Flask(__name__)
def init_db():
    connection = sqlite3.connect("study_more.db")

    connection.execute("""
    CREATE TABLE IF NOT EXISTS study_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    status TEXT NOT NULL DEFAULT 'open'
    )
    """)

    connection.close()

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

init_db()

if __name__ == "__main__":
    app.run(debug=True)