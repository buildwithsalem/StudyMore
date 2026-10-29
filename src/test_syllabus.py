"""Tests for syllabus reading and the syllabus upload routes."""

import io
import sqlite3
import zipfile
from datetime import date

import pytest

from syllabus_parse import (
    SyllabusError,
    build_study_plan,
    detect_course,
    extract_text,
    parse_syllabus,
)

START, END = date(2026, 8, 24), date(2026, 12, 12)


def by_title(rows):
    return {r["title"]: r for r in rows}


# ------------------------------------------------------------------ parser

def test_finds_common_date_styles():
    text = (
        "Sep 14 Quiz 1\n"
        "9/21 Homework 2 due 11:59 PM\n"
        "Sept. 28, 2026 Midterm Exam\n"
        "2026-12-03 Final project presentation\n"
        "Dec 9 Final Exam\n"
    )
    rows = by_title(parse_syllabus(text, START, END))
    assert rows["Quiz 1"]["due_date"] == "2026-09-14"
    assert rows["Homework 2 due"]["due_date"] == "2026-09-21"
    assert rows["Midterm Exam"]["due_date"] == "2026-09-28"
    assert rows["Final project presentation"]["item_type"] == "Project"
    assert rows["Final Exam"]["item_type"] == "Exam"


def test_ignores_dates_outside_semester_and_no_class_lines():
    text = "Jan 5 Exam\nSep 7 Labor Day - No class\nNov 25 Thanksgiving break\n"
    assert parse_syllabus(text, START, END) == []


def test_lowercase_may_is_not_a_date():
    text = "You may 2 attempts on each quiz.\n"
    assert parse_syllabus(text, date(2026, 1, 12), date(2026, 5, 8)) == []


def test_date_range_keeps_first_day_and_clean_title():
    rows = parse_syllabus("Oct 12-14 Group project proposal due", START, END)
    assert rows[0]["due_date"] == "2026-10-12"
    assert rows[0]["title"] == "Group project proposal due"


def test_date_alone_on_a_line_uses_next_line_as_title():
    rows = parse_syllabus("Oct 20\nProject milestone 2\n", START, END)
    assert rows[0]["title"] == "Project milestone 2"
    assert rows[0]["due_date"] == "2026-10-20"


def test_unchecked_by_default_when_no_deadline_word():
    rows = parse_syllabus("Aug 26 Intro to software engineering\nSep 14 Quiz 1\n", START, END)
    flags = {r["title"]: r["selected"] for r in rows}
    assert flags["Intro to software engineering"] is False
    assert flags["Quiz 1"] is True


def test_winter_semester_crossing_new_year():
    rows = parse_syllabus("Dec 20 Exam\nJan 8 Quiz\n", date(2026, 12, 14), date(2027, 1, 15))
    dates = {r["title"]: r["due_date"] for r in rows}
    assert dates["Exam"] == "2026-12-20"
    assert dates["Quiz"] == "2027-01-08"


def test_duplicates_removed_and_row_cap():
    assert len(parse_syllabus("Sep 14 Quiz 1\nSep 14 Quiz 1\n", START, END)) == 1
    many = "\n".join(f"Oct {d} Quiz {d}" for d in range(1, 29)) * 5
    assert len(parse_syllabus(many, START, END)) <= 80


def test_detect_course_skips_seasons():
    assert detect_course("Fall 2026 Syllabus\nCSE 3310 Software Engineering") == "CSE 3310"
    assert detect_course("FALL 2026 only") == ""


# ------------------------------------------------------------- file reading

def make_docx(paragraphs, table_rows=()):
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    for row in table_rows:
        cells = "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row)
        body += f"<w:tbl><w:tr>{cells}</w:tr></w:tbl>"
    xml = f'<?xml version="1.0"?><w:document {ns}><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_reads_docx_paragraphs_and_tables():
    data = make_docx(["CSE 3310"], table_rows=[["Week 5", "Sep 28", "Midterm Exam"]])
    text = extract_text("syllabus.docx", data)
    assert "CSE 3310" in text
    rows = parse_syllabus(text, START, END)
    assert rows[0]["title"].endswith("Midterm Exam")
    assert rows[0]["due_date"] == "2026-09-28"


def test_reads_txt():
    assert "Quiz" in extract_text("s.txt", b"Sep 14 Quiz 1")


@pytest.mark.parametrize(
    "name,data",
    [
        ("notes.exe", b"x"),
        ("s.docx", b"not a zip"),
        ("s.txt", b"   \n  "),
        ("big.txt", b"a" * (5 * 1024 * 1024 + 1)),
    ],
)
def test_bad_files_raise_friendly_error(name, data):
    with pytest.raises(SyllabusError):
        extract_text(name, data)


def test_docx_with_entities_is_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY a "b">]><x/>')
    with pytest.raises(SyllabusError):
        extract_text("s.docx", buf.getvalue())


# --------------------------------------------------------------- study plan

def test_study_plan_rules_and_skips_past_dates():
    items = [
        {"title": "Midterm", "due_date": "2026-10-10", "item_type": "Exam", "course_code": "CSE3310"},
        {"title": "Quiz 3", "due_date": "2026-10-10", "item_type": "Quiz", "course_code": "CSE3310"},
        {"title": "Reading", "due_date": "2026-10-10", "item_type": "Reading", "course_code": ""},
    ]
    plan = build_study_plan(items, START, END, today=date(2026, 10, 5))
    got = {(p["due_date"], p["title"]) for p in plan}
    assert ("2026-10-07", "Practice and review for Midterm") in got
    assert ("2026-10-09", "Final review for Midterm") in got
    assert ("2026-10-08", "Review for Quiz 3") in got
    assert not any(d < "2026-10-05" for d, _ in got)  # 7 days before is in the past
    assert all(p["item_type"] == "Other" for p in plan)


# ------------------------------------------------------------------- routes

@pytest.fixture
def ctx(tmp_path):
    from app import app, create_test_user, init_db
    from semesters import ensure_semester_schema

    path = str(tmp_path / "test.db")
    app.config["DATABASE"] = path
    app.config["TESTING"] = True
    init_db(path)
    create_test_user(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_semester_schema(conn)
    uid = conn.execute("SELECT id FROM users WHERE email = 'test@example.com'").fetchone()["id"]
    conn.execute(
        "INSERT INTO semesters (user_id, name, term, year, start_date, end_date, is_active) "
        "VALUES (?, 'Fall 2026', 'Fall', 2026, '2026-08-24', '2026-12-12', 1)", (uid,)
    )
    conn.commit()
    sem_id = conn.execute("SELECT id FROM semesters").fetchone()["id"]
    conn.close()
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = uid
        s["user_name"] = "Test User"
    return client, path, sem_id


def items(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM semester_items ORDER BY due_date, title").fetchall()
    conn.close()
    return rows


def post_text(client, sem_id, text):
    return client.post(
        f"/semesters/{sem_id}/syllabus",
        data={"pasted_text": text},
        content_type="multipart/form-data",
    )


def test_upload_page_requires_login(ctx):
    client, _, sem_id = ctx
    other = client.application.test_client()
    resp = other.get(f"/semesters/{sem_id}/syllabus")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_other_users_semester_is_404(ctx):
    client, _, sem_id = ctx
    assert client.get(f"/semesters/{sem_id + 99}/syllabus").status_code == 404


def test_upload_shows_review_page_and_adds_nothing_yet(ctx):
    client, path, sem_id = ctx
    resp = post_text(client, sem_id, "CSE 3310\nSep 28 Midterm Exam\nDec 9 Final Exam\n")
    assert resp.status_code == 200
    assert b"Midterm Exam" in resp.data and b"Check what we found" in resp.data
    assert items(path) == []


def test_empty_submission_and_no_dates_are_friendly(ctx):
    client, _, sem_id = ctx
    assert post_text(client, sem_id, "   ").status_code == 400
    resp = post_text(client, sem_id, "Nothing with dates in here")
    assert b"No dates inside this semester" in resp.data


def test_bad_extension_rejected(ctx):
    client, _, sem_id = ctx
    resp = client.post(
        f"/semesters/{sem_id}/syllabus",
        data={"syllabus": (io.BytesIO(b"x"), "evil.exe")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_confirm_adds_checked_rows_and_study_plan(ctx):
    client, path, sem_id = ctx
    resp = client.post(
        f"/semesters/{sem_id}/syllabus/confirm",
        data={
            "count": "2",
            "keep-0": "1", "title-0": "Midterm Exam", "type-0": "Exam",
            "date-0": "2026-11-20", "course-0": "CSE 3310",
            "title-1": "Skipped row", "type-1": "Other", "date-1": "2026-09-01",
            "add_plan": "1",
        },
    )
    assert resp.status_code == 302
    rows = items(path)
    sources = {r["source"] for r in rows}
    titles = [r["title"] for r in rows]
    assert "Midterm Exam" in titles and "Skipped row" not in titles
    assert sources == {"syllabus", "plan"}


def test_confirm_rejects_bad_rows_and_dedupes(ctx):
    client, path, sem_id = ctx
    good = {
        "count": "3",
        "keep-0": "1", "title-0": "Quiz 1", "type-0": "Quiz", "date-0": "2026-09-14",
        "keep-1": "1", "title-1": "Outside", "type-1": "Exam", "date-1": "2027-03-01",
        "keep-2": "1", "title-2": "Bad type", "type-2": "Nope", "date-2": "2026-09-15",
    }
    client.post(f"/semesters/{sem_id}/syllabus/confirm", data=good)
    client.post(f"/semesters/{sem_id}/syllabus/confirm", data=good)  # again
    assert [r["title"] for r in items(path)] == ["Quiz 1"]


def test_confirm_requires_ownership(ctx):
    client, path, sem_id = ctx
    resp = client.post(
        f"/semesters/{sem_id + 99}/syllabus/confirm",
        data={"count": "1", "keep-0": "1", "title-0": "X", "type-0": "Exam", "date-0": "2026-09-14"},
    )
    assert resp.status_code == 404
    assert items(path) == []