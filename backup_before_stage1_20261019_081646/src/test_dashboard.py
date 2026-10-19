"""
Tests for the Student Dashboard feature and Root Landing Page routing.

    python -m unittest test_dashboard -v

Each test builds its own database in a temp folder using init_db() from app.py,
so the real study_more.db is never touched.
"""

from datetime import datetime, timedelta
import os
import shutil
import sqlite3
import tempfile
import unittest

import app as app_module
from demo_data import insert_demo_data
from dashboard import (
    derive_group_status,
    format_location_display,
    get_dashboard_data,
    get_upcoming_meetings,
    get_user_info,
    get_user_joined_groups,
    get_user_smart_matches,
    get_user_study_requests,
    is_meeting_in_future,
)

DB_NAME = "study_more.db"


class DashboardTestCase(unittest.TestCase):

    def setUp(self):
        # Work in a temporary directory for isolation. The app is pointed at the
        # temp database through app.config["DATABASE"] (not the working
        # directory), exactly like production configuration.
        self.original_cwd = os.getcwd()
        self.tmp_dir = tempfile.mkdtemp(prefix="studymore-dashboard-test-")
        os.chdir(self.tmp_dir)

        self.db_path = os.path.join(self.tmp_dir, DB_NAME)
        app_module.init_db(self.db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        insert_demo_data(self.conn)

        self.flask_app = app_module.app
        self.original_db = self.flask_app.config.get("DATABASE")
        self.flask_app.config["DATABASE"] = self.db_path
        self.flask_app.config["TESTING"] = True
        self.flask_app.secret_key = "test-secret-key"
        self.client = self.flask_app.test_client()

    def tearDown(self):
        self.conn.close()
        self.flask_app.config["DATABASE"] = self.original_db
        os.chdir(self.original_cwd)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def log_in(self, user_id=1, user_name="Demo Student"):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_name"] = user_name


class RootLandingAndAuthRoutingTests(DashboardTestCase):

    def test_root_redirects_logged_out_user_to_login(self):
        """Visiting `/` while logged out must redirect to `/login`."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_root_redirects_logged_in_user_to_dashboard(self):
        """Visiting `/` while logged in must redirect to `/dashboard`."""
        self.log_in(user_id=1, user_name="Demo Student")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["Location"])

    def test_dashboard_requires_authentication(self):
        """Unauthenticated user accessing /dashboard must be redirected to /login."""
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_dashboard_accessible_when_logged_in(self):
        """Authenticated user accessing /dashboard receives 200 OK."""
        self.log_in(user_id=1, user_name="Demo Student")
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)

    def test_login_redirects_to_dashboard_on_success(self):
        """Successful login must redirect directly to the Dashboard landing page, not /create-group."""
        from werkzeug.security import generate_password_hash
        hashed = generate_password_hash("password123")
        self.conn.execute(
            "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
            ("Salem User", "salem@example.com", hashed)
        )
        self.conn.commit()

        response = self.client.post("/login", data={
            "email": "salem@example.com",
            "password": "password123"
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["Location"])

    def test_logout_clears_session_and_root_redirects_to_login(self):
        """Logging out clears session so subsequent / and /dashboard requests redirect to /login."""
        self.log_in(user_id=1)
        logout_res = self.client.get("/logout")
        self.assertEqual(logout_res.status_code, 302)
        self.assertIn("/login", logout_res.headers["Location"])

        # Visiting root after logout must go to /login
        root_res = self.client.get("/")
        self.assertEqual(root_res.status_code, 302)
        self.assertIn("/login", root_res.headers["Location"])

        # Visiting /dashboard after logout must go to /login
        dash_res = self.client.get("/dashboard")
        self.assertEqual(dash_res.status_code, 302)
        self.assertIn("/login", dash_res.headers["Location"])


class DashboardContentTests(DashboardTestCase):

    def test_dashboard_shows_user_greeting(self):
        """Dashboard displays greeting personalized with the user's name."""
        self.log_in(user_id=1, user_name="Demo Student")
        body = self.client.get("/dashboard").data.decode()
        self.assertIn("Welcome back, Demo Student!", body)

    def test_dashboard_shows_joined_groups(self):
        """Dashboard lists the groups the logged-in user has joined."""
        # Demo student (user 1) is in 'Iteration 1 Review' (group 1) and 'Midterm Prep' (group 3)
        self.log_in(user_id=1)
        body = self.client.get("/dashboard").data.decode()

        self.assertIn("Iteration 1 Review", body)
        self.assertIn("Midterm Prep", body)
        self.assertIn("CSE 3311", body)
        self.assertIn("MATH 1426", body)
        # Group 4 (Algorithms) is not joined by user 1
        self.assertNotIn("Algorithms Problem Set", body)

    def test_dashboard_different_user_sees_their_own_joined_groups(self):
        """User 8 sees only their joined groups, not other users' groups."""
        # User 8 is in group 3 (Midterm Prep) and group 5 (Finals Review)
        self.log_in(user_id=8, user_name="Casey Nguyen")
        body = self.client.get("/dashboard").data.decode()

        self.assertIn("Midterm Prep", body)
        self.assertIn("Finals Review", body)
        self.assertNotIn("Iteration 1 Review", body)

    def test_dashboard_quick_actions_present(self):
        """Dashboard contains quick action links to Search Groups, Create Group, and My Groups."""
        self.log_in(user_id=1)
        body = self.client.get("/dashboard").data.decode()

        self.assertIn('href="/search"', body)
        self.assertIn('href="/create-group"', body)
        self.assertIn('href="/my-groups"', body)
        self.assertIn("Search Groups", body)
        self.assertIn("Create Group", body)
        self.assertIn("My Groups", body)

    def test_dashboard_summary_stats_display(self):
        """Dashboard displays summary statistics matching user's counts."""
        self.log_in(user_id=1)
        body = self.client.get("/dashboard").data.decode()

        self.assertIn("Joined Groups", body)
        self.assertIn("Upcoming Meetings", body)
        self.assertIn("Smart Matches", body)
        self.assertIn("Study Requests", body)


class UpcomingMeetingsFilteringTests(DashboardTestCase):

    def test_past_meetings_do_not_appear_in_upcoming_meetings(self):
        """Past meetings (yesterday or past dates) must NOT appear in Upcoming Meetings."""
        # Insert a past meeting group for user 1
        self.conn.execute("""
            INSERT INTO study_groups (
                id, creator_user_id, group_name, course_code, section, description,
                meeting_type, meeting_date, meeting_time, location, max_members, status
            ) VALUES (
                101, 1, 'Yesterday Past Meeting', 'CSE 1310', '001', 'Past review',
                'In-Person', '2020-01-01', '10:00', 'ERB 100', 5, 'open'
            )
        """)
        self.conn.execute("INSERT INTO group_memberships (user_id, group_id) VALUES (1, 101)")
        self.conn.commit()

        # Check with upcoming meetings helper
        upcoming = get_upcoming_meetings(self.conn, user_id=1)
        upcoming_names = [m["group_name"] for m in upcoming]
        self.assertNotIn("Yesterday Past Meeting", upcoming_names)

        # In joined groups it still appears (for record history)
        joined = get_user_joined_groups(self.conn, user_id=1)
        joined_names = [g["group_name"] for g in joined]
        self.assertIn("Yesterday Past Meeting", joined_names)

    def test_earlier_today_meeting_that_ended_does_not_appear(self):
        """A meeting today whose time has already passed must NOT appear in Upcoming Meetings."""
        ref_now = datetime(2026, 11, 12, 18, 30)  # 6:30 PM
        # Group 1 is on 2026-11-12 at 17:00 (5:00 PM), which is in the past relative to ref_now
        upcoming = get_upcoming_meetings(self.conn, user_id=1, now=ref_now)
        upcoming_names = [m["group_name"] for m in upcoming]
        self.assertNotIn("Iteration 1 Review", upcoming_names)

        # Group 3 is on 2026-11-15 at 14:30 (future), so it should appear
        self.assertIn("Midterm Prep", upcoming_names)

    def test_future_meeting_later_today_appears(self):
        """A meeting today whose time is in the future relative to now MUST appear."""
        ref_now = datetime(2026, 11, 12, 12, 0)  # 12:00 PM noon
        # Group 1 is at 17:00 (5:00 PM) on 2026-11-12
        upcoming = get_upcoming_meetings(self.conn, user_id=1, now=ref_now)
        upcoming_names = [m["group_name"] for m in upcoming]
        self.assertIn("Iteration 1 Review", upcoming_names)

    def test_completed_or_cancelled_groups_do_not_appear_in_upcoming_even_with_future_date(self):
        """Cancelled or completed groups must be excluded from upcoming meetings."""
        # Insert a cancelled future group
        self.conn.execute("""
            INSERT INTO study_groups (
                id, creator_user_id, group_name, course_code, section, description,
                meeting_type, meeting_date, meeting_time, location, max_members, status
            ) VALUES (
                102, 1, 'Cancelled Future Meeting', 'CSE 3311', '001', 'Cancelled session',
                'In-Person', '2030-01-01', '10:00', 'ERB 100', 5, 'cancelled'
            )
        """)
        self.conn.execute("INSERT INTO group_memberships (user_id, group_id) VALUES (1, 102)")
        self.conn.commit()

        upcoming = get_upcoming_meetings(self.conn, user_id=1)
        upcoming_names = [m["group_name"] for m in upcoming]
        self.assertNotIn("Cancelled Future Meeting", upcoming_names)


class ActiveStudyRequestsFilteringTests(DashboardTestCase):

    def test_active_study_requests_appear(self):
        """Active requests with status 'open', 'active', 'pending' must appear."""
        self.conn.execute("""
            INSERT INTO study_requests (user_id, course_code, section, study_goal, meeting_preference, note, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (1, "CSE 3311", "001", "Need help with design patterns", "In-Person", "Weekly meetings", "open"))
        self.conn.execute("""
            INSERT INTO study_requests (user_id, course_code, section, study_goal, meeting_preference, note, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (1, "CSE 3320", "002", "OS Virtual Memory study partner", "Online", "Discord study", "active"))
        self.conn.commit()

        requests = get_user_study_requests(self.conn, user_id=1)
        self.assertEqual(len(requests), 2)
        courses = [r["course_code"] for r in requests]
        self.assertIn("CSE 3311", courses)
        self.assertIn("CSE 3320", courses)

        # Verify rendering in dashboard HTML
        self.log_in(user_id=1)
        body = self.client.get("/dashboard").data.decode()
        self.assertIn("CSE 3311 Study Request", body)
        self.assertIn("OS Virtual Memory study partner", body)

    def test_inactive_completed_cancelled_requests_do_not_appear(self):
        """Inactive, completed, cancelled, closed, fulfilled requests must NOT appear in Active Study Requests."""
        self.conn.execute("""
            INSERT INTO study_requests (user_id, course_code, section, study_goal, meeting_preference, note, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (1, "MATH 1426", "004", "Old Calculus Request", "Hybrid", "Done", "completed"))
        self.conn.execute("""
            INSERT INTO study_requests (user_id, course_code, section, study_goal, meeting_preference, note, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (1, "CSE 1320", "001", "Cancelled C Programming Request", "Online", "N/A", "cancelled"))
        self.conn.execute("""
            INSERT INTO study_requests (user_id, course_code, section, study_goal, meeting_preference, note, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (1, "PHYS 1443", "002", "Inactive Physics Request", "In-Person", "N/A", "inactive"))
        self.conn.execute("""
            INSERT INTO study_requests (user_id, course_code, section, study_goal, meeting_preference, note, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (1, "CSE 3315", "001", "Closed Theory Request", "Online", "N/A", "closed"))
        self.conn.commit()

        requests = get_user_study_requests(self.conn, user_id=1)
        self.assertEqual(requests, [])

        # Verify empty state renders when only inactive requests exist
        self.log_in(user_id=1)
        body = self.client.get("/dashboard").data.decode()
        self.assertIn("No active study requests at this time.", body)
        self.assertNotIn("Old Calculus Request", body)
        self.assertNotIn("Cancelled C Programming Request", body)


class DashboardEmptyStatesTests(DashboardTestCase):

    def setUp(self):
        super().setUp()
        # Create a new user with 0 memberships
        self.conn.execute(
            "INSERT INTO users (id, name, email, password) VALUES (?, ?, ?, ?)",
            (99, "New Student", "new@example.com", "hash99")
        )
        self.conn.commit()

    def test_empty_state_when_user_has_no_joined_groups(self):
        """User with no joined groups sees clear empty state for groups."""
        self.log_in(user_id=99, user_name="New Student")
        body = self.client.get("/dashboard").data.decode()

        self.assertIn("You haven't joined any study groups yet.", body)

    def test_empty_state_when_user_has_no_upcoming_meetings(self):
        """User with no joined groups sees clear empty state for upcoming meetings."""
        self.log_in(user_id=99, user_name="New Student")
        body = self.client.get("/dashboard").data.decode()

        self.assertIn("No upcoming meetings scheduled.", body)

    def test_empty_state_for_study_requests(self):
        """Dashboard renders proper empty state when user has no study requests."""
        self.log_in(user_id=1)
        body = self.client.get("/dashboard").data.decode()

        self.assertIn("No active study requests at this time.", body)

    def test_empty_state_for_smart_matches(self):
        """Dashboard renders proper empty state when user has no smart matches."""
        self.log_in(user_id=1)
        body = self.client.get("/dashboard").data.decode()

        self.assertIn("No smart matches found yet.", body)


class DashboardSmartMatchIntegrationTests(DashboardTestCase):
    """The dashboard must use the real matching logic (matching.py), not a fake table."""

    def add_request(self, user_id, course, section, goal, preference, status="active"):
        cursor = self.conn.execute(
            """
            INSERT INTO study_requests
                (user_id, course_code, section, study_goal, meeting_preference, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, course, section, goal, preference, status),
        )
        self.conn.commit()
        return cursor.lastrowid

    def add_availability(self, user_id, *slots):
        self.conn.executemany(
            """
            INSERT INTO user_availability (user_id, day_of_week, start_time, end_time)
            VALUES (?, ?, ?, ?)
            """,
            [(user_id,) + slot for slot in slots],
        )
        self.conn.commit()

    def test_no_study_requests_gives_empty_matches(self):
        """No study requests is a legitimate empty state, not an error."""
        self.assertEqual(get_user_smart_matches(self.conn, user_id=1), [])

    def test_group_match_comes_from_real_matching_logic(self):
        """An open group for the user's course appears with the route's own score."""
        # User 1 is not in group 4 (CSE 3315, section 001, In-Person, 5 seats).
        request_id = self.add_request(1, "CSE 3315", "001", "Exam", "In-Person")

        matches = get_user_smart_matches(self.conn, user_id=1)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["group_id"], 4)
        self.assertEqual(matches[0]["group_name"], "Algorithms Problem Set")
        # Same course 40 + same section 20 + same meeting type 20 (group has no goal).
        self.assertEqual(matches[0]["match_score"], 80)

        # The /study-matches route shows the exact same score for the same request.
        self.log_in(user_id=1)
        route_body = self.client.get(f"/study-matches/{request_id}").data.decode()
        self.assertIn("Algorithms Problem Set", route_body)
        self.assertRegex(route_body, r"80\s*%")

        body = self.client.get("/dashboard").data.decode()
        self.assertIn("Algorithms Problem Set", body)
        self.assertIn("80% Match", body)
        self.assertNotIn("No smart matches found yet.", body)

    def test_dashboard_scores_equal_the_study_matches_route_scores(self):
        """The dashboard and /study-matches share one scoring implementation."""
        from matching import find_group_matches, find_partner_matches, get_user_study_request

        request_id = self.add_request(1, "CSE 3320", "002", "Exam", "Online")
        partner_request = self.add_request(3, "CSE 3320", "002", "Exam", "Online")
        slot = ("Monday", "10:00", "11:00")
        self.add_availability(1, slot, ("Tuesday", "10:00", "11:00"), ("Friday", "13:00", "14:00"))
        self.add_availability(3, slot, ("Tuesday", "10:00", "11:00"), ("Friday", "13:00", "14:00"))

        study_request = get_user_study_request(self.conn, request_id, 1)
        partner = find_partner_matches(self.conn, 1, study_request)
        self.assertEqual(len(partner), 1)
        # 10 course + 30 goal + 20 preference + 10 section + 30 strong overlap
        self.assertEqual(partner[0]["score"], 100)
        self.assertEqual(partner[0]["request_id"], partner_request)

        matches = get_user_smart_matches(self.conn, user_id=1)
        dashboard_scores = {m["title"]: m["match_score"] for m in matches if "title" in m}
        self.assertEqual(dashboard_scores, {"Study partner: Priya Nair": 100})

        # Group 2 (CSE 3320, Online) is one the user is not in; group 6 is cancelled.
        group_scores = {
            m["group_name"]: m["match_score"] for m in matches if "group_name" in m
        }
        expected = {
            g["group_name"]: g["score"]
            for g in find_group_matches(self.conn, study_request)
        }
        self.assertEqual(group_scores, expected)

    def test_already_joined_groups_are_not_recommended(self):
        """User 1 is already in group 1 (CSE 3311), so it is not suggested."""
        self.add_request(1, "CSE 3311", "001", "Exam", "In-Person")
        names = [m.get("group_name") for m in get_user_smart_matches(self.conn, 1)]
        self.assertNotIn("Iteration 1 Review", names)

    def test_inactive_request_produces_no_matches(self):
        """Only active requests drive matches (same rule as the matching route)."""
        self.add_request(1, "CSE 3315", "001", "Exam", "In-Person", status="matched")
        self.assertEqual(get_user_smart_matches(self.conn, user_id=1), [])

    def test_user_never_sees_another_users_matches(self):
        """Matches come only from the logged-in user's own requests."""
        self.add_request(5, "MATH 1426", "004", "Exam", "Hybrid")  # user 5's private request
        self.add_request(6, "PHYS 1443", "001", "Exam", "Online")
        self.add_request(2, "MATH 1426", "004", "Exam", "Hybrid")  # a possible partner for 5

        # User 1 has no active request: nothing, even though others have matches.
        self.log_in(user_id=1)
        body = self.client.get("/dashboard").data.decode()
        self.assertIn("No smart matches found yet.", body)
        self.assertNotIn("Study partner:", body)

        # User 6 (PHYS request, no one else for it) sees none of user 5's matches.
        self.log_in(user_id=6, user_name="Taylor Brooks")
        body = self.client.get("/dashboard").data.decode()
        self.assertNotIn("Study partner: Alex Rivera", body)
        self.assertNotIn("Study partner: Sam Chen", body)

    def test_study_matches_route_still_hides_other_users_requests(self):
        """Existing behavior: /study-matches/<id> is 404 for another user's request."""
        request_id = self.add_request(5, "CSE 3315", "001", "Exam", "In-Person")
        self.log_in(user_id=1)
        self.assertEqual(self.client.get(f"/study-matches/{request_id}").status_code, 404)
        self.log_in(user_id=5, user_name="Sam Chen")
        self.assertEqual(self.client.get(f"/study-matches/{request_id}").status_code, 200)


class DashboardUnitHelperTests(DashboardTestCase):

    def test_is_meeting_in_future_logic(self):
        ref_now = datetime(2026, 11, 12, 12, 0)
        # Future date
        self.assertTrue(is_meeting_in_future("2026-11-13", "10:00", now=ref_now))
        # Later same day
        self.assertTrue(is_meeting_in_future("2026-11-12", "17:00", now=ref_now))
        # Earlier same day (past)
        self.assertFalse(is_meeting_in_future("2026-11-12", "09:00", now=ref_now))
        # Past date
        self.assertFalse(is_meeting_in_future("2026-11-10", "12:00", now=ref_now))
        # Invalid date
        self.assertFalse(is_meeting_in_future(None, "12:00", now=ref_now))

    def test_get_user_info_existing_user(self):
        user = get_user_info(self.conn, 1)
        self.assertIsNotNone(user)
        self.assertEqual(user["name"], "Demo Student")
        self.assertEqual(user["email"], "demo@example.com")

    def test_get_user_info_non_existent_user(self):
        user = get_user_info(self.conn, 9999)
        self.assertIsNone(user)

    def test_get_user_info_none_id(self):
        self.assertIsNone(get_user_info(self.conn, None))

    def test_get_user_joined_groups_ordering(self):
        groups = get_user_joined_groups(self.conn, 1)
        self.assertEqual(len(groups), 2)
        dates = [g["meeting_date"] for g in groups]
        self.assertEqual(dates, sorted(dates))

    def test_format_location_display(self):
        self.assertEqual(format_location_display("Online", "N/A"), "Online")
        self.assertEqual(format_location_display("In-Person", "ERB 128"), "ERB 128")
        self.assertEqual(format_location_display("In-Person", None), "Location TBA")
        self.assertEqual(format_location_display("Hybrid", "Library 204"), "Library 204 and online")
        self.assertEqual(format_location_display("Hybrid", None), "Hybrid (location TBA)")

    def test_derive_group_status(self):
        self.assertEqual(derive_group_status("open", 2, 6), "Open")
        self.assertEqual(derive_group_status("open", 6, 6), "Full")
        self.assertEqual(derive_group_status("completed", 5, 10), "Completed")
        self.assertEqual(derive_group_status("cancelled", 2, 6), "Cancelled")

    def test_get_dashboard_data_structure(self):
        data = get_dashboard_data(self.conn, 1)
        self.assertIn("user", data)
        self.assertIn("joined_groups", data)
        self.assertIn("upcoming_meetings", data)
        self.assertIn("study_requests", data)
        self.assertIn("smart_matches", data)
        self.assertIn("stats", data)
        self.assertEqual(data["stats"]["joined_groups_count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
