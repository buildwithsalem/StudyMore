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
        # Work in a temporary directory for isolation
        self.original_cwd = os.getcwd()
        self.tmp_dir = tempfile.mkdtemp(prefix="studymore-dashboard-test-")
        os.chdir(self.tmp_dir)

        app_module.init_db()

        self.db_path = os.path.join(self.tmp_dir, DB_NAME)
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

    def test_smart_matches_graceful_when_table_missing(self):
        """get_user_smart_matches returns empty list without error when table does not exist."""
        matches = get_user_smart_matches(self.conn, user_id=1)
        self.assertEqual(matches, [])

    def test_smart_matches_surfaced_when_table_exists(self):
        """When smart_matches table is created, dashboard queries and surfaces matches."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS smart_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                group_id INTEGER,
                group_name TEXT,
                course_code TEXT,
                match_score INTEGER,
                reason TEXT
            )
        """)
        self.conn.execute("""
            INSERT INTO smart_matches (user_id, group_id, group_name, course_code, match_score, reason)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (1, 4, "Algorithms Problem Set", "CSE 3315", 95, "Matches your enrolled course and available hours"))
        self.conn.commit()

        matches = get_user_smart_matches(self.conn, user_id=1)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["course_code"], "CSE 3315")
        self.assertEqual(matches[0]["match_score"], 95)

        # Verify it renders on the dashboard HTML
        self.log_in(user_id=1)
        body = self.client.get("/dashboard").data.decode()
        self.assertIn("Algorithms Problem Set", body)
        self.assertIn("95% Match", body)
        self.assertIn("Matches your enrolled course and available hours", body)


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
