"""Reads a syllabus PDF and returns the dates a student should plan around.

Main entry point:
    result = parse_syllabus("syllabus.pdf", default_year=2026)

result is a dict:
    course, title, term_label, meetings, items, warnings
Each item: title, type (Exam/Quiz/Assignment/Other), date (YYYY-MM-DD),
           points (or None), source, confidence ("high" for schedule tables).

It reads the schedule TABLE (so it can tell what happens in class apart from what
is due), falls back to scanning the text for dated lines, and says so in
`warnings` whenever it had to guess. Nothing here writes to the database.

Command line check:   python3 syllabus_parser.py path/to/syllabus.pdf --year 2026
"""

import datetime
import re
import sys

MONTHS = {}
for number, names in enumerate(
    [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",),
     ("june", "jun"), ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"),
     ("october", "oct"), ("november", "nov"), ("december", "dec")], start=1):
    for name in names:
        MONTHS[name] = number

DATE_WORD = re.compile(r"^\s*([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\s*$")
DATE_NUM = re.compile(r"^\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*$")
BULLET = re.compile(r"^\s*(?:[\u2022\u00b7\u25cf*\-\u2013\u2014]|\d{1,2}[.)])\s+")
TIME = r"(\d{1,2}(?::\d{2})?\s*[AaPp]\.?[Mm]\.?)"


# ------------------------------------------------------------------ helpers

def parse_date_cell(text):
    """'September 04' / 'Oct 9, 2026' / '10/16' -> (month, day, year or None), else None."""
    text = (text or "").strip()
    match = DATE_WORD.match(text)
    if match and match.group(1).lower() in MONTHS:
        year = int(match.group(3)) if match.group(3) else None
        return MONTHS[match.group(1).lower()], int(match.group(2)), year
    match = DATE_NUM.match(text)
    if match:
        year = match.group(3)
        if year:
            year = int(year) + 2000 if len(year) == 2 else int(year)
        return int(match.group(1)), int(match.group(2)), year
    return None


def balance_parens(text):
    """Drop a dangling '(' tail so titles never end mid-bracket."""
    if text.count("(") > text.count(")"):
        text = text[: text.rfind("(")].rstrip(" -\u2013:,")
    return text


def tidy(text):
    text = re.sub(r"\s+", " ", (text or "").replace("\u2022", " ")).strip(" -\u2013\u2014:;,")
    return balance_parens(text)


def split_bullets(text):
    """Join wrapped lines; start a new entry at each bullet."""
    entries = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        if BULLET.match(line) or not entries:
            entries.append(BULLET.sub("", line))
        else:
            entries[-1] += " " + line
    return [tidy(e) for e in entries if tidy(e)]


def title_case_assignment(text):
    """'Assignment #3 \u2013 Video Speeches (Upload to Canvas)' -> 'Assignment #3: Video Speeches (Upload to Canvas)'."""
    match = re.match(r"^(assignment|homework|hw|project|lab|quiz|exam)\s*#?\s*(\d+)\s*[:\u2013\u2014-]?\s*(.*)$", text, re.I)
    if not match:
        return text
    kind = match.group(1).capitalize() if match.group(1).lower() not in ("hw",) else "Homework"
    head = f"{kind} #{match.group(2)}"
    rest = match.group(3)
    if not rest:
        return head
    return f"{head} {rest}" if rest.startswith("(") else f"{head}: {rest}"


def kind_of(text, in_due_column):
    low = text.lower()
    if re.search(r"\bmake-?\s?up\b", low) and "exam" in low:
        return "Exam"
    if re.search(r"\b(exam|mid-?term|final exam)\b", low) and "review" not in low:
        return "Exam"
    if re.search(r"\bquiz\b", low):
        return "Quiz"
    if in_due_column or re.search(r"\b(due|submit|submission|turn in)\b", low):
        return "Assignment"
    return None


def number_of(text):
    match = re.search(r"#\s*(\d+)|\b(?:exam|quiz|assig\w*|homework|project|lab)\s*(\d+)", text, re.I)
    if match:
        return int(match.group(1) or match.group(2))
    return None


# ------------------------------------------------------------------ points

def read_points(tables, text):
    """{('exam', 1): 20, ('quiz', 2): 5, ('assignment', 3): 10}. Tolerates 'assigment' typos."""
    points = {}
    for table in tables:
        for row in table:
            cells = [c.strip() for c in row if c]
            if len(cells) != 2 or not re.fullmatch(r"\d+(\.\d+)?", cells[1]):
                continue
            match = re.match(r"^(exam|mid-?term|quiz|assi\w*|homework|hw|project)\s*#?\s*(\d+)$", cells[0], re.I)
            if match:
                word = match.group(1).lower()
                kind = "exam" if word.startswith(("exam", "mid")) else "quiz" if word == "quiz" else "assignment"
                points[(kind, int(match.group(2)))] = float(cells[1]) if "." in cells[1] else int(cells[1])
    each = {}
    for word, value in re.findall(r"(Exams?|Quizzes|Assignments?)\s*\((\d+)\s*pts?\s*each\)", text, re.I):
        each[word.lower().rstrip("s").replace("quizze", "quiz")] = int(value)
    return points, each


def read_assignment_names(text):
    """{2: 'Life Long Learning', ...} from lines like 'Assignment #2: Life Long Learning (Student to ...)'."""
    names = {}
    for number, name in re.findall(r"Assignment\s*#\s*(\d+)\s*:\s*([^\n(]{3,70})", text):
        names.setdefault(int(number), tidy(name))
    return names


def points_for(kind, number, points, each):
    key = {"Exam": "exam", "Quiz": "quiz", "Assignment": "assignment"}.get(kind)
    if key is None:
        return None
    if number is not None and (key, number) in points:
        return points[(key, number)]
    return each.get(key) if number is not None else None


# ------------------------------------------------------------------ schedule

def schedule_rows(tables):
    """Yield (date_tuple, activities_text, due_text, class_no) for every dated row."""
    due_last = True
    for table in tables:
        for raw in table:
            cells = ["" if c is None else str(c) for c in raw]
            # merged cells come back as None; real empty cells are ''. Drop only the merged ones.
            kept = [("" if c is None else str(c)) for c in raw if c is not None]
            header = " ".join(kept).lower()
            if "date" in header and ("due" in header or "activit" in header or "lecture" in header):
                due_last = "due" in kept[-1].lower()
                continue
            index = next((i for i, c in enumerate(kept) if parse_date_cell(c)), None)
            if index is None:
                continue
            date = parse_date_cell(kept[index])
            rest = kept[index + 1:] + [c for c in kept[:index] if c.strip()]
            class_no = None
            for i, cell in enumerate(rest):
                if re.fullmatch(r"\d{1,2}", cell.strip()):
                    class_no = int(cell.strip())
                    del rest[i]
                    break
            if len(rest) >= 2 and due_last:
                activities, due = "\n".join(rest[:-1]), rest[-1]
            else:
                activities, due = "\n".join(rest), ""
            yield date, activities, due, class_no


def split_due(text):
    """'Assignment #3 \u2013 Video\\nSpeeches\\nAssignment #5' -> two entries."""
    flat = re.sub(r"\s*\n\s*", " ", (text or "").strip())
    if not flat:
        return []
    if re.search(r"assignment\s*#?\s*\d+", flat, re.I):
        parts = re.split(r"(?=assignment\s*#?\s*\d+)", flat, flags=re.I)
        return [tidy(p) for p in parts if tidy(p)]
    if BULLET.search(text):
        return split_bullets(text)
    return [tidy(flat)]


def resolve_year(date, running):
    month, day, year = date
    if year:
        return year, year
    if running["prev_month"] is not None and month < running["prev_month"] - 6:
        running["year"] += 1
    running["prev_month"] = month
    return running["year"], running["year"]


# ------------------------------------------------------------------ meetings, term

def read_meetings(text):
    meetings = []
    pattern = re.compile(r"\b(Mo|Tu|We|Th|Fr|Sa|Su|[MTWRF]{1,4})\s+" + TIME + r"\s*[-\u2013]\s*" + TIME + r"\s+([A-Z]{1,5}\s?\d{2,4}[A-Z]?)")
    for match in pattern.finditer(text):
        before = text[max(0, match.start() - 250): match.start()]
        sections = re.findall(r"-(\d{3})\b", before)
        meetings.append({
            "section": sections[-1] if sections else "",
            "days": match.group(1),
            "start": to_24h(match.group(2)),
            "end": to_24h(match.group(3)),
            "room": match.group(4),
        })
    return meetings


def to_24h(text):
    match = re.match(r"(\d{1,2})(?::(\d{2}))?\s*([AaPp])", text)
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if match.group(3).lower() == "p" and hour != 12:
        hour += 12
    if match.group(3).lower() == "a" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def guess_term(first_month):
    if first_month in (8, 9):
        return "Fall"
    if first_month in (1, 2):
        return "Spring"
    if first_month in (5, 6):
        return "Summer"
    return ""


# ------------------------------------------------------------------ text fallback

KEYWORDS = re.compile(r"\b(exam|midterm|mid-term|final|quiz|test|assignment|homework|project|paper|essay|presentation|due)\b", re.I)
DATE_IN_TEXT = re.compile(
    r"(?P<w>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?)"
    r"|(?P<n>\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b)")


def scan_text(text, year):
    found = []
    for line in text.split("\n"):
        if not KEYWORDS.search(line):
            continue
        match = DATE_IN_TEXT.search(line)
        if not match:
            continue
        date = parse_date_cell(match.group("w") or match.group("n"))
        if not date:
            continue
        title = tidy(line.replace(match.group(0), " "))[:90]
        found.append((date, title))
    return found


# ------------------------------------------------------------------ main

def parse_tables_and_text(tables, text, default_year=None, section=None):
    warnings = []
    points, each = read_points(tables, text)
    meetings = read_meetings(text)
    names = read_assignment_names(text)
    year = default_year
    if not year:
        match = re.search(r"Updated:\s*\d{1,2}/\d{1,2}/(\d{4})", text)
        year = int(match.group(1)) if match else datetime.date.today().year
    running = {"year": year, "prev_month": None}
    items, seen = [], set()
    first_month = None
    used_numbers = set()

    def add(date, title, kind, source, confidence, in_class=False):
        nonlocal first_month
        yr, _ = resolve_year(date, running) if date else (None, None)
        try:
            iso = datetime.date(yr, date[0], date[1]).isoformat()
        except (TypeError, ValueError):
            warnings.append(f"Skipped '{title}': '{date}' is not a real date.")
            return
        key = (title.lower(), iso)
        if key in seen:
            return
        seen.add(key)
        number = number_of(title)
        if kind == "Assignment" and number in names and re.fullmatch(r"Assignment #\d+", title):
            title = f"Assignment #{number}: {names[number]}"
        pts = points_for(kind, number, points, each)
        if kind == "Assignment" and number is not None:
            if number in used_numbers:
                pts = None
            used_numbers.add(number)
        item = {"title": title, "type": kind, "date": iso, "points": pts, "source": source, "confidence": confidence}
        if in_class:
            chosen = [m for m in meetings if section and m["section"] == section] or (meetings if len(meetings) == 1 else [])
            if chosen:
                item["start_time"], item["end_time"] = chosen[0]["start"], chosen[0]["end"]
        items.append(item)

    for date, activities, due, class_no in schedule_rows(tables):
        if first_month is None:
            first_month = date[0]
        label = f"Schedule, class {class_no}" if class_no else "Schedule"
        month_before = running["prev_month"]
        # one date resolution per row: use the first add() call to advance the year state
        for entry in split_bullets(activities):
            kind = kind_of(entry, False)
            if kind:
                add(date, title_case_assignment(entry), kind, label + " (in class)", "high", in_class=kind in ("Exam", "Quiz"))
        for entry in split_due(due):
            kind = kind_of(entry, True) or "Assignment"
            add(date, title_case_assignment(entry), kind, label + " (due date column)", "high")
        if running["prev_month"] == month_before:
            resolve_year(date, running)

    if not items:
        warnings.append("No schedule table was found, so I scanned the text for dated lines. Check each one.")
        for date, title in scan_text(text, year):
            add(date, title, kind_of(title, False) or "Other", "Found in text", "medium")

    items.sort(key=lambda i: i["date"])
    term = guess_term(first_month) if first_month else ""
    header_term = re.search(r"Syllabus for\s+(Spring|Summer|Fall|Winter)\s+(\d{4})", text, re.I)
    if header_term and term and header_term.group(1).capitalize() != term:
        warnings.append(
            f"The syllabus header says {header_term.group(1).title()} {header_term.group(2)}, but its dates start in "
            f"{datetime.date(2000, first_month, 1):%B}, so this looks like {term}. Dates were read from the schedule."
        )
    course = re.search(r"\b([A-Z]{2,5})\s?(\d{4})\b", text)
    title = re.search(r"Course Title:\s*(.+)", text)
    if not meetings:
        warnings.append("Couldn't find class meeting times.")
    return {
        "course": f"{course.group(1)} {course.group(2)}" if course else "",
        "title": title.group(1).strip() if title else "",
        "term_label": f"{term} {running['year']}".strip(),
        "meetings": meetings,
        "items": items,
        "warnings": warnings,
    }


def parse_syllabus(path, default_year=None, section=None):
    """Read a PDF file. Needs pdfplumber (pip install pdfplumber)."""
    import pdfplumber

    tables, text = [], ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += (page.extract_text() or "") + "\n"
            tables.extend(page.extract_tables())
    if len(text.strip()) < 200:
        return {"course": "", "title": "", "term_label": "", "meetings": [], "items": [],
                "warnings": ["This PDF has almost no readable text. It may be a scan. Add the dates by hand."]}
    return parse_tables_and_text(tables, text, default_year=default_year, section=section)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit("usage: python3 syllabus_parser.py file.pdf [--year 2026] [--section 001]")
    year = int(args[args.index("--year") + 1]) if "--year" in args else None
    sect = args[args.index("--section") + 1] if "--section" in args else None
    out = parse_syllabus(args[0], default_year=year, section=sect)
    print(f"{out['course']} {out['title']}  ({out['term_label']})")
    for m in out["meetings"]:
        print(f"  section {m['section'] or '?'}: {m['days']} {m['start']}-{m['end']} {m['room']}")
    print()
    for i in out["items"]:
        pts = f"{i['points']} pts" if i["points"] is not None else ""
        when = i["date"] + (f" {i['start_time']}" if i.get("start_time") else "")
        print(f"{when:17} {i['type']:10} {i['title']}  {pts}")
    for w in out["warnings"]:
        print("WARNING:", w)