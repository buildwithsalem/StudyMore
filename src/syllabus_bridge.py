"""Runs the new PDF reader and returns rows in the shape the review page expects."""
import datetime
import io

from syllabus_parse import MAX_ROWS, MAX_TITLE, guess_kind
from syllabus_parser import parse_syllabus as read_pdf


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_pdf_bytes(data, start, end, section=None):
    """Returns (rows, warnings). Empty rows means: use the old reader instead."""
    try:
        out = read_pdf(io.BytesIO(data), default_year=start.year, section=section)
    except Exception:
        return [], []
    rows, seen = [], set()
    for it in out.get("items", []):
        when = _as_date(it.get("date"))
        title = " ".join(str(it.get("title") or "").split())[:MAX_TITLE]
        if when is None or not title or not start <= when <= end:
            continue
        key = (title.lower(), when.isoformat())
        if key in seen:
            continue
        seen.add(key)
        kind = guess_kind(title)  # always a valid item type
        rows.append({"title": title, "due_date": when.isoformat(),
                     "item_type": kind, "selected": kind != "Other"})
    rows.sort(key=lambda r: (r["due_date"], r["title"].lower()))
    return rows[:MAX_ROWS], out.get("warnings", [])
