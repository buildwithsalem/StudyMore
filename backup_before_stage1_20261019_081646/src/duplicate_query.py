"""
Rules for spotting a study group that duplicates one a student is about to create.

No Flask in this file, so every rule can be unit tested on its own.
duplicate_check.py is the Flask side that shows the warning page.

How it decides:

    Only groups in the same course that the student could join right now are
    compared. That uses derive_display_status() from search_query.py, the same
    rule the Join button uses, so a group that is full, completed, cancelled,
    or whose meeting time has already passed is never suggested.

    An open group in the same course counts as similar when ANY of these is true:
        1. The group names are similar (course code and filler words ignored)
        2. Same section and same study goal
        3. Same section and same meeting date
        4. Same meeting date and same meeting time

    An exact duplicate is the same student creating a group they already made:
    same creator, course, name, meeting date and meeting time. That one is
    blocked outright, because it is almost always a double submit.
"""

import re
from difflib import SequenceMatcher

from search_query import (
    CLOSED_STATUSES,
    derive_display_status,
    normalize_course_code,
    tidy_status,
)

NAME_SIMILARITY_THRESHOLD = 0.75
MAX_SIMILAR_GROUPS = 5

REASON_SIMILAR_NAME = "Similar group name"
REASON_SECTION_AND_GOAL = "Same section and study goal"
REASON_SECTION_AND_DATE = "Same section and meeting date"
REASON_DATE_AND_TIME = "Same meeting date and time"

# Words in almost every group name that say nothing about what the group studies.
FILLER_WORDS = {
    "a", "an", "and", "the", "for", "of", "to", "with", "in", "on",
    "study", "group", "session", "team", "club", "meetup",
}

# Course codes written inside a group name, like "CSE 3311" or "cse3311".
COURSE_CODE_IN_NAME = re.compile(r"\b[a-z]{2,4}\s*-?\s*\d{4}\b")

# The same fields create_group() refuses to work without. create_group() only
# rejects a field that is missing from the form; a blank value is accepted.
REQUIRED_FIELDS = (
    "group_name",
    "course_code",
    "section",
    "description",
    "meeting_type",
    "meeting_date",
    "meeting_time",
    "location",
    "meeting_link",
    "max_members",
)


def text_of(form, key):
    value = form.get(key) if form else None
    return "" if value is None else str(value).strip()


def has_required_fields(form):
    """True when the form has every field create_group() requires.

    Uses the same rule as create_group(): a field only counts as missing when
    it is not in the form at all. A blank or all spaces value still counts as
    present, so it cannot skip the duplicate check while create_group() goes
    on to save the group. When a field really is missing, the duplicate check
    stays out of the way and lets create_group() show its own error message.
    """
    if not form:
        return False
    return all(form.get(key) is not None for key in REQUIRED_FIELDS)


def proposal_from_form(form):
    """The parts of the submitted form that the duplicate rules look at."""
    return {
        "group_name": text_of(form, "group_name"),
        "course_code": text_of(form, "course_code"),
        "section": text_of(form, "section"),
        "study_goal": text_of(form, "study_goal"),
        "meeting_type": text_of(form, "meeting_type"),
        "meeting_date": text_of(form, "meeting_date"),
        "meeting_time": text_of(form, "meeting_time"),
    }


def normalize_section(value):
    """'001', '1', ' 001 ' -> '1'. Blank stays blank."""
    text = (value or "").strip().upper()
    if not text:
        return ""
    stripped = text.lstrip("0")
    return stripped or "0"


def normalize_time(value):
    """'17:00' and '17:00:00' compare as the same time."""
    return (value or "").strip()[:5]


def normalize_name(value):
    """Lowercase with single spaces, for the exact duplicate comparison."""
    return " ".join((value or "").lower().split())


def name_words(name):
    """
    The words in a group name that describe what the group is for.

    'CSE 3311 Finals Review Study Group' -> ['final', 'review']
    Lowercase, course code removed, filler words removed, and a trailing s
    dropped so 'Finals' and 'Final' match.
    """
    text = COURSE_CODE_IN_NAME.sub(" ", (name or "").lower())
    words = []
    for word in re.findall(r"[a-z0-9]+", text):
        if word in FILLER_WORDS:
            continue
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        words.append(word)
    return words


def name_similarity(first, second):
    """
    Score from 0 to 1 for how alike two group names are.

    Takes the higher of two measures: how alike the cleaned names read as
    text, and how many words the shorter name shares with the longer one.
    Names that differ only by a number ('Exam 1 Review' vs 'Exam 2 Review')
    score 0, because those are different study sessions.
    """
    first_words = name_words(first)
    second_words = name_words(second)
    if not first_words or not second_words:
        return 0.0

    first_numbers = {word for word in first_words if word.isdigit()}
    second_numbers = {word for word in second_words if word.isdigit()}
    if first_numbers and second_numbers and first_numbers != second_numbers:
        return 0.0

    text_score = SequenceMatcher(
        None, " ".join(first_words), " ".join(second_words)
    ).ratio()

    first_set, second_set = set(first_words), set(second_words)
    shared_score = len(first_set & second_set) / min(len(first_set), len(second_set))

    return max(text_score, shared_score)


def similarity_reasons(proposal, group):
    """Every rule the existing group matches. An empty list means not similar."""
    reasons = []

    if name_similarity(proposal["group_name"], group["group_name"]) >= NAME_SIMILARITY_THRESHOLD:
        reasons.append(REASON_SIMILAR_NAME)

    new_section = normalize_section(proposal["section"])
    old_section = normalize_section(group["section"])
    same_section = bool(new_section) and new_section == old_section

    new_goal = proposal["study_goal"].lower()
    old_goal = (group["study_goal"] or "").strip().lower()
    if same_section and new_goal and new_goal == old_goal:
        reasons.append(REASON_SECTION_AND_GOAL)

    same_date = bool(proposal["meeting_date"]) and (
        proposal["meeting_date"] == (group["meeting_date"] or "").strip()
    )
    if same_section and same_date:
        reasons.append(REASON_SECTION_AND_DATE)

    same_time = bool(proposal["meeting_time"]) and (
        normalize_time(proposal["meeting_time"]) == normalize_time(group["meeting_time"])
    )
    if same_date and same_time:
        reasons.append(REASON_DATE_AND_TIME)

    return reasons


GROUPS_IN_COURSE_SQL = """
    SELECT
        g.id AS group_id,
        g.creator_user_id,
        g.group_name,
        g.course_code,
        g.section,
        g.study_goal,
        g.meeting_type,
        g.meeting_date,
        g.meeting_time,
        g.max_members,
        g.status,
        COUNT(m.id) AS current_members,
        MAX(CASE WHEN m.user_id = ? THEN 1 ELSE 0 END) AS is_member
    FROM study_groups AS g
    LEFT JOIN group_memberships AS m ON m.group_id = g.id
    WHERE REPLACE(UPPER(g.course_code), ' ', '') = ?
    GROUP BY g.id
"""


def groups_in_course(conn, course_code, user_id=None):
    """Every group in the course, with its member count. Course spacing and case ignored."""
    return conn.execute(
        GROUPS_IN_COURSE_SQL,
        (user_id if user_id is not None else -1, normalize_course_code(course_code)),
    ).fetchall()


def is_still_usable(group):
    """True when the student could join this group right now.

    Same rule as the Join button: Open means not completed, not cancelled,
    not full, and the meeting time has not already passed.
    """
    return derive_display_status(
        group["status"],
        int(group["current_members"]),
        int(group["max_members"]),
        group["meeting_date"],
        group["meeting_time"],
    ) == "Open"


def find_similar_groups(conn, proposal, user_id=None):
    """
    Open groups in the same course that look like the one being created.

    Returns at most MAX_SIMILAR_GROUPS dicts, most similar first.
    """
    matches = []

    for group in groups_in_course(conn, proposal["course_code"], user_id):
        if not is_still_usable(group):
            continue

        reasons = similarity_reasons(proposal, group)
        if not reasons:
            continue

        matches.append({
            "group_id": group["group_id"],
            "group_name": group["group_name"],
            "course_code": group["course_code"],
            "section": group["section"] or "",
            "study_goal": group["study_goal"] or "",
            "meeting_type": group["meeting_type"],
            "meeting_date": group["meeting_date"],
            "meeting_time": group["meeting_time"],
            "current_members": int(group["current_members"]),
            "max_members": int(group["max_members"]),
            "is_member": bool(group["is_member"]),
            "reasons": reasons,
            "name_score": name_similarity(proposal["group_name"], group["group_name"]),
        })

    # More rules matched first, then closer names, then the soonest meeting.
    matches.sort(key=lambda match: (
        -len(match["reasons"]),
        -match["name_score"],
        match["meeting_date"] or "",
        match["meeting_time"] or "",
    ))
    return matches[:MAX_SIMILAR_GROUPS]


def find_exact_duplicate(conn, user_id, proposal):
    """
    A group this same student already created with the same course, name,
    meeting date and meeting time, if it is not completed or cancelled.
    """
    if user_id is None:
        return None

    wanted_name = normalize_name(proposal["group_name"])
    wanted_time = normalize_time(proposal["meeting_time"])

    for group in groups_in_course(conn, proposal["course_code"], user_id):
        if group["creator_user_id"] != user_id:
            continue
        if tidy_status(group["status"]) in CLOSED_STATUSES:
            continue
        if (
            normalize_name(group["group_name"]) == wanted_name
            and (group["meeting_date"] or "").strip() == proposal["meeting_date"]
            and normalize_time(group["meeting_time"]) == wanted_time
        ):
            return {
                "group_id": group["group_id"],
                "group_name": group["group_name"],
                "course_code": group["course_code"],
                "meeting_date": group["meeting_date"],
                "meeting_time": group["meeting_time"],
            }
    return None
