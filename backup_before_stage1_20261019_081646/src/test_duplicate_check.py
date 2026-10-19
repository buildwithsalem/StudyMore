"""
Tests for Duplicate Group Prevention.

    python -m unittest test_duplicate_check -v

Each test builds its own database in a temp folder using init_db() from app.py,
so the real study_more.db is never touched. The route tests load their own copy
of app.py and register the duplicate check on it, so app.py itself is not changed
and the search tests keep their own app.
"""

import importlib.util
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

from duplicate_check import CONFIRM_FIELD, duplicate_bp
from duplicate_query import (
    MAX_SIMILAR_GROUPS,
    REASON_DATE_AND_TIME,
    REASON_SECTION_AND_DATE,
    REASON_SECTION_AND_GOAL,
    REASON_SIMILAR_NAME,
    find_exact_duplicate,
    find_similar_groups,
    has_required_fields,
    name_similarity,
    name_words,
    normalize_section,
)

HERE = os.path.dirname(os.path.abspath(__file__))
DB_NAME = "study_more.db"


def day(offset):
    """A date relative to today, so these tests never go stale."""
    return (date.today() + timedelta(days=offset)).isoformat()


def moment(hours):
    """(date, time) a number of hours from now."""
    when = datetime.now() + timedelta(hours=hours)
    return when.strftime("%Y-%m-%d"), when.strftime("%H:%M")


FUTURE_DATE = day(60)


def load_fresh_app_module():
    """A separate copy of app.py, so registering the blueprint here cannot affect other tests."""
    name = "app_for_duplicate_tests"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "app.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    # So the tests run both before and after app.py registers the blueprint.
    if duplicate_bp.name not in module.app.blueprints:
        module.app.register_blueprint(duplicate_bp)
    return module


APP_MODULE = load_fresh_app_module()


def proposal(**overrides):
    values = {
        "group_name": "Exam Review",
        "course_code": "CSE 3311",
        "section": "",
        "study_goal": "",
        "meeting_type": "In-Person",
        "meeting_date": day(38),
        "meeting_time": "18:00",
    }
    values.update(overrides)
    return values


def names_of(matches):
    return [match["group_name"] for match in matches]


class DuplicateTestCase(unittest.TestCase):

    def setUp(self):
        self.original_cwd = os.getcwd()
        self.tmp_dir = tempfile.mkdtemp(prefix="studymore-duplicate-test-")
        os.chdir(self.tmp_dir)

        # Point the app at the temp database before creating tables. Works whether
        # init_db() reads app.config["DATABASE"] or writes study_more.db in the cwd.
        self.db_path = os.path.join(self.tmp_dir, DB_NAME)
        APP_MODULE.app.config["DATABASE"] = self.db_path
        APP_MODULE.init_db()

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        for user_id in range(1, 9):
            self.conn.execute(
                "INSERT INTO users (id, name, email, password) VALUES (?, ?, ?, ?)",
                (user_id, f"Student {user_id}", f"student{user_id}@example.com", "hash"),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.chdir(self.original_cwd)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def add_group(self, name, course="CSE 3311", section="", study_goal=None,
                  meeting_date=None, meeting_time="17:00", creator=2,
                  max_members=6, status="open", members=(2,), meeting_type="In-Person"):
        meeting_date = meeting_date or day(30)
        cursor = self.conn.execute(
            """
            INSERT INTO study_groups
                (creator_user_id, group_name, course_code, section, study_goal,
                 description, meeting_type, meeting_date, meeting_time,
                 location, meeting_link, max_members, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (creator, name, course, section, study_goal, "Test group",
             meeting_type, meeting_date, meeting_time, "ERB 128", "",
             max_members, status),
        )
        group_id = cursor.lastrowid
        for user_id in members:
            self.conn.execute(
                "INSERT INTO group_memberships (user_id, group_id) VALUES (?, ?)",
                (user_id, group_id),
            )
        self.conn.commit()
        return group_id

    def similar(self, user_id=1, **overrides):
        return find_similar_groups(self.conn, proposal(**overrides), user_id)

    def group_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM study_groups").fetchone()[0]


class NameRuleTests(DuplicateTestCase):

    def test_course_code_and_filler_words_are_ignored(self):
        self.assertEqual(name_words("CSE 3311 Exam Study Group"), ["exam"])
        self.assertEqual(name_words("cse3311 exam group"), ["exam"])

    def test_plural_and_singular_match(self):
        self.assertEqual(name_words("Finals Review"), name_words("Final Review Session"))

    def test_different_exam_numbers_are_not_similar(self):
        self.assertEqual(name_similarity("Exam 1 Review", "Exam 2 Review"), 0.0)

    def test_same_exam_number_is_similar(self):
        self.assertGreaterEqual(name_similarity("Exam 1 Review", "exam 1 review session"), 0.75)

    def test_names_made_only_of_filler_words_never_match_on_name(self):
        self.assertEqual(name_similarity("CSE 3311 Study Group", "Study Group"), 0.0)

    def test_unrelated_names_are_not_similar(self):
        self.assertLess(name_similarity("Homework Help", "Iteration 1 Review"), 0.75)

    def test_section_leading_zeros_do_not_matter(self):
        self.assertEqual(normalize_section("001"), normalize_section("1"))
        self.assertEqual(normalize_section(" 002 "), "2")
        self.assertEqual(normalize_section(""), "")


class SimilarGroupTests(DuplicateTestCase):

    def test_similar_name_in_same_course_is_found(self):
        self.add_group("CSE 3311 Exam Review Group")
        matches = self.similar(group_name="Exam Review")
        self.assertEqual(names_of(matches), ["CSE 3311 Exam Review Group"])
        self.assertIn(REASON_SIMILAR_NAME, matches[0]["reasons"])

    def test_same_name_in_a_different_course_is_ignored(self):
        self.add_group("Exam Review", course="CSE 3320")
        self.assertEqual(self.similar(group_name="Exam Review"), [])

    def test_course_code_spacing_and_case_do_not_matter(self):
        self.add_group("Exam Review", course="CSE 3311")
        self.assertEqual(len(self.similar(course_code="cse3311")), 1)
        self.assertEqual(len(self.similar(course_code="CSE  3311")), 1)

    def test_same_section_and_study_goal_is_found(self):
        self.add_group("Thursday Crew", section="001", study_goal="Exam")
        matches = self.similar(group_name="Night Owls", section="1", study_goal="exam")
        self.assertEqual(matches[0]["reasons"], [REASON_SECTION_AND_GOAL])

    def test_same_section_and_meeting_date_is_found(self):
        self.add_group("Thursday Crew", section="002", meeting_date=day(38), meeting_time="09:00")
        matches = self.similar(group_name="Night Owls", section="002", meeting_date=day(38))
        self.assertEqual(matches[0]["reasons"], [REASON_SECTION_AND_DATE])

    def test_same_date_and_time_is_found_without_a_section(self):
        self.add_group("Thursday Crew", meeting_date=day(38), meeting_time="18:00")
        matches = self.similar(group_name="Night Owls", meeting_date=day(38), meeting_time="18:00")
        self.assertEqual(matches[0]["reasons"], [REASON_DATE_AND_TIME])

    def test_only_sharing_a_section_is_not_a_duplicate(self):
        self.add_group("Homework Help", section="001", meeting_date=day(30))
        self.assertEqual(self.similar(group_name="Exam Review", section="001"), [])

    def test_blank_sections_do_not_count_as_the_same_section(self):
        self.add_group("Thursday Crew", section="", study_goal="Exam")
        self.assertEqual(self.similar(group_name="Night Owls", section="", study_goal="Exam"), [])

    def test_full_group_is_skipped(self):
        self.add_group("Exam Review", max_members=2, members=(2, 3))
        self.assertEqual(self.similar(), [])

    def test_completed_and_cancelled_groups_are_skipped(self):
        self.add_group("Exam Review", status="completed")
        self.add_group("Exam Review", status="Cancelled")
        self.assertEqual(self.similar(), [])

    def test_group_whose_meeting_already_passed_is_skipped(self):
        self.add_group("Exam Review", meeting_date=day(-1))
        self.assertEqual(self.similar(), [])

    def test_group_that_met_earlier_today_is_skipped(self):
        meeting_date, meeting_time = moment(-2)
        self.add_group("Exam Review", meeting_date=meeting_date, meeting_time=meeting_time)
        self.assertEqual(self.similar(), [])

    def test_group_meeting_later_is_still_shown(self):
        meeting_date, meeting_time = moment(2)
        self.add_group("Exam Review", meeting_date=meeting_date, meeting_time=meeting_time)
        self.assertEqual(len(self.similar()), 1)

    def test_more_matching_rules_rank_first(self):
        self.add_group("Exam Review", meeting_date=day(20))
        self.add_group("Exam Review Session", section="001", study_goal="Exam",
                       meeting_date=day(38), meeting_time="18:00")
        matches = self.similar(section="001", study_goal="Exam")
        self.assertEqual(names_of(matches), ["Exam Review Session", "Exam Review"])

    def test_at_most_five_groups_are_returned(self):
        for offset in range(30, 38):
            self.add_group("Exam Review", meeting_date=day(offset))
        self.assertEqual(len(self.similar()), MAX_SIMILAR_GROUPS)

    def test_groups_the_student_already_joined_are_marked(self):
        self.add_group("Exam Review", members=(2, 1))
        self.assertTrue(self.similar(user_id=1)[0]["is_member"])
        self.assertFalse(self.similar(user_id=5)[0]["is_member"])

    def test_injection_in_course_code_is_harmless(self):
        self.add_group("Exam Review")
        self.assertEqual(self.similar(course_code="CSE' OR '1'='1"), [])
        self.assertEqual(self.group_count(), 1)


class ExactDuplicateTests(DuplicateTestCase):

    def test_same_creator_name_date_and_time_is_an_exact_duplicate(self):
        group_id = self.add_group("Exam Review", creator=1, meeting_date=day(38), meeting_time="18:00")
        found = find_exact_duplicate(self.conn, 1, proposal(group_name="  exam   REVIEW "))
        self.assertEqual(found["group_id"], group_id)

    def test_another_students_group_is_not_an_exact_duplicate(self):
        self.add_group("Exam Review", creator=2, meeting_date=day(38), meeting_time="18:00")
        self.assertIsNone(find_exact_duplicate(self.conn, 1, proposal()))

    def test_cancelled_group_does_not_block_recreating_it(self):
        self.add_group("Exam Review", creator=1, meeting_date=day(38),
                       meeting_time="18:00", status="cancelled")
        self.assertIsNone(find_exact_duplicate(self.conn, 1, proposal()))

    def test_different_time_is_not_an_exact_duplicate(self):
        self.add_group("Exam Review", creator=1, meeting_date=day(38), meeting_time="19:00")
        self.assertIsNone(find_exact_duplicate(self.conn, 1, proposal()))

    def test_required_fields_check(self):
        form = {
            "group_name": "Exam Review", "course_code": "CSE 3311", "section": "",
            "description": "Practice", "meeting_type": "Online",
            "meeting_date": day(38), "meeting_time": "18:00",
            "location": "", "meeting_link": "https://example.com", "max_members": "4",
        }
        self.assertTrue(has_required_fields(form))
        # Blank values still count as present, the same as in create_group().
        form["description"] = "   "
        self.assertTrue(has_required_fields(form))
        del form["description"]
        self.assertFalse(has_required_fields(form))


class RouteTests(DuplicateTestCase):

    def setUp(self):
        super().setUp()
        self.flask_app = APP_MODULE.app
        self.flask_app.config["TESTING"] = True
        self.flask_app.config["DATABASE"] = self.db_path
        self.flask_app.secret_key = "test-key"
        self.client = self.flask_app.test_client()

    def log_in(self, user_id=1):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def form(self, **overrides):
        values = {
            "group_name": "Exam Review",
            "course_code": "CSE 3311",
            "section": "001",
            "study_goal": "",
            "description": "Practice problems before the exam.",
            "meeting_type": "In-Person",
            "meeting_date": FUTURE_DATE,
            "meeting_time": "18:00",
            "location": "ERB 128",
            "meeting_link": "",
            "max_members": "5",
            "partner_id": "",
            "request_id": "",
            "partner_request_id": "",
        }
        values.update(overrides)
        return values

    def test_similar_group_shows_warning_and_creates_nothing(self):
        self.add_group("CSE 3311 Exam Review Group", meeting_date=FUTURE_DATE, meeting_time="10:00")
        self.log_in()
        response = self.client.post("/create-group", data=self.form())
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Similar study groups already exist", response.data)
        self.assertIn(b"CSE 3311 Exam Review Group", response.data)
        self.assertEqual(self.group_count(), 1)

    def test_warning_runs_on_every_url_that_creates_a_group(self):
        # Today both "/" and "/create-group" create groups. Checked by endpoint,
        # so this keeps passing if "/" later becomes a different page.
        self.add_group("Exam Review", meeting_date=FUTURE_DATE)
        self.log_in()
        urls = [
            rule.rule for rule in self.flask_app.url_map.iter_rules()
            if rule.endpoint == "create_group" and "POST" in rule.methods
        ]
        self.assertIn("/create-group", urls)
        for url in urls:
            response = self.client.post(url, data=self.form())
            self.assertIn(b"Similar study groups already exist", response.data, url)

    def test_warning_keeps_every_field_including_match_fields(self):
        self.add_group("Exam Review", meeting_date=FUTURE_DATE)
        self.log_in()
        response = self.client.post("/create-group", data=self.form(
            partner_id="3", request_id="10", partner_request_id="11",
            description='Bring "practice" <questions>',
        ))
        page = response.data.decode()
        self.assertIn('name="partner_id" value="3"', page)
        self.assertIn('name="request_id" value="10"', page)
        self.assertIn('name="partner_request_id" value="11"', page)
        self.assertIn(f'name="{CONFIRM_FIELD}" value="yes"', page)
        # Typed text is escaped, not injected into the page.
        self.assertIn("Bring &#34;practice&#34; &lt;questions&gt;", page)

    def test_create_anyway_creates_the_group(self):
        self.add_group("Exam Review", meeting_date=FUTURE_DATE)
        self.log_in()
        response = self.client.post("/create-group", data=self.form(**{CONFIRM_FIELD: "yes"}))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/group/", response.headers["Location"])
        self.assertEqual(self.group_count(), 2)

    def test_no_similar_group_creates_normally(self):
        self.add_group("Homework Help", course="CSE 3320")
        self.log_in()
        response = self.client.post("/create-group", data=self.form())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.group_count(), 2)

    def test_exact_duplicate_is_blocked_even_after_create_anyway(self):
        group_id = self.add_group("Exam Review", creator=1, members=(1,),
                                  meeting_date=FUTURE_DATE, meeting_time="18:00")
        self.log_in(1)
        response = self.client.post("/create-group", data=self.form(**{CONFIRM_FIELD: "yes"}))
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"You already created this group", response.data)
        self.assertIn(f"/group/{group_id}".encode(), response.data)
        self.assertEqual(self.group_count(), 1)

    def test_join_button_on_warning_page_joins_the_existing_group(self):
        group_id = self.add_group("Exam Review", meeting_date=FUTURE_DATE)
        self.log_in(1)
        page = self.client.post("/create-group", data=self.form()).data.decode()
        self.assertIn(f'action="/group/{group_id}/join"', page)

        response = self.client.post(f"/group/{group_id}/join")
        self.assertEqual(response.status_code, 302)
        joined = self.conn.execute(
            "SELECT 1 FROM group_memberships WHERE user_id = 1 AND group_id = ?", (group_id,)
        ).fetchone()
        self.assertIsNotNone(joined)
        self.assertEqual(self.group_count(), 1)

    def test_member_sees_view_but_no_join_button(self):
        group_id = self.add_group("Exam Review", members=(2, 1), meeting_date=FUTURE_DATE)
        self.log_in(1)
        page = self.client.post("/create-group", data=self.form()).data.decode()
        self.assertIn("YOU ARE A MEMBER", page)
        self.assertNotIn(f'action="/group/{group_id}/join"', page)

    def test_logged_out_student_is_still_sent_to_login(self):
        self.add_group("Exam Review", meeting_date=FUTURE_DATE)
        response = self.client.post("/create-group", data=self.form())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_blank_description_still_gets_the_duplicate_check(self):
        self.add_group("CSE 3311 Exam Review Group", meeting_date=FUTURE_DATE, meeting_time="10:00")
        self.log_in()
        response = self.client.post("/create-group", data=self.form(description="   "))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Similar study groups already exist", response.data)
        self.assertEqual(self.group_count(), 1)

    def test_missing_field_is_left_to_create_group(self):
        self.add_group("Exam Review", meeting_date=FUTURE_DATE)
        self.log_in()
        data = self.form()
        del data["description"]
        response = self.client.post("/create-group", data=data)
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Missing required form field", response.data)

    def test_create_group_page_still_loads(self):
        self.log_in()
        response = self.client.get("/create-group")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Create a Study Group", response.data)

    def test_create_group_together_still_adds_partner_after_create_anyway(self):
        self.conn.executemany(
            """
            INSERT INTO study_requests (id, user_id, course_code, study_goal, meeting_preference)
            VALUES (?, ?, 'CSE 3311', 'Exam', 'In-Person')
            """,
            [(10, 1), (11, 3)],
        )
        self.conn.commit()
        self.add_group("Exam Review", meeting_date=FUTURE_DATE)
        self.log_in(1)
        data = self.form(partner_id="3", request_id="10", partner_request_id="11",
                         study_goal="Exam")

        warning = self.client.post("/create-group", data=data)
        self.assertIn(b"Similar study groups already exist", warning.data)

        response = self.client.post("/create-group", data={**data, CONFIRM_FIELD: "yes"})
        self.assertEqual(response.status_code, 302)
        new_group_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        members = {
            row[0] for row in self.conn.execute(
                "SELECT user_id FROM group_memberships WHERE group_id = ?", (new_group_id,)
            )
        }
        self.assertEqual(members, {1, 3})
        statuses = {
            row[0] for row in self.conn.execute(
                "SELECT status FROM study_requests WHERE id IN (10, 11)"
            )
        }
        self.assertEqual(statuses, {"matched"})


if __name__ == "__main__":
    unittest.main()
