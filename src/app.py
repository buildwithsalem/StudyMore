from flask import Flask, render_template, request
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
        group_name = request.form["group_name"]
        course_code = request.form["course_code"]
        section = request.form["section"]
        description = request.form["description"]
        meeting_type = request.form["meeting_type"]
        meeting_date = request.form["meeting_date"]
        meeting_time = request.form["meeting_time"]
        location = request.form["location"]
        meeting_link = request.form["meeting_link"]
        max_members = request.form["max_members"]
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

    return render_template("create_group.html")

if __name__ == "__main__":
    init_db()
    app.run(debug=True)