"""
Tests for the search and filter feature.

    python -m unittest test_search -v

Each test builds its own database in a temp folder using init_db() from app.py,
so the real study_more.db is never touched.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest

import app as app_module
import search_query
from demo_data import insert_demo_data
from search import search_bp
from search_query import (
    SearchValidationError,
    build_search_query,
    clean_filters,
    normalize_course_code,
    search_study_groups,
    tidy_status,
)

DB_NAME = "study_more.db"


def names_of(results):
    return [group["group_name"] for group in results]


def find_group(results, name):
    for group in results:
        if group["group_name"] == name:
            return group
    return None


class StudyMoreTestCase(unittest.TestCase):

    def setUp(self):
        # init_db() writes study_more.db relative to the cwd, so work in a temp dir.
        self.original_cwd = os.getcwd()
        self.tmp_dir = tempfile.mkdtemp(prefix="studymore-test-")
        os.chdir(self.tmp_dir)

        app_module.init_db()

        self.db_path = os.path.join(self.tmp_dir, DB_NAME)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        insert_demo_data(self.conn)

    def tearDown(self):
        self.conn.close()
        os.chdir(self.original_cwd)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def search(self, **filters):
        results, _ = search_study_groups(self.conn, filters)
        return results


class NormalSearchTests(StudyMoreTestCase):

    def test_search_by_course_code_finds_that_course(self):
        results = self.search(term="CSE 3311")
        self.assertCountEqual(names_of(results), ["Iteration 1 Review", "Finals Review"])

    def test_course_code_spacing_does_not_matter(self):
        spaced = names_of(self.search(term="CSE 3311"))
        squashed = names_of(self.search(term="cse3311"))
        doubled = names_of(self.search(term="CSE  3311"))
        self.assertEqual(spaced, squashed)
        self.assertEqual(spaced, doubled)

    def test_search_matches_group_name(self):
        self.assertEqual(names_of(self.search(term="Midterm")), ["Midterm Prep"])

    def test_search_matches_description(self):
        # "pointer" is only in group 7's description
        self.assertEqual(names_of(self.search(term="pointer")), ["Linked List Practice"])

    def test_search_is_not_case_sensitive(self):
        self.assertEqual(names_of(self.search(term="midterm")), ["Midterm Prep"])
        self.assertEqual(names_of(self.search(term="MIDTERM")), ["Midterm Prep"])

    def test_course_code_filter_matches_a_prefix(self):
        results = self.search(course_code="CSE")
        self.assertNotIn("Midterm Prep", names_of(results))  # MATH 1426
        self.assertEqual(len(results), 6)

    def test_section_filter(self):
        results = self.search(course_code="CSE 3311", section="001")
        self.assertEqual(names_of(results), ["Iteration 1 Review"])

    def test_meeting_type_filter(self):
        for meeting_type in ("In-Person", "Online", "Hybrid"):
            with self.subTest(meeting_type=meeting_type):
                results = self.search(meeting_type=meeting_type)
                self.assertTrue(results)
                for group in results:
                    self.assertEqual(group["meeting_type"], meeting_type)

    def test_status_filter_completed_and_cancelled(self):
        self.assertEqual(names_of(self.search(status="Completed")), ["Finals Review"])
        self.assertEqual(names_of(self.search(status="Cancelled")), ["Weekend Sprint"])

    def test_status_filter_works_against_lower_case_stored_values(self):
        # app.py stores "open"; the dropdown sends "Open"
        stored = self.conn.execute(
            "SELECT status FROM study_groups WHERE id = 1"
        ).fetchone()["status"]
        self.assertEqual(stored, "open")
        self.assertIn("Iteration 1 Review", names_of(self.search(status="Open")))

    def test_filters_combine(self):
        results = self.search(course_code="CSE", meeting_type="Online", available_only="on")
        self.assertEqual(names_of(results), ["Linked List Practice"])

    def test_member_count_is_counted_from_memberships(self):
        group = self.search(term="Iteration 1 Review")[0]
        self.assertEqual(group["current_members"], 2)
        self.assertEqual(group["max_members"], 6)
        self.assertEqual(group["spots_left"], 4)

    def test_group_with_no_members_still_appears(self):
        # relies on the LEFT JOIN
        group = find_group(self.search(term="Linked List"), "Linked List Practice")
        self.assertIsNotNone(group)
        self.assertEqual(group["current_members"], 0)

    def test_results_are_ordered_by_soonest_meeting(self):
        dates = [group["meeting_date"] for group in self.search(available_only="on")]
        self.assertEqual(dates, sorted(dates))

    def test_joinable_groups_are_listed_before_unavailable_ones(self):
        # Finals Review has the earlier date but is completed
        self.assertEqual(
            names_of(self.search(term="CSE 3311")),
            ["Iteration 1 Review", "Finals Review"],
        )

    def test_full_groups_sort_above_completed_and_cancelled_ones(self):
        self.assertEqual(
            names_of(self.search(course_code="CSE 3320")),
            ["Exam 1 Study Jam", "Weekend Sprint"],
        )

    def test_location_display_depends_on_meeting_type(self):
        in_person = find_group(self.search(term="Iteration 1"), "Iteration 1 Review")
        online = find_group(self.search(term="Linked List"), "Linked List Practice")
        hybrid = find_group(self.search(term="Midterm"), "Midterm Prep")

        self.assertEqual(in_person["location_display"], "ERB 128")
        self.assertEqual(online["location_display"], "Online")
        self.assertEqual(hybrid["location_display"], "Central Library 204 and online")

    def test_build_search_query_uses_bound_parameters(self):
        sql, params = build_search_query(clean_filters({"term": "CSE 3311"}))
        self.assertNotIn("CSE", sql)
        self.assertEqual(sql.count("?"), len(params))

    def test_normalize_course_code(self):
        self.assertEqual(normalize_course_code("cse 3311"), "CSE3311")
        self.assertEqual(normalize_course_code("  CSE  3311 "), "CSE3311")
        self.assertEqual(normalize_course_code(None), "")

    def test_tidy_status(self):
        self.assertEqual(tidy_status("open"), "Open")
        self.assertEqual(tidy_status("CANCELLED"), "Cancelled")
        self.assertEqual(tidy_status(""), "Open")
        self.assertEqual(tidy_status("something else"), "Open")


class ExceptionalCaseTests(StudyMoreTestCase):

    # no groups match
    def test_no_matching_groups_returns_empty_with_a_notice(self):
        results, notice = search_study_groups(self.conn, {"term": "PHYS 1443"})
        self.assertEqual(results, [])
        self.assertIn("No study groups match your search", notice)

    def test_no_match_notice_suggests_creating_a_group(self):
        _, notice = search_study_groups(self.conn, {"term": "PHYS 1443"})
        self.assertIn("create a group", notice.lower())

    # search field is empty
    def test_empty_search_returns_every_group_with_a_hint(self):
        results, notice = search_study_groups(self.conn, {"term": ""})
        self.assertEqual(len(results), 7)
        self.assertIn("narrow the list", notice)

    def test_search_of_only_spaces_is_treated_as_empty(self):
        results, notice = search_study_groups(self.conn, {"term": "     "})
        self.assertEqual(len(results), 7)
        self.assertIn("narrow the list", notice)

    def test_empty_search_with_a_filter_is_not_a_browse(self):
        _, notice = search_study_groups(self.conn, {"term": "", "meeting_type": "Online"})
        self.assertIsNone(notice)

    # group is full
    def test_full_group_is_shown_but_cannot_be_joined(self):
        # group 2 is 4 of 4 while its status column still says "open"
        group = find_group(self.search(term="Exam 1 Study Jam"), "Exam 1 Study Jam")
        self.assertEqual(group["current_members"], group["max_members"])
        self.assertEqual(group["status"], "Full")
        self.assertFalse(group["is_joinable"])
        self.assertEqual(group["join_blocked_reason"], "This group is full.")

    def test_status_filter_full_finds_the_derived_full_group(self):
        self.assertEqual(names_of(self.search(status="Full")), ["Exam 1 Study Jam"])

    def test_available_only_excludes_the_full_group(self):
        self.assertNotIn("Exam 1 Study Jam", names_of(self.search(available_only="on")))

    def test_a_group_stops_being_full_when_a_member_leaves(self):
        # leaving only deletes a membership row; the count has to pick that up
        self.conn.execute(
            "DELETE FROM group_memberships WHERE group_id = 2 AND user_id = 7"
        )
        self.conn.commit()

        group = find_group(self.search(term="Exam 1 Study Jam"), "Exam 1 Study Jam")
        self.assertEqual(group["status"], "Open")
        self.assertTrue(group["is_joinable"])
        self.assertIn("Exam 1 Study Jam", names_of(self.search(available_only="on")))

    # group is completed or cancelled
    def test_completed_group_is_shown_but_cannot_be_joined(self):
        group = find_group(self.search(term="Finals Review"), "Finals Review")
        self.assertEqual(group["status"], "Completed")
        self.assertFalse(group["is_joinable"])
        self.assertIn("finished", group["join_blocked_reason"])

    def test_cancelled_group_is_shown_but_cannot_be_joined(self):
        group = find_group(self.search(term="Weekend Sprint"), "Weekend Sprint")
        self.assertEqual(group["status"], "Cancelled")
        self.assertFalse(group["is_joinable"])
        self.assertIn("cancelled", group["join_blocked_reason"])

    def test_available_only_excludes_completed_and_cancelled(self):
        names = names_of(self.search(available_only="on"))
        self.assertNotIn("Finals Review", names)
        self.assertNotIn("Weekend Sprint", names)

    def test_a_completed_group_is_never_available_even_with_spots_left(self):
        group = find_group(self.search(term="Finals Review"), "Finals Review")
        self.assertLess(group["current_members"], group["max_members"])
        self.assertFalse(group["is_joinable"])

    # bad input
    def test_invalid_meeting_type_is_rejected(self):
        with self.assertRaises(SearchValidationError) as caught:
            self.search(meeting_type="Telepathy")
        self.assertIn("not a valid meeting type", str(caught.exception))

    def test_invalid_status_is_rejected(self):
        with self.assertRaises(SearchValidationError):
            self.search(status="Maybe")

    def test_impossible_filter_combination_is_explained(self):
        with self.assertRaises(SearchValidationError) as caught:
            self.search(available_only="on", status="Cancelled")
        self.assertIn("cannot be combined", str(caught.exception))

    def test_available_only_with_status_open_is_allowed(self):
        results = self.search(available_only="on", status="Open")
        self.assertTrue(results)
        for group in results:
            self.assertTrue(group["is_joinable"])

    def test_overly_long_search_term_is_rejected(self):
        with self.assertRaises(SearchValidationError) as caught:
            self.search(term="x" * (search_query.MAX_SEARCH_TERM_LENGTH + 1))
        self.assertIn("too long", str(caught.exception))

    def test_search_term_at_the_length_limit_is_accepted(self):
        self.assertEqual(self.search(term="x" * search_query.MAX_SEARCH_TERM_LENGTH), [])


class SecurityTests(StudyMoreTestCase):

    def test_percent_sign_is_not_treated_as_a_wildcard(self):
        self.assertEqual(self.search(term="%"), [])

    def test_underscore_is_not_treated_as_a_wildcard(self):
        self.assertEqual(self.search(term="_"), [])

    def test_sql_injection_attempt_is_harmless(self):
        self.assertEqual(self.search(term="'; DROP TABLE study_groups; --"), [])
        still_there = self.conn.execute("SELECT COUNT(*) FROM study_groups").fetchone()[0]
        self.assertEqual(still_there, 7)

    def test_injection_attempt_in_a_filter_is_harmless(self):
        self.assertEqual(self.search(course_code="CSE' OR '1'='1"), [])


class RouteTests(StudyMoreTestCase):

    def setUp(self):
        super().setUp()
        self.flask_app = app_module.app

        # so the tests run even before app.py registers the blueprint
        if "search" not in self.flask_app.blueprints:
            self.flask_app.register_blueprint(search_bp)

        self.original_db = self.flask_app.config.get("DATABASE")
        self.flask_app.config["DATABASE"] = self.db_path
        self.flask_app.config["TESTING"] = True
        self.flask_app.secret_key = "test-key"
        self.client = self.flask_app.test_client()

    def tearDown(self):
        self.flask_app.config["DATABASE"] = self.original_db
        super().tearDown()

    def log_in(self, user_id=1):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def test_search_page_requires_login(self):
        response = self.client.get("/search")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_search_page_renders_results(self):
        self.log_in()
        response = self.client.get("/search?term=CSE+3311")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Iteration 1 Review", response.data)

    def test_search_page_keeps_the_filters_the_student_chose(self):
        self.log_in()
        body = self.client.get("/search?term=CSE+3311&meeting_type=Online").data.decode()
        self.assertIn('value="CSE 3311"', body)
        self.assertIn('<option value="Online" selected>', body)

    def test_search_page_shows_the_no_results_message(self):
        self.log_in()
        response = self.client.get("/search?term=PHYS+1443")
        self.assertIn(b"No study groups match your search", response.data)

    def test_search_page_shows_a_validation_message_instead_of_crashing(self):
        self.log_in()
        response = self.client.get("/search?available_only=on&status=Cancelled")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"cannot be combined", response.data)

    def test_results_link_to_the_group_details_page(self):
        self.log_in()
        body = self.client.get("/search?term=Iteration+1").data.decode()
        self.assertIn('href="/group/1"', body)

    def test_joinable_result_posts_to_the_join_route(self):
        self.log_in()
        body = self.client.get("/search?term=Algorithms").data.decode()
        self.assertIn('action="/group/4/join"', body)

    def test_groups_the_student_already_joined_are_marked(self):
        # user 1 is in groups 1 and 3
        self.log_in(user_id=1)
        body = self.client.get("/search").data.decode()
        self.assertIn("Joined", body)
        self.assertIn('action="/group/7/join"', body)   # not joined, so a join form

    def test_a_different_student_sees_different_joined_groups(self):
        self.log_in(user_id=8)
        body = self.client.get("/search?term=Iteration+1+Review").data.decode()
        self.assertNotIn("Joined", body)
        self.assertIn('action="/group/1/join"', body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
