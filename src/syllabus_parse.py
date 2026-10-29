"""Syllabus reading: turn an uploaded syllabus into a list of dated deadlines.

Pure Python (no Flask, no database) so it is easy to test. The routes that use
it live in syllabus.py.

Supported inputs: .txt, .docx (read with the standard library) and .pdf (needs
the optional `pypdf` package; without it the student is told to paste the text).
"""

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta

try:  # optional dependency
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - depends on the server
    PdfReader = None

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_PDF_PAGES = 40
MAX_ROWS = 80
MAX_TITLE = 120
ALLOWED_EXTENSIONS = (".pdf", ".docx", ".txt")


class SyllabusError(Exception):
    """A problem the student can fix; the message is safe to show them."""


# ------------------------------------------------------------ reading files

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_paragraph(p):
    parts = []
    for node in p.iter():
        if node.tag == _W + "t" and node.text:
            parts.append(node.text)
        elif node.tag in (_W + "tab", _W + "br"):
            parts.append(" ")
    return "".join(parts).strip()


def _docx_walk(node, out):
    for child in node:
        if child.tag == _W + "p":
            out.append(_docx_paragraph(child))
        elif child.tag == _W + "tbl":
            for row in child.iter(_W + "tr"):
                cells = []
                for cell in row.findall(_W + "tc"):
                    cells.append(" ".join(_docx_paragraph(p) for p in cell.iter(_W + "p")).strip())
                out.append(" | ".join(cells))
        else:
            _docx_walk(child, out)


def _docx_text(data):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        info = archive.getinfo("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise SyllabusError("That does not look like a Word .docx file.")
    if info.file_size > 15 * 1024 * 1024:
        raise SyllabusError("That Word file is too large to read.")
    xml = archive.read("word/document.xml")
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise SyllabusError("That Word file could not be read safely.")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise SyllabusError("That Word file could not be read.")
    body = root.find(_W + "body")
    out = []
    _docx_walk(body if body is not None else root, out)
    return "\n".join(out)


def _pdf_text(data):
    if PdfReader is None:
        raise SyllabusError(
            "Reading PDFs is not switched on for this server yet. Upload a "
            ".docx or .txt file, or paste the schedule text instead."
        )
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise SyllabusError("That PDF is password protected.")
        pages = reader.pages[:MAX_PDF_PAGES]
        return "\n".join((page.extract_text() or "") for page in pages)
    except SyllabusError:
        raise
    except Exception:
        raise SyllabusError("That PDF could not be read. Try pasting the text instead.")


def extract_text(filename, data):
    """Return the plain text of an uploaded file, or raise SyllabusError."""
    name = (filename or "").lower()
    if len(data) > MAX_FILE_BYTES:
        raise SyllabusError("That file is too large. Please use a file under 5 MB.")
    if name.endswith(".txt"):
        text = data.decode("utf-8", errors="replace")
    elif name.endswith(".docx"):
        text = _docx_text(data)
    elif name.endswith(".pdf"):
        text = _pdf_text(data)
    else:
        raise SyllabusError("Please upload a PDF, Word (.docx) or text (.txt) file.")
    text = text[:MAX_TEXT_CHARS]
    if not text.strip():
        raise SyllabusError(
            "No readable text was found. A scanned syllabus is a picture, so "
            "please paste the schedule text instead."
        )
    return text


# ------------------------------------------------------------ finding dates

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAMES = (
    "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|"
    "Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
DATE_RE = re.compile(
    r"(?P<iso>\b(?P<iy>\d{4})-(?P<im>\d{1,2})-(?P<id>\d{1,2})\b)"
    r"|(?P<named>\b(?P<nm>" + _MONTH_NAMES + r")\.?\s+(?P<nd>\d{1,2})(?:st|nd|rd|th)?\b"
    r"(?:,?\s+(?P<ny>\d{4})\b)?)"
    r"|(?P<num>(?<![\d/])(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{4}|\d{2}))?(?![\d/]))",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm|a\.m\.|p\.m\.)", re.IGNORECASE)
_WEEKDAY_RE = re.compile(
    r"\b(?:Mon(?:day)?|Tue(?:s(?:day)?)?|Wed(?:nesday)?|Thu(?:r(?:s(?:day)?)?)?|"
    r"Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\b\.?",
    re.IGNORECASE,
)
_RANGE_TAIL_RE = re.compile(r"[-\u2013\u2014]\d{1,2}(?:st|nd|rd|th)?\b(?![:/])")
_NOISE_RE = re.compile(
    r"\b(?:no class(?:es)?|holiday|spring break|fall break|thanksgiving|"
    r"university closed|campus closed|classes? (?:do not|don't) meet)\b",
    re.IGNORECASE,
)


def _resolve(month, day, year, start, end):
    if year is None:
        years = [start.year] + ([end.year] if end.year != start.year else [])
    else:
        years = [year + 2000 if year < 100 else year]
    for y in years:
        try:
            found = date(y, month, day)
        except ValueError:
            continue
        if start <= found <= end:
            return found
    return None


def _match_to_date(m, start, end):
    if m.group("iso"):
        return _resolve(int(m.group("im")), int(m.group("id")), int(m.group("iy")), start, end)
    if m.group("named"):
        word = m.group("nm")
        if word.lower() == "may" and not word[0].isupper():
            return None  # "may 2 attempts" is prose, not a date
        year = int(m.group("ny")) if m.group("ny") else None
        return _resolve(_MONTHS[word[:3].lower()], int(m.group("nd")), year, start, end)
    year = int(m.group("y")) if m.group("y") else None
    return _resolve(int(m.group("m")), int(m.group("d")), year, start, end)


_KIND_RULES = (
    ("Quiz", re.compile(r"\bquiz(?:zes)?\b", re.IGNORECASE)),
    ("Project", re.compile(r"\b(?:project|presentation|capstone|demo)\b", re.IGNORECASE)),
    ("Exam", re.compile(r"\b(?:midterm|mid-term|exam|final|test)\b", re.IGNORECASE)),
    ("Assignment", re.compile(
        r"\b(?:homework|hw|assignment|problem set|pset|lab|worksheet|essay|paper|due)\b",
        re.IGNORECASE)),
    ("Reading", re.compile(r"\b(?:reading|chapters?|ch\.)\b", re.IGNORECASE)),
)


def guess_kind(text):
    for kind, pattern in _KIND_RULES:
        if pattern.search(text):
            return kind
    return "Other"


def _clean_title(line, spans):
    pieces, last = [], 0
    for a, b in spans:
        pieces.append(line[last:a])
        last = b
    pieces.append(line[last:])
    text = " ".join(pieces)
    text = _TIME_RE.sub(" ", text)
    text = _WEEKDAY_RE.sub(" ", text)
    text = re.sub(r"[|\t]+", " - ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?:\s-\s?){2,}", " - ", text)
    text = text.strip(" -\u2013\u2014:|,;[]\u2022\u00b7*.")
    text = text.lstrip(")").strip()
    if text.count("(") > text.count(")"):
        text += ")"
    return text


def _expand_lines(text):
    """Split the text into lines; very long lines are split into sentences."""
    lines = []
    for raw in text.replace("\r", "\n").split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        if len(raw) > 240:
            lines.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", raw) if s.strip())
        else:
            lines.append(raw)
    return lines


def parse_syllabus(text, start, end):
    """Find dated items. Returns a list of dicts sorted by date:
    {title, due_date (ISO), item_type, selected}. Only dates that fall inside the
    semester (start..end) are kept."""
    lines = _expand_lines(text)
    found, seen = [], set()
    for idx, line in enumerate(lines):
        if _NOISE_RE.search(line):
            continue
        when, spans = None, []
        for m in DATE_RE.finditer(line):
            resolved = _match_to_date(m, start, end)
            if resolved is None:
                continue
            if when is None:
                when = resolved
            a, b = m.span()
            tail = _RANGE_TAIL_RE.match(line, b)  # "Oct 12-14": drop the "-14"
            spans.append((a, tail.end() if tail else b))
        if when is None:
            continue

        title = _clean_title(line, spans)
        if len(re.findall(r"[A-Za-z]", title)) < 3:
            nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
            if nxt and not DATE_RE.search(nxt) and not _NOISE_RE.search(nxt):
                title = _clean_title(nxt, [])
        kind = guess_kind(title or line)
        if len(re.findall(r"[A-Za-z]", title)) < 3:
            if kind == "Other":
                continue
            title = kind
        if len(title) > 60:  # a whole sentence: keep it short, the student can edit
            title = title[:60].rsplit(" ", 1)[0].rstrip(" ,;:-") + "\u2026"
        title = title[:MAX_TITLE].rstrip()

        key = (title.lower(), when.isoformat())
        if key in seen:
            continue
        seen.add(key)
        found.append(
            {"title": title, "due_date": when.isoformat(), "item_type": kind, "selected": kind != "Other"}
        )

    found.sort(key=lambda r: (r["due_date"], r["title"].lower()))
    typed = [r for r in found if r["selected"]][:MAX_ROWS]
    room = MAX_ROWS - len(typed)
    others = [r for r in found if not r["selected"]][: max(room, 0)]
    rows = typed + others
    rows.sort(key=lambda r: (r["due_date"], r["title"].lower()))
    return rows


# ---------------------------------------------------------- course detection

_NOT_COURSES = {
    "FALL", "SPRING", "SUMMER", "WINTER", "TERM", "SEMESTER", "YEAR", "PAGE",
    "ROOM", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
    "SEPT", "OCT", "NOV", "DEC", "COPYRIGHT",
}
_COURSE_RE = re.compile(r"\b([A-Z]{2,5})[ \u00a0-]?(\d{4})\b")


def detect_course(text):
    """First course-looking code such as 'CSE 3310' near the top, or ''."""
    for m in _COURSE_RE.finditer(text[:3000]):
        if m.group(1) not in _NOT_COURSES:
            return f"{m.group(1)} {m.group(2)}"
    return ""


# ---------------------------------------------------------------- study plan

_PLAN_RULES = {
    "Exam": ((7, "Start studying for {t}"), (3, "Practice and review for {t}"), (1, "Final review for {t}")),
    "Quiz": ((2, "Review for {t}"),),
    "Project": ((14, "Start {t}"), (7, "Draft check for {t}")),
}


def build_study_plan(items, sem_start, sem_end, today):
    """Study-prep reminders before exams, quizzes and projects.

    `items` are dicts with title, due_date (ISO), item_type, course_code.
    Reminders before today or outside the semester are left out."""
    earliest = max(sem_start, today)
    plan, seen = [], set()
    for item in items:
        rules = _PLAN_RULES.get(item["item_type"])
        if not rules:
            continue
        due = date.fromisoformat(item["due_date"])
        for days_before, template in rules:
            when = due - timedelta(days=days_before)
            if when < earliest or when > sem_end:
                continue
            title = template.format(t=item["title"])[:MAX_TITLE].rstrip()
            key = (title.lower(), when.isoformat())
            if key in seen:
                continue
            seen.add(key)
            plan.append(
                {
                    "title": title,
                    "due_date": when.isoformat(),
                    "item_type": "Other",
                    "course_code": item.get("course_code", ""),
                    "notes": "Added by your study plan",
                }
            )
    plan.sort(key=lambda r: (r["due_date"], r["title"].lower()))
    return plan