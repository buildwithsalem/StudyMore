"""
Regression tests for database configuration, meeting-link safety, error
handling, SQL safety, and group creation/membership.

    python -m unittest test_integration -v

Every test builds its own database in a temp folder and points the app at it
with app.config["DATABASE"]. The working directory is deliberately a DIFFERENT
empty folder, so any code that still opened "study_more.db" relative to the
working directory would create a stray file there and the tests would fail.
"""

import logging
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import app as app_module
from dashboard import get_user_study_requests
from demo_data import insert_demo_data
from validators import safe_meeting_url, validate_meeting_link


class IntegrationTestCase(unittest.TestCase):

    def setUp(self):
        self.original_cwd = os.getcwd()
        self.db_dir = tempfile.mkdtemp(prefix="studymore-db-")
        self.cwd_dir = tempfile.mkdtemp(prefix="studymore-cwd-")
        os.chdir(self.cwd_dir)

        self.db_path = os.path.join(self.db_dir, "configured.db")
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
        shutil.rmtree(self.db_dir, ignore_errors=True)
        shutil.rmtree(self.cwd_dir, ignore_errors=True)

    def log_in(self, user_id=1, user_name="Demo Student"):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_name"] = user_name

    def group_form(self, **overrides):
        data = {
            "group_name": "Regression Group",
            "course_code": "CSE 3311",
            "section": "001",
            "study_goal": "Exam",
            "description": "Testing",
            "meeting_type": "Online",
            "meeting_date": "2099-01-01",
            "meeting_time": "10:00",
            "location": "",
            "meeting_link": "https://uta.zoom.us/j/123456789",
            "max_members": "4",
        }
        data.update(overrides)
        return data

    def stored_groups(self, name="Regression Group"):
        return self.conn.execute(
            "SELECT * FROM study_groups WHERE group_name = ?", (name,)
        ).fetchall()

    def stray_files(self):
        return os.listdir(self.cwd_dir)


class DatabaseConfigurationTests(IntegrationTestCase):

    def test_dashboard_uses_the_configured_database(self):
        self.conn.execute("UPDATE users SET name = 'Configured DB User' WHERE id = 1")
        self.conn.commit()
        self.log_in()
        body = self.client.get("/dashboard").data.decode()
        self.assertIn("Welcome back, Configured DB User!", body)

    def test_group_created_through_the_app_is_visible_on_the_dashboard(self):
        """app.py routes and the dashboard blueprint read/write the same database."""
        self.log_in()
        response = self.client.post("/create-group", data=self.group_form())
        self.assertEqual(response.status_code, 302)
        body = self.client.get("/dashboard").data.decode()
        self.assertIn("Regression Group", body)

    def test_study_request_saved_by_the_app_appears_on_the_dashboard(self):
        self.log_in()
        self.client.post("/availability", data={"availability": ["Monday|09:00|10:00"]})
        response = self.client.post("/study-request", data={
            "course_code": "cse 3315", "section": "001", "study_goal": "Exam",
            "meeting_preference": "In-Person", "note": "",
        })
        self.assertEqual(response.status_code, 302)
        body = self.client.get("/dashboard").data.decode()
        self.assertIn("CSE 3315 Study Request", body)

    def test_nothing_is_written_to_a_database_in_the_working_directory(self):
        self.log_in()
        self.client.post("/create-group", data=self.group_form())
        self.client.get("/my-groups")
        self.client.get("/dashboard")
        self.client.get("/search")
        self.client.post("/availability", data={"availability": ["Monday|09:00|10:00"]})
        self.client.post("/register", data={
            "name": "N", "email": "n@example.com",
            "password": "pw", "confirm_password": "pw"})
        self.assertEqual(self.stray_files(), [])
        self.assertTrue(self.conn.execute(
            "SELECT 1 FROM users WHERE email = 'n@example.com'").fetchone())

    def test_changing_the_config_switches_every_route_to_the_other_database(self):
        other = os.path.join(self.db_dir, "other.db")
        app_module.init_db(other)
        other_conn = sqlite3.connect(other)
        other_conn.execute(
            "INSERT INTO users (id, name, email, password) VALUES (1, 'Other DB User', 'o@x.com', 'h')")
        other_conn.commit()
        other_conn.close()

        self.flask_app.config["DATABASE"] = other
        self.log_in()
        self.assertIn("Welcome back, Other DB User!",
                      self.client.get("/dashboard").data.decode())
        self.assertEqual(self.client.get("/my-groups").status_code, 200)
        self.assertEqual(self.client.get("/search").status_code, 200)

    def test_init_db_is_repeatable_and_keeps_existing_data(self):
        app_module.init_db(self.db_path)
        app_module.init_db(self.db_path)
        count = self.conn.execute("SELECT COUNT(*) FROM study_groups").fetchone()[0]
        self.assertEqual(count, 7)

    def test_connections_are_closed_when_a_route_raises(self):
        opened = []
        real_open = app_module.get_db_connection

        def tracking():
            conn = real_open()
            opened.append(conn)
            return conn

        self.log_in()
        original_propagate = self.flask_app.config.get("PROPAGATE_EXCEPTIONS")
        self.flask_app.config["PROPAGATE_EXCEPTIONS"] = False
        try:
            with mock.patch.object(app_module, "get_db_connection", tracking), \
                    mock.patch("app.render_template", side_effect=RuntimeError("boom")):
                response = self.client.get("/my-groups")
        finally:
            self.flask_app.config["PROPAGATE_EXCEPTIONS"] = original_propagate
        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(opened), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")  # closed by the teardown hook


class MeetingLinkValidationTests(IntegrationTestCase):

    def test_validator_accepts_common_meeting_links(self):
        for url in (
            "https://uta.zoom.us/j/1234567890?pwd=abc",
            "https://teams.microsoft.com/l/meetup-join/19%3ameeting",
            "https://meet.google.com/abc-defg-hij",
            "http://example.com/room",
            "HTTPS://Meet.Example.com/Room",
        ):
            clean, error = validate_meeting_link(url)
            self.assertIsNone(error, url)
            self.assertEqual(clean, url)

    def test_validator_allows_blank(self):
        self.assertEqual(validate_meeting_link(""), ("", None))
        self.assertEqual(validate_meeting_link("   "), ("", None))
        self.assertEqual(validate_meeting_link(None), ("", None))

    def test_validator_rejects_unsafe_or_malformed_links(self):
        for url in (
            "javascript:alert(1)",
            "JaVaScRiPt:alert(1)",
            " javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
            "ftp://example.com/file",
            "mailto:a@b.com",
            "//example.com/room",
            "/relative/path",
            "example.com/no-scheme",
            "not a url",
            "http://",
            "https:///path-only",
            "https://nodot",
            "http://exa mple.com",
            "https://example.com/\" onclick=\"alert(1)",
            "https://user:pw@example.com/room",
            "https://example.com:99999/",
            "https://[::1",
            "https://" + "a" * 3000 + ".com",
        ):
            clean, error = validate_meeting_link(url)
            self.assertIsNone(clean, url)
            self.assertTrue(error, url)

    def test_create_group_stores_a_valid_https_link(self):
        self.log_in()
        response = self.client.post("/create-group", data=self.group_form())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stored_groups()[0]["meeting_link"],
                         "https://uta.zoom.us/j/123456789")

    def test_create_group_stores_a_valid_http_link(self):
        self.log_in()
        response = self.client.post("/create-group",
                                    data=self.group_form(meeting_link="http://example.com/room"))
        self.assertEqual(response.status_code, 302)

    def test_empty_link_is_allowed_for_in_person_groups(self):
        self.log_in()
        response = self.client.post("/create-group", data=self.group_form(
            meeting_type="In-Person", location="ERB 128", meeting_link=""))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.stored_groups()), 1)

    def test_empty_link_is_still_rejected_for_online_groups(self):
        self.log_in()
        response = self.client.post("/create-group", data=self.group_form(meeting_link=""))
        self.assertIn(b"Meeting link is required", response.data)
        self.assertEqual(self.stored_groups(), [])

    def test_create_group_rejects_dangerous_and_malformed_links(self):
        self.log_in()
        for bad in ("javascript:alert(1)", "data:text/html,<b>x</b>",
                    "not a url", "https:///nohost", "http://"):
            response = self.client.post("/create-group", data=self.group_form(meeting_link=bad))
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn(b"valid meeting link", response.data, bad)
        self.assertEqual(self.stored_groups(), [])

    def test_unsafe_link_is_rejected_even_for_in_person_groups(self):
        self.log_in()
        response = self.client.post("/create-group", data=self.group_form(
            meeting_type="In-Person", location="ERB 128", meeting_link="javascript:alert(1)"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stored_groups(), [])

    def test_templates_do_not_render_unsafe_links_already_in_the_database(self):
        """Old rows may hold bad links; they must never reach an href."""
        self.conn.execute(
            "UPDATE study_groups SET meeting_link = ? WHERE id = 1", ("javascript:alert(1)",))
        self.conn.execute(
            "UPDATE study_groups SET meeting_link = ?, meeting_date = '2099-01-01' WHERE id = 3",
            ("data:text/html,<script>alert(1)</script>",))
        self.conn.commit()
        self.log_in()
        for path in ("/group/1", "/group/3", "/my-groups", "/dashboard"):
            body = self.client.get(path).data.decode()
            self.assertNotIn("javascript:", body, path)
            self.assertNotIn("data:text/html", body, path)

    def test_templates_still_render_valid_links(self):
        self.log_in()
        self.assertIn('href="https://uta.zoom.us/j/2233445566"',
                      self.client.get("/group/3").data.decode())
        self.assertIn('href="https://uta.zoom.us/j/2233445566"',
                      self.client.get("/my-groups").data.decode())

    def test_safe_meeting_url_filter(self):
        self.assertEqual(safe_meeting_url("https://example.com/a"), "https://example.com/a")
        self.assertIsNone(safe_meeting_url("javascript:alert(1)"))
        self.assertIsNone(safe_meeting_url(""))
        self.assertIsNone(safe_meeting_url(None))


class DatabaseErrorHandlingTests(IntegrationTestCase):

    def test_zero_rows_is_a_normal_empty_state(self):
        self.assertEqual(get_user_study_requests(self.conn, 1), [])
        self.log_in()
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("No active study requests at this time.", response.data.decode())

    def test_missing_required_table_is_not_reported_as_empty(self):
        self.conn.execute("DROP TABLE study_requests")
        self.conn.commit()
        with self.assertRaises(sqlite3.OperationalError):
            get_user_study_requests(self.conn, 1)

    def test_missing_required_column_is_not_reported_as_empty(self):
        self.conn.execute("DROP TABLE study_requests")
        self.conn.execute("CREATE TABLE study_requests (id INTEGER PRIMARY KEY, user_id INTEGER)")
        self.conn.commit()
        with self.assertRaises(sqlite3.OperationalError):
            get_user_study_requests(self.conn, 1)

    def test_broken_schema_gives_a_safe_500_and_is_logged(self):
        self.conn.execute("DROP TABLE study_requests")
        self.conn.commit()
        self.log_in()
        with self.assertLogs(self.flask_app.logger, level=logging.ERROR) as logs:
            response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 500)
        body = response.data.decode().lower()
        self.assertNotIn("study_requests", body)
        self.assertNotIn("sqlite", body)
        self.assertNotIn("select", body)
        self.assertNotIn("traceback", body)
        self.assertTrue(any("no such table" in line for line in logs.output))

    def test_database_errors_are_propagated_in_debug_mode(self):
        self.conn.execute("DROP TABLE study_requests")
        self.conn.commit()
        self.log_in()
        self.flask_app.debug = True
        try:
            with self.assertLogs(self.flask_app.logger, level=logging.ERROR):
                with self.assertRaises(sqlite3.OperationalError):
                    self.client.get("/dashboard")
        finally:
            self.flask_app.debug = False

    def test_normal_dashboard_request_still_works(self):
        self.log_in()
        self.assertEqual(self.client.get("/dashboard").status_code, 200)


class SqlSafetyTests(IntegrationTestCase):

    INJECTION = "CSE3311' OR '1'='1"

    def test_malicious_study_request_values_are_stored_literally(self):
        self.log_in()
        self.client.post("/availability", data={"availability": ["Monday|09:00|10:00"]})
        payload = "x'); DROP TABLE users;--"
        response = self.client.post("/study-request", data={
            "course_code": payload, "section": payload, "study_goal": "Exam",
            "meeting_preference": "Online", "note": payload})
        self.assertEqual(response.status_code, 302)
        row = self.conn.execute(
            "SELECT course_code, note FROM study_requests").fetchone()
        self.assertEqual(row["note"], payload)
        self.assertEqual(row["course_code"], payload.upper())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 8)

    def test_injection_in_course_code_does_not_widen_matching(self):
        """A crafted course code must match nothing, not every group/request."""
        self.conn.execute(
            "INSERT INTO study_requests (user_id, course_code, section, study_goal, "
            "meeting_preference, status) VALUES (1, ?, '', 'Exam', 'Online', 'active')",
            (self.INJECTION,))
        self.conn.execute(
            "INSERT INTO study_requests (user_id, course_code, section, study_goal, "
            "meeting_preference, status) VALUES (2, 'CSE 3311', '', 'Exam', 'Online', 'active')")
        self.conn.commit()
        from dashboard import get_user_smart_matches
        self.assertEqual(get_user_smart_matches(self.conn, 1), [])

    def test_injection_in_group_fields_does_not_alter_other_rows(self):
        self.log_in()
        payload = "x', 1); DELETE FROM study_groups;--"
        response = self.client.post("/create-group", data=self.group_form(
            group_name=payload, description=payload))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM study_groups").fetchone()[0], 8)
        self.assertEqual(len(self.stored_groups(payload)), 1)

    def test_injection_in_login_and_search_is_harmless(self):
        response = self.client.post("/login", data={
            "email": "' OR '1'='1", "password": "x"})
        self.assertEqual(response.status_code, 400)
        self.log_in()
        response = self.client.get("/search", query_string={"term": "' OR 1=1 --"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("No study groups match", response.data.decode())


class GroupCreationAndMembershipTests(IntegrationTestCase):

    def test_creator_is_a_member_and_sees_the_group_everywhere(self):
        self.log_in()
        response = self.client.post("/create-group", data=self.group_form())
        group = self.stored_groups()[0]
        self.assertEqual(group["creator_user_id"], 1)
        self.assertTrue(self.conn.execute(
            "SELECT 1 FROM group_memberships WHERE group_id = ? AND user_id = 1",
            (group["id"],)).fetchone())
        self.assertIn(f"/group/{group['id']}", response.headers["Location"])

        self.assertIn("Regression Group", self.client.get("/my-groups").data.decode())
        dashboard = self.client.get("/dashboard").data.decode()
        self.assertIn("Regression Group", dashboard)
        self.assertIn("Regression Group", self.client.get("/search").data.decode())

        # Another student does not see it in their own My Groups.
        self.log_in(user_id=8, user_name="Casey Nguyen")
        other = self.client.get("/my-groups").data.decode()
        self.assertNotIn("Regression Group", other)

    def test_join_and_leave_still_work(self):
        self.log_in()
        self.client.post("/create-group", data=self.group_form())
        gid = self.stored_groups()[0]["id"]

        self.log_in(user_id=8, user_name="Casey Nguyen")
        self.assertEqual(self.client.post(f"/group/{gid}/join").status_code, 302)
        self.assertIn("Regression Group", self.client.get("/my-groups").data.decode())
        self.assertEqual(self.client.post(f"/group/{gid}/join").status_code, 400)
        self.assertEqual(self.client.post(f"/group/{gid}/leave").status_code, 302)
        self.assertNotIn("Regression Group", self.client.get("/my-groups").data.decode())

        # The creator is unaffected by someone else leaving.
        self.log_in()
        self.assertIn("Regression Group", self.client.get("/my-groups").data.decode())

    def test_full_group_cannot_be_joined(self):
        self.log_in()
        self.client.post("/create-group", data=self.group_form(max_members="2"))
        gid = self.stored_groups()[0]["id"]
        self.log_in(user_id=2, user_name="Alex Rivera")
        self.assertEqual(self.client.post(f"/group/{gid}/join").status_code, 302)
        self.log_in(user_id=3, user_name="Priya Nair")
        self.assertEqual(self.client.post(f"/group/{gid}/join").status_code, 400)

    def test_arbitrary_partner_id_does_not_enroll_another_user(self):
        """partner_id alone (no validated study-request match) must not add anyone."""
        self.log_in()
        self.client.post("/create-group", data=self.group_form(partner_id="8"))
        gid = self.stored_groups()[0]["id"]
        members = {r[0] for r in self.conn.execute(
            "SELECT user_id FROM group_memberships WHERE group_id = ?", (gid,))}
        self.assertEqual(members, {1})

    def test_validated_study_match_still_adds_the_partner(self):
        self.conn.execute(
            "INSERT INTO study_requests (id, user_id, course_code, study_goal, "
            "meeting_preference, status) VALUES (101, 1, 'CSE 3311', 'Exam', 'Online', 'active')")
        self.conn.execute(
            "INSERT INTO study_requests (id, user_id, course_code, study_goal, "
            "meeting_preference, status) VALUES (102, 8, 'CSE 3311', 'Exam', 'Online', 'active')")
        self.conn.commit()
        self.log_in()
        response = self.client.post("/create-group", data=self.group_form(
            partner_id="8", request_id="101", partner_request_id="102"))
        self.assertEqual(response.status_code, 302)
        gid = self.stored_groups()[0]["id"]
        members = {r[0] for r in self.conn.execute(
            "SELECT user_id FROM group_memberships WHERE group_id = ?", (gid,))}
        self.assertEqual(members, {1, 8})
        statuses = {r[0] for r in self.conn.execute(
            "SELECT status FROM study_requests WHERE id IN (101, 102)")}
        self.assertEqual(statuses, {"matched"})

    def test_invalid_study_match_is_rejected_and_creates_nothing(self):
        self.log_in()
        response = self.client.post("/create-group", data=self.group_form(
            partner_id="8", request_id="9999", partner_request_id="9998"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stored_groups(), [])


if __name__ == "__main__":
    unittest.main()
