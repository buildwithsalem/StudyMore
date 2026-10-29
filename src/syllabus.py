"""Syllabus upload for a semester: read the file, let the student review every
date, then add the checked ones (plus an optional study plan) to the semester.

The uploaded file is read in memory and never stored."""

from datetime import date

from flask import Blueprint, redirect, render_template, request, session, url_for

from db import get_db_connection
from semesters import (
    ITEM_TYPES,
    MAX_TITLE,
    ensure_semester_schema,
    get_owned_semester,
    parse_date,
    user_courses,
)
from student_profile import normalize_course
from syllabus_parse import (
    MAX_ROWS,
    SyllabusError,
    build_study_plan,
    detect_course,
    extract_text,
    parse_syllabus,
)
from syllabus_bridge import parse_pdf_bytes

syllabus_bp = Blueprint("syllabus", __name__, template_folder="templates")

MAX_PASTE = 200_000


def _render_upload(semester, courses, error=None, status=200):
    return (
        render_template(
            "syllabus_upload.html",
            semester=semester,
            courses=courses,
            error=error,
            form=request.form,
        ),
        status,
    )


@syllabus_bp.route("/semesters/<int:semester_id>/syllabus", methods=["GET", "POST"])
def syllabus_upload(semester_id):
    user_id = session.get("user_id")
    if user_id is None:
        return redirect(url_for("login"))

    conn = get_db_connection()
    ensure_semester_schema(conn)
    sem = get_owned_semester(conn, semester_id, user_id)
    if sem is None:
        return "Semester not found", 404
    semester = dict(sem)
    courses = user_courses(conn, user_id)

    if request.method == "GET":
        return _render_upload(semester, courses)

    upload = request.files.get("syllabus")
    pasted = (request.form.get("pasted_text") or "")[:MAX_PASTE]
    pdf_bytes = None
    try:
        if upload is not None and upload.filename:
            data = upload.read()
            text = extract_text(upload.filename, data)
            if upload.filename.lower().endswith(".pdf"):
                pdf_bytes = data
        elif pasted.strip():
            text = pasted
        else:
            raise SyllabusError("Choose a syllabus file or paste the schedule text.")
    except SyllabusError as exc:
        return _render_upload(semester, courses, str(exc), 400)

    start, end = parse_date(semester["start_date"]), parse_date(semester["end_date"])

    # PDFs go through the new reader first; if it finds nothing (or fails),
    # fall back to the old text reader so nothing that worked before breaks.
    rows = []
    if pdf_bytes:
        rows, _warnings = parse_pdf_bytes(pdf_bytes, start, end)
    if not rows:
        rows = parse_syllabus(text, start, end)

    # Course: what the student typed, else the code found in the syllabus
    # (matched to their profile courses when possible).
    typed = (request.form.get("course_code") or "").strip()
    detected = detect_course(text)
    course_default = ""
    if typed:
        course_default = normalize_course(typed)
    elif detected:
        wanted = normalize_course(detected)
        match = next((c for c in courses if normalize_course(c) == wanted), None)
        course_default = normalize_course(match or detected)

    if not rows:
        return _render_upload(
            semester,
            courses,
            "No dates inside this semester were found. Check that the semester "
            "dates are right, or paste just the schedule part of the syllabus.",
            200,
        )

    return render_template(
        "syllabus_review.html",
        semester=semester,
        rows=rows,
        item_types=ITEM_TYPES,
        course_default=course_default,
        sem_start=semester["start_date"],
        sem_end=semester["end_date"],
        max_title=MAX_TITLE,
    )


@syllabus_bp.route("/semesters/<int:semester_id>/syllabus/confirm", methods=["POST"])
def syllabus_confirm(semester_id):
    user_id = session.get("user_id")
    if user_id is None:
        return redirect(url_for("login"))

    conn = get_db_connection()
    ensure_semester_schema(conn)
    sem = get_owned_semester(conn, semester_id, user_id)
    if sem is None:
        return "Semester not found", 404
    sem_start, sem_end = parse_date(sem["start_date"]), parse_date(sem["end_date"])

    existing = {
        ((r["title"] or "").lower(), r["due_date"], r["course_code"] or "")
        for r in conn.execute(
            "SELECT title, due_date, course_code FROM semester_items "
            "WHERE semester_id = ? AND user_id = ?",
            (semester_id, user_id),
        )
    }

    count = min(max(request.form.get("count", type=int, default=0), 0), MAX_ROWS)
    chosen = []
    for i in range(count):
        if not request.form.get(f"keep-{i}"):
            continue
        title = " ".join((request.form.get(f"title-{i}") or "").split())
        item_type = (request.form.get(f"type-{i}") or "").strip()
        due = parse_date(request.form.get(f"date-{i}"))
        raw_course = (request.form.get(f"course-{i}") or "").strip()
        if not title or len(title) > MAX_TITLE or item_type not in ITEM_TYPES:
            continue
        if due is None or not sem_start <= due <= sem_end:
            continue
        chosen.append(
            {
                "title": title,
                "due_date": due.isoformat(),
                "item_type": item_type,
                "course_code": normalize_course(raw_course) if raw_course else "",
                "notes": "From your syllabus",
            }
        )

    to_add = [(item, "syllabus") for item in chosen]
    if request.form.get("add_plan"):
        plan = build_study_plan(chosen, sem_start, sem_end, date.today())
        to_add += [(item, "plan") for item in plan]

    added = 0
    for item, source in to_add:
        key = (item["title"].lower(), item["due_date"], item["course_code"])
        if key in existing:
            continue
        existing.add(key)
        conn.execute(
            "INSERT INTO semester_items "
            "(semester_id, user_id, course_code, title, item_type, due_date, notes, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                semester_id, user_id, item["course_code"], item["title"],
                item["item_type"], item["due_date"], item["notes"], source,
            ),
        )
        added += 1
    conn.commit()

    if not added:
        return redirect(
            url_for(
                "semesters.semester_view",
                semester_id=semester_id,
                error="Nothing new was added. Rows that were unchecked, "
                "invalid or already in your semester are skipped.",
            )
        )
    return redirect(url_for("semesters.semester_view", semester_id=semester_id, view="semester"))