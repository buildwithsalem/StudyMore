import os
import sqlite3 as real_sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import app as app_module

REAL_CONNECT = real_sqlite3.connect


def when(days):
    dt = datetime.now() + timedelta(days=days)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


class Stage2Base(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.patcher = patch.object(
            app_module.sqlite3, "connect",
            lambda *a, **k: REAL_CONNECT(self.db_path),
        )
        self.patcher.start()
        app_module.init_db()
        conn = REAL_CONNECT(self.db_path)
        for uid, name in ((1, "Creator Person"), (2, "Member Two"), (3, "Hidden Three")):
            conn.execute(
                "INSERT INTO users (id, name, email, password) VALUES (?, ?, ?, 'x')",
                (uid, name, f"u{uid}@mavs.uta.edu"),
            )
        d, t = when(7)
        conn.execute(
            """INSERT INTO study_groups
               (id, creator_user_id, group_name, course_code, section, description,
                meeting_type, meeting_date, meeting_time, location, meeting_link,
                max_members, status)
               VALUES (1, 1, 'Group One', 'CSE 3311', '001', 'desc', 'In-Person',
                       ?, ?, 'ERB 128', '', 4, 'open')""",
            (d, t),
        )
        conn.executemany(
            "INSERT INTO group_memberships (user_id, group_id) VALUES (?, 1)",
            [(1,), (2,), (3,)],
        )
        conn.commit()
        conn.close()
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.patcher.stop()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def login(self, uid):
        with self.client.session_transaction() as s:
            s["user_id"] = uid

    def row(self, sql, args=()):
        conn = REAL_CONNECT(self.db_path)
        try:
            return conn.execute(sql, args).fetchone()
        finally:
            conn.close()


class TestEditGroup(Stage2Base):
    def form(self, **over):
        d, t = when(10)
        data = dict(group_name="Renamed", description="new desc", meeting_type="In-Person",
                    meeting_date=d, meeting_time=t, location="PKH 105",
                    meeting_link="", max_members="5")
        data.update(over)
        return data

    def test_requires_login(self):
        self.assertEqual(self.client.get("/group/1/edit").status_code, 302)

    def test_non_creator_forbidden(self):
        self.login(2)
        self.assertEqual(self.client.get("/group/1/edit").status_code, 403)
        self.assertEqual(self.client.post("/group/1/edit", data=self.form()).status_code, 403)
        self.assertEqual(self.row("SELECT group_name FROM study_groups WHERE id=1")[0], "Group One")

    def test_missing_group(self):
        self.login(1)
        self.assertEqual(self.client.get("/group/99/edit").status_code, 404)

    def test_creator_sees_prefilled_form(self):
        self.login(1)
        r = self.client.get("/group/1/edit")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Group One", r.data)
        self.assertIn(b"ERB 128", r.data)

    def test_creator_can_save(self):
        self.login(1)
        r = self.client.post("/group/1/edit", data=self.form())
        self.assertEqual(r.status_code, 302)
        row = self.row("SELECT group_name, location, max_members FROM study_groups WHERE id=1")
        self.assertEqual(tuple(row), ("Renamed", "PKH 105", 5))

    def test_past_date_rejected(self):
        self.login(1)
        d, t = when(-1)
        r = self.client.post("/group/1/edit", data=self.form(meeting_date=d, meeting_time=t))
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"must be in the future", r.data)
        self.assertEqual(self.row("SELECT group_name FROM study_groups WHERE id=1")[0], "Group One")

    def test_max_below_member_count_rejected(self):
        self.login(1)
        r = self.client.post("/group/1/edit", data=self.form(max_members="2"))
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"already has 3 members", r.data)

    def test_in_person_needs_location(self):
        self.login(1)
        r = self.client.post("/group/1/edit", data=self.form(location=""))
        self.assertEqual(r.status_code, 400)

    def test_online_needs_valid_link(self):
        self.login(1)
        r = self.client.post("/group/1/edit", data=self.form(meeting_type="Online", meeting_link="javascript:alert(1)"))
        self.assertEqual(r.status_code, 400)

    def test_edit_button_only_for_creator(self):
        self.login(1)
        self.assertIn(b"/group/1/edit", self.client.get("/group/1").data)
        self.login(2)
        self.assertNotIn(b"/group/1/edit", self.client.get("/group/1").data)

    def test_stale_open_group_explains_itself(self):
        d, t = when(-2)
        conn = REAL_CONNECT(self.db_path)
        conn.execute("UPDATE study_groups SET meeting_date=?, meeting_time=? WHERE id=1", (d, t))
        conn.commit(); conn.close()
        self.login(1)
        body = self.client.get("/group/1").data
        self.assertIn(b"already passed", body)
        self.assertIn(b"Use Edit Details", body)


class TestProfileAndMembers(Stage2Base):
    def profile_form(self, **over):
        data = dict(display_name="Creator Person", avatar_color="teal", show_in_groups="on",
                    class_year="Junior", major="Computer Science", study_style="Quiet focus")
        data.update(over)
        return data

    def test_avatar_and_privacy_saved(self):
        self.login(1)
        r = self.client.post("/profile", data=self.profile_form())
        self.assertEqual(r.status_code, 200)
        row = self.row("SELECT avatar_color, show_in_groups FROM user_profiles WHERE user_id=1")
        self.assertEqual(tuple(row), ("teal", 1))

    def test_invalid_avatar_rejected(self):
        self.login(1)
        r = self.client.post("/profile", data=self.profile_form(avatar_color="neon"))
        self.assertEqual(r.status_code, 400)

    def test_old_table_is_migrated(self):
        conn = REAL_CONNECT(self.db_path)
        conn.execute("DROP TABLE IF EXISTS user_profiles")
        conn.execute("CREATE TABLE user_profiles (user_id INTEGER PRIMARY KEY, bio TEXT, major TEXT, minor TEXT, class_year TEXT, grad_term TEXT, courses TEXT, study_style TEXT, meeting_pref TEXT, group_size TEXT, study_goals TEXT, can_help TEXT, need_help TEXT, interests TEXT)")
        conn.execute("INSERT INTO user_profiles (user_id, major) VALUES (2, 'Biology')")
        conn.commit(); conn.close()
        self.login(2)
        self.assertEqual(self.client.get("/profile").status_code, 200)
        self.assertEqual(self.client.get("/group/1").status_code, 200)

    def test_members_listed_with_profile_info(self):
        self.login(1)
        self.client.post("/profile", data=self.profile_form())
        self.login(2)
        body = self.client.get("/group/1").data
        self.assertIn(b"Creator Person", body)
        self.assertIn(b"Member Two", body)
        self.assertIn(b"Junior", body)
        self.assertIn(b"Computer Science", body)
        self.assertIn(b"Quiet focus", body)
        self.assertIn(b"Creator</span>", body)

    def test_hidden_profile_shows_name_only(self):
        self.login(3)
        self.client.post("/profile", data=self.profile_form(
            display_name="Hidden Three", class_year="Senior", major="Secret Major",
            study_style="Flashcards and quizzes", show_in_groups=""))
        self.login(2)
        body = self.client.get("/group/1").data
        self.assertIn(b"Hidden Three", body)
        self.assertNotIn(b"Secret Major", body)
        self.assertNotIn(b"Senior", body)

    def test_emails_never_shown(self):
        self.login(2)
        self.assertNotIn(b"mavs.uta.edu", self.client.get("/group/1").data)


if __name__ == "__main__":
    unittest.main()
