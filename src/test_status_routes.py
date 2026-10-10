import os
import sqlite3 as real_sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import app as app_module


def future_date_time():
    dt = datetime.now() + timedelta(days=7)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def past_date_time():
    dt = datetime.now() - timedelta(days=1)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


class TestStatusRoutes(unittest.TestCase):

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")

        self.patcher = patch.object(
            app_module.sqlite3,
            "connect",
            lambda *args, **kwargs: real_sqlite3.connect(self.db_path)
        )
        self.patcher.start()

        app_module.init_db()

        conn = real_sqlite3.connect(self.db_path)
        conn.row_factory = real_sqlite3.Row

        conn.execute(
            "INSERT INTO users (id, name, email, password) "
            "VALUES (1, 'Creator', 'creator@test.com', 'x')"
        )
        conn.execute(
            "INSERT INTO users (id, name, email, password) "
            "VALUES (2, 'Other', 'other@test.com', 'x')"
        )

        future_date, future_time = future_date_time()

        conn.execute(
            """
            INSERT INTO study_groups
                (id, creator_user_id, group_name, course_code, section,
                 description, meeting_type, meeting_date, meeting_time,
                 location, meeting_link, max_members, status)
            VALUES (1, 1, 'Open Group', 'CSE 3311', '001',
                    'desc', 'Online', ?, ?, '', 'http://x', 2, 'open')
            """,
            (future_date, future_time)
        )

        conn.execute(
            """
            INSERT INTO study_groups
                (id, creator_user_id, group_name, course_code, section,
                 description, meeting_type, meeting_date, meeting_time,
                 location, meeting_link, max_members, status)
            VALUES (2, 1, 'Full Group', 'CSE 3311', '001',
                    'desc', 'Online', ?, ?, '', 'http://x', 1, 'open')
            """,
            (future_date, future_time)
        )
        conn.execute(
            "INSERT INTO group_memberships (user_id, group_id) VALUES (1, 2)"
        )

        conn.execute(
            """
            INSERT INTO study_groups
                (id, creator_user_id, group_name, course_code, section,
                 description, meeting_type, meeting_date, meeting_time,
                 location, meeting_link, max_members, status)
            VALUES (3, 1, 'Cancelled Group', 'CSE 3311', '001',
                    'desc', 'Online', ?, ?, '', 'http://x', 6, 'Cancelled')
            """,
            (future_date, future_time)
        )

        past_date, past_time = past_date_time()
        conn.execute(
            """
            INSERT INTO study_groups
                (id, creator_user_id, group_name, course_code, section,
                 description, meeting_type, meeting_date, meeting_time,
                 location, meeting_link, max_members, status)
            VALUES (4, 1, 'Past Group', 'CSE 3311', '001',
                    'desc', 'Online', ?, ?, '', 'http://x', 6, 'open')
            """,
            (past_date, past_time)
        )

        conn.commit()
        conn.close()

        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.patcher.stop()
        os.close(self.db_fd)
        os.remove(self.db_path)

    def login_as(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def test_join_open_group_succeeds(self):
        self.login_as(2)
        response = self.client.post("/group/1/join")
        self.assertEqual(response.status_code, 302)

        conn = real_sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT 1 FROM group_memberships WHERE user_id = 2 AND group_id = 1"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_join_full_group_blocked(self):
        self.login_as(2)
        response = self.client.post("/group/2/join")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"full", response.data.lower())

    def test_join_cancelled_group_blocked(self):
        self.login_as(2)
        response = self.client.post("/group/3/join")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"cancelled", response.data.lower())

    def test_join_past_meeting_group_blocked(self):
        self.login_as(2)
        response = self.client.post("/group/4/join")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"completed", response.data.lower())

    def test_join_already_member_blocked(self):
        self.login_as(1)
        response = self.client.post("/group/2/join")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"already joined", response.data.lower())

    def test_set_status_as_creator_succeeds(self):
        self.login_as(1)
        response = self.client.post(
            "/group/1/status", data={"status": "Completed"}
        )
        self.assertEqual(response.status_code, 302)

        conn = real_sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT status FROM study_groups WHERE id = 1"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "Completed")

    def test_set_status_as_non_creator_forbidden(self):
        self.login_as(2)
        response = self.client.post(
            "/group/1/status", data={"status": "Completed"}
        )
        self.assertEqual(response.status_code, 403)

    def test_set_status_invalid_value_rejected(self):
        self.login_as(1)
        response = self.client.post(
            "/group/1/status", data={"status": "Banana"}
        )
        self.assertEqual(response.status_code, 400)

    def test_set_status_reopen_stores_lowercase(self):
        self.login_as(1)
        self.client.post("/group/1/status", data={"status": "Cancelled"})
        self.client.post("/group/1/status", data={"status": "Open"})

        conn = real_sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT status FROM study_groups WHERE id = 1"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "open")


if __name__ == "__main__":
    unittest.main()
