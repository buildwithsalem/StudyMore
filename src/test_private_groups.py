import os
import sqlite3 as real_sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import app as app_module
import duplicate_query
import invitations

REAL_CONNECT = real_sqlite3.connect


def future(days=7):
    dt = datetime.now() + timedelta(days=days)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


class PrivateGroupBase(unittest.TestCase):
    """Users: 1 creator, 2 outsider, 3 invited student, 4 member.
    Group 1 is private, group 2 is public (same course)."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.patcher = patch.object(
            app_module.sqlite3, "connect", lambda *a, **k: REAL_CONNECT(self.db_path)
        )
        self.patcher.start()
        app_module.init_db()
        conn = REAL_CONNECT(self.db_path)
        for uid, name in ((1, "Creator"), (2, "Outsider"), (3, "Invited"), (4, "Member")):
            conn.execute(
                "INSERT INTO users (id, name, email, password) VALUES (?, ?, ?, 'x')",
                (uid, name, f"u{uid}@mavs.uta.edu"),
            )
        d, t = future()
        for gid, name, private in ((1, "Secret Group", 1), (2, "Open Group", 0)):
            conn.execute(
                """INSERT INTO study_groups
                   (id, creator_user_id, group_name, course_code, section, description,
                    meeting_type, meeting_date, meeting_time, location, meeting_link,
                    max_members, status, is_private)
                   VALUES (?, 1, ?, 'CSE 3311', '001', 'desc', 'In-Person', ?, ?,
                           'Library', NULL, 5, 'open', ?)""",
                (gid, name, d, t, private),
            )
            conn.execute(
                "INSERT INTO group_memberships (user_id, group_id) VALUES (1, ?)", (gid,)
            )
        conn.execute("INSERT INTO group_memberships (user_id, group_id) VALUES (4, 1)")
        conn.execute(
            "INSERT INTO group_invitations (group_id, inviter_id, invitee_id, status, created_at) "
            "VALUES (1, 1, 3, 'pending', ?)",
            (datetime.now().strftime(invitations.TIME_FORMAT),),
        )
        conn.commit()
        conn.close()
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.patcher.stop()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def login(self, uid):
        with self.client.session_transaction() as s:
            s["user_id"] = uid

    def db(self):
        conn = REAL_CONNECT(self.db_path)
        conn.row_factory = real_sqlite3.Row
        return conn

    def edit_form(self, **extra):
        d, t = future(10)
        form = {
            "group_name": "Secret Group", "description": "desc",
            "meeting_type": "In-Person", "meeting_date": d, "meeting_time": t,
            "location": "Library", "meeting_link": "", "max_members": "5",
        }
        form.update(extra)
        return form


class PrivateGroupTests(PrivateGroupBase):
    def test_groups_default_to_public(self):
        conn = self.db()
        conn.execute(
            """INSERT INTO study_groups
               (creator_user_id, group_name, course_code, description, meeting_type,
                meeting_date, meeting_time, max_members)
               VALUES (1, 'Plain', 'CSE 1310', 'd', 'Online', '2030-01-01', '10:00', 4)"""
        )
        conn.commit()
        row = conn.execute("SELECT is_private FROM study_groups WHERE group_name='Plain'").fetchone()
        conn.close()
        self.assertEqual(row["is_private"], 0)

    def test_duplicate_warning_hides_private_from_outsider(self):
        conn = self.db()
        names = [r["group_name"] for r in duplicate_query.groups_in_course(conn, "CSE 3311", 2)]
        conn.close()
        self.assertIn("Open Group", names)
        self.assertNotIn("Secret Group", names)

    def test_duplicate_warning_shows_private_to_member(self):
        conn = self.db()
        names = [r["group_name"] for r in duplicate_query.groups_in_course(conn, "CSE 3311", 4)]
        conn.close()
        self.assertIn("Secret Group", names)

    def test_outsider_cannot_open_private_group_page(self):
        self.login(2)
        self.assertEqual(self.client.get("/group/1").status_code, 404)

    def test_member_and_creator_can_open_private_group_page(self):
        for uid in (1, 4):
            self.login(uid)
            self.assertEqual(self.client.get("/group/1").status_code, 200)

    def test_invited_student_can_open_private_group_page(self):
        self.login(3)
        self.assertEqual(self.client.get("/group/1").status_code, 200)

    def test_outsider_cannot_join_private_group(self):
        self.login(2)
        resp = self.client.post("/group/1/join")
        self.assertEqual(resp.status_code, 403)
        conn = self.db()
        count = conn.execute(
            "SELECT COUNT(*) FROM group_memberships WHERE user_id=2 AND group_id=1"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_outsider_can_still_join_public_group(self):
        self.login(2)
        self.assertNotEqual(self.client.post("/group/2/join").status_code, 403)

    def test_invitation_acceptance_still_works_for_private_group(self):
        self.login(3)
        conn = self.db()
        inv = conn.execute("SELECT id FROM group_invitations WHERE invitee_id=3").fetchone()
        conn.close()
        self.client.post(f"/invitations/{inv['id']}/accept")
        conn = self.db()
        count = conn.execute(
            "SELECT COUNT(*) FROM group_memberships WHERE user_id=3 AND group_id=1"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_creator_can_make_group_public_and_private_again(self):
        self.login(1)
        self.client.post("/group/1/edit", data=self.edit_form())  # box unticked
        conn = self.db()
        self.assertEqual(conn.execute("SELECT is_private FROM study_groups WHERE id=1").fetchone()[0], 0)
        conn.close()
        self.client.post("/group/1/edit", data=self.edit_form(is_private="1"))
        conn = self.db()
        self.assertEqual(conn.execute("SELECT is_private FROM study_groups WHERE id=1").fetchone()[0], 1)
        conn.close()

    def test_non_creator_cannot_change_privacy(self):
        self.login(4)
        resp = self.client.post("/group/1/edit", data=self.edit_form())
        self.assertEqual(resp.status_code, 403)
        conn = self.db()
        self.assertEqual(conn.execute("SELECT is_private FROM study_groups WHERE id=1").fetchone()[0], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
