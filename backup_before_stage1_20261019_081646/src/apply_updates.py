"""
Adds the Profile tab and My Groups filters to app.py, and adds the Profile
link and the Poppins font to every page.

Run from the repo root:   python src/apply_updates.py
Safe to run twice. A backup is saved as app.py.bak.
"""

import re
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
APP = SRC / "app.py"
TEMPLATES = SRC / "templates"

FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '    <link href="https://fonts.googleapis.com/css2?family=Poppins:'
    'wght@400;500;600;700;800&display=swap" rel="stylesheet">\n'
    '    '
)
STYLE_LINK = re.compile(
    r"""<link rel="stylesheet" href="\{\{ url_for\('static', filename='style\.css'\) \}\}">"""
)
AVAILABILITY_LINK = re.compile(
    r"""^(\s*)<a [^>]*url_for\('availability'\)[^>]*>Availability</a>\s*$"""
)
PROFILE_LINK = """<a href="{{ url_for('profile.profile') }}">Profile</a>"""

NEW_MY_GROUPS = '''from search_query import GROUP_STATUSES, MEETING_TYPES, normalize_course_code


@app.route("/my-groups", methods=["GET"])
def my_groups():
    # my-groups-filters
    user_id = session.get("user_id")

    if user_id is None:
        return redirect(url_for("login"))

    conn = sqlite3.connect("study_more.db")
    conn.row_factory = sqlite3.Row

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


'''


def apply_templates():
    nav_added = fonts_added = 0
    for path in sorted(TEMPLATES.glob("*.html")):
        text = path.read_text()
        original = text

        if "profile.profile" not in text:
            out = []
            for line in text.split("\n"):
                out.append(line)
                match = AVAILABILITY_LINK.match(line)
                if match:
                    out.append(match.group(1) + PROFILE_LINK)
            text = "\n".join(out)
            if text != original:
                nav_added += 1

        if "fonts.googleapis.com" not in text and STYLE_LINK.search(text):
            text = STYLE_LINK.sub(lambda m: FONT_LINKS + m.group(0), text, count=1)
            fonts_added += 1

        if text != original:
            path.write_text(text)
    print(f"templates: Profile link added to {nav_added} page(s), font added to {fonts_added} page(s)")


def apply_app():
    text = APP.read_text()
    backup = APP.with_name("app.py.bak")
    if not backup.exists():
        shutil.copy2(APP, backup)
    notes = []

    if "student_profile" not in text:
        marker = "app.register_blueprint(search_bp)"
        if marker in text:
            text = text.replace(
                marker,
                marker + "\nfrom student_profile import profile_bp\n"
                "app.register_blueprint(profile_bp)",
                1,
            )
            notes.append("app.py: Profile blueprint registered")
        else:
            notes.append(
                "MANUAL STEP: add these two lines to app.py after your other "
                "register_blueprint lines:\n"
                "    from student_profile import profile_bp\n"
                "    app.register_blueprint(profile_bp)"
            )
    else:
        notes.append("app.py: Profile blueprint already registered")

    if "my-groups-filters" not in text:
        pattern = re.compile(
            r'@app\.route\("/my-groups"[^\n]*\)\s*\ndef my_groups\(\):.*?'
            r'(?=\n@app\.route\(|\nif __name__|\Z)',
            re.S,
        )
        if (
            pattern.search(text)
            and "derive_display_status" in text
            and "import sqlite3" in text
        ):
            text = pattern.sub(lambda m: NEW_MY_GROUPS.rstrip() + "\n\n", text, count=1)
            notes.append("app.py: My Groups filters installed")
        else:
            notes.append(
                "MANUAL STEP: could not find my_groups() in app.py. "
                "Send me the function and I will adapt the filters to it."
            )
    else:
        notes.append("app.py: My Groups filters already installed")

    try:
        compile(text, str(APP), "exec")
    except SyntaxError as error:
        print(f"STOPPED: the edited app.py has a syntax error ({error}). Nothing was changed.")
        return False

    APP.write_text(text)
    for note in notes:
        print(note)
    return True


def main():
    if not APP.exists():
        print(f"Could not find {APP}. Run this from the repo root: python src/apply_updates.py")
        sys.exit(1)
    if not apply_app():
        sys.exit(1)
    apply_templates()
    print("Done. Now paste the CSS into src/static/style.css, then run the tests.")


if __name__ == "__main__":
    main()