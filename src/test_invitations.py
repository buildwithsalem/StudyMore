import os
import sqlite3 as real_sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import app as app_module
import invitations

REAL_CONNECT = real_sqlite3.connect


def when(days):
    dt = datetime.now() + timedelta(days=days)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


class InviteBase(unittest.TestCase):
    """Users: 1 creator, 2 and 3 students, 4 a student in the group, 5 unverified."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.patcher = patch.object(
            app_module.sqlite3, "connect", lambda *a, **k: REAL_CONNECT(self.db_path)
        )
        self.patcher.start()
        app_module.init_db()
        conn = REAL_CONNECT(self.db_path)
        for uid, name in ((1, "Creator"), (2, "Student Two"), (3, "Student Three"), (4, "Member Four")):
            conn.execute(
                "INSERT INTO users (id, name, email, password) VALUES (?, ?, ?, 'x')",
                (uid, name, f"u{uid}@mavs.uta.edu"),
            )
        conn.execute(
            "INSERT INTO users (id, name, email, password, email_verified) "
            "VALUES (5, 'Unverified', 'u5@mavs.uta.edu', 'x', 0)"
        )
        d, t = when(7)
        conn.execute(
            """INSERT INTO study_groups
               (id, creator_user_id, group_name, course_code, section, description,
                meeting_type, meeting_date, meeting_time, location, meeting_link,
                max_members, status)
               VALUES (1, 1, 'Group One', 'CSE 3311', '001', 'desc', 'In-Person',
                       ?, ?, 'ERB 128', '', 3, 'open')""",
            (d, t),
        )
        conn.executemany(
            "INSERT INTO group_memberships (user_id, group_id) VALUES (?, 1)", [(1,), (4,)]
        )
        conn.commit()
        conn.close()
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.patcher.stop()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def login(self, uid, name=None):
        with self.client.session_transaction() as s:
            s["user_id"] = uid
            s["user_name"] = name or f"User {uid}"

    def invite(self, email, group_id=1):
        self.login(1, "Creator")
        return self.client.post(f"/group/{group_id}/invite", data={"email": email})

    def rows(self, sql, args=()):
        conn = REAL_CONNECT(self.db_path)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def exec(self, sql, args=()):
        conn = REAL_CONNECT(self.db_path)
        conn.execute(sql, args)
        conn.commit()
        conn.close()

    def invite_id(self):
        return self.rows("SELECT id FROM group_invitations ORDER BY id DESC")[0][0]

    def members(self):
        return {r[0] for r in self.rows("SELECT user_id FROM group_memberships WHERE group_id = 1")}


class TestSendingInvitations(InviteBase):
    def test_creator_invites_a_student(self):
        r = self.invite("u2@mavs.uta.edu")
        self.assertEqual(r.status_code, 302)
        row = self.rows("SELECT group_id, inviter_id, invitee_id, status FROM group_invitations")[0]
        self.assertEqual(tuple(row), (1, 1, 2, "pending"))

    def test_email_is_trimmed_and_case_insensitive(self):
        self.assertEqual(self.invite("  U2@MAVS.UTA.EDU ").status_code, 302)

    def test_invite_creates_notification_for_invitee(self):
        self.invite("u2@mavs.uta.edu")
        n = self.rows("SELECT user_id, kind, link_url, is_read, group_id, related_id FROM notifications")
        self.assertEqual(len(n), 1)
        self.assertEqual((n[0][0], n[0][1], n[0][2], n[0][3], n[0][4]), (2, "group_invitation", "/invitations", 0, 1))
        self.assertEqual(n[0][5], self.invite_id())

    def test_requires_login(self):
        self.assertEqual(self.client.post("/group/1/invite", data={"email": "u2@mavs.uta.edu"}).status_code, 302)
        self.assertEqual(self.client.get("/group/1/manage").status_code, 302)
        self.assertEqual(self.client.get("/invitations").status_code, 302)

    def test_only_creator_can_invite_or_view_manage_page(self):
        self.login(4)
        self.assertEqual(self.client.post("/group/1/invite", data={"email": "u2@mavs.uta.edu"}).status_code, 403)
        self.assertEqual(self.client.get("/group/1/manage").status_code, 403)
        self.assertEqual(self.rows("SELECT * FROM group_invitations"), [])

    def test_missing_group(self):
        self.login(1)
        self.assertEqual(self.client.get("/group/99/manage").status_code, 404)
        self.assertEqual(self.client.post("/group/99/invite", data={"email": "u2@mavs.uta.edu"}).status_code, 404)

    def test_blank_email_rejected(self):
        self.assertEqual(self.invite("   ").status_code, 400)

    def test_unknown_email_rejected(self):
        r = self.invite("nobody@mavs.uta.edu")
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"No StudyMore student", r.data)

    def test_unverified_account_rejected(self):
        self.assertEqual(self.invite("u5@mavs.uta.edu").status_code, 400)

    def test_cannot_invite_yourself(self):
        r = self.invite("u1@mavs.uta.edu")
        self.assertEqual(r.status_code, 400)

    def test_cannot_invite_existing_member(self):
        r = self.invite("u4@mavs.uta.edu")
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"already a member", r.data)

    def test_cannot_invite_twice_while_pending(self):
        self.invite("u2@mavs.uta.edu")
        r = self.invite("u2@mavs.uta.edu")
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"already has a pending", r.data)
        self.assertEqual(len(self.rows("SELECT * FROM group_invitations")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM notifications")), 1)

    def test_database_blocks_duplicate_pending_even_if_the_check_is_skipped(self):
        self.invite("u2@mavs.uta.edu")
        with self.assertRaises(real_sqlite3.IntegrityError):
            self.exec("INSERT INTO group_invitations (group_id, inviter_id, invitee_id, status, created_at) "
                      "VALUES (1, 1, 2, 'pending', '2026-01-01 00:00:00')")

    def test_can_reinvite_after_decline(self):
        self.invite("u2@mavs.uta.edu")
        self.login(2)
        self.client.post(f"/invitations/{self.invite_id()}/decline")
        self.assertEqual(self.invite("u2@mavs.uta.edu").status_code, 302)
        self.assertEqual(len(self.rows("SELECT * FROM group_invitations")), 2)

    def test_full_group_rejects_invites(self):
        self.exec("INSERT INTO group_memberships (user_id, group_id) VALUES (3, 1)")
        r = self.invite("u2@mavs.uta.edu")
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"full", r.data)

    def test_completed_and_cancelled_groups_reject_invites(self):
        for status in ("Completed", "Cancelled"):
            self.exec("UPDATE study_groups SET status = ? WHERE id = 1", (status,))
            self.assertEqual(self.invite("u2@mavs.uta.edu").status_code, 400, status)
        self.assertEqual(self.rows("SELECT * FROM group_invitations"), [])

    def test_past_meeting_rejects_invites(self):
        d, t = when(-1)
        self.exec("UPDATE study_groups SET meeting_date = ?, meeting_time = ? WHERE id = 1", (d, t))
        self.assertEqual(self.invite("u2@mavs.uta.edu").status_code, 400)


class TestAnswering(InviteBase):
    def setUp(self):
        super().setUp()
        self.invite("u2@mavs.uta.edu")
        self.iid = self.invite_id()

    def test_accept_adds_member_and_notifies_inviter(self):
        self.login(2, "Student Two")
        r = self.client.post(f"/invitations/{self.iid}/accept")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/group/1", r.headers["Location"])
        self.assertIn(2, self.members())
        self.assertEqual(self.rows("SELECT status FROM group_invitations")[0][0], "accepted")
        kinds = [(r[0], r[1]) for r in self.rows("SELECT user_id, kind FROM notifications")]
        self.assertIn((1, "invitation_accepted"), kinds)

    def test_decline_does_not_add_member_and_notifies_inviter(self):
        self.login(2, "Student Two")
        self.client.post(f"/invitations/{self.iid}/decline")
        self.assertNotIn(2, self.members())
        self.assertEqual(self.rows("SELECT status FROM group_invitations")[0][0], "declined")
        kinds = [(r[0], r[1]) for r in self.rows("SELECT user_id, kind FROM notifications")]
        self.assertIn((1, "invitation_declined"), kinds)

    def test_someone_elses_invitation_looks_like_it_does_not_exist(self):
        self.login(3)
        self.assertEqual(self.client.post(f"/invitations/{self.iid}/accept").status_code, 404)
        self.assertEqual(self.client.post(f"/invitations/{self.iid}/decline").status_code, 404)
        self.assertNotIn(3, self.members())

    def test_unknown_invitation(self):
        self.login(2)
        self.assertEqual(self.client.post("/invitations/999/accept").status_code, 404)

    def test_requires_login(self):
        self.client.get("/logout")
        self.assertEqual(self.client.post(f"/invitations/{self.iid}/accept").status_code, 302)
        self.assertNotIn(2, self.members())

    def test_cannot_accept_twice(self):
        self.login(2)
        self.client.post(f"/invitations/{self.iid}/accept")
        r = self.client.post(f"/invitations/{self.iid}/accept")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(self.rows("SELECT * FROM group_memberships WHERE user_id = 2")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM notifications WHERE kind = 'invitation_accepted'")), 1)

    def test_cannot_accept_after_declining(self):
        self.login(2)
        self.client.post(f"/invitations/{self.iid}/decline")
        self.client.post(f"/invitations/{self.iid}/accept")
        self.assertNotIn(2, self.members())

    def test_accept_blocked_when_group_filled_up_and_invite_stays_pending(self):
        self.exec("INSERT INTO group_memberships (user_id, group_id) VALUES (3, 1)")
        self.login(2)
        self.client.post(f"/invitations/{self.iid}/accept")
        self.assertNotIn(2, self.members())
        self.assertEqual(self.rows("SELECT status FROM group_invitations")[0][0], "pending")
        page = self.client.get("/invitations")
        self.assertIn(b"Group One", page.data)

    def test_accept_works_after_a_spot_opens(self):
        self.exec("INSERT INTO group_memberships (user_id, group_id) VALUES (3, 1)")
        self.login(2)
        self.client.post(f"/invitations/{self.iid}/accept")
        self.exec("DELETE FROM group_memberships WHERE user_id = 3")
        self.client.post(f"/invitations/{self.iid}/accept")
        self.assertIn(2, self.members())

    def test_cannot_exceed_max_members_with_two_invitations_for_last_seat(self):
        self.invite("u3@mavs.uta.edu")
        iid3 = self.invite_id()
        self.login(2); self.client.post(f"/invitations/{self.iid}/accept")
        self.login(3); self.client.post(f"/invitations/{iid3}/accept")
        self.assertEqual(len(self.members()), 3)
        self.assertNotIn(3, self.members())

    def test_cancelled_group_makes_invitation_expired(self):
        self.exec("UPDATE study_groups SET status = 'Cancelled' WHERE id = 1")
        self.login(2)
        self.client.post(f"/invitations/{self.iid}/accept")
        self.assertNotIn(2, self.members())
        self.assertIn(b"Expired", self.client.get("/invitations").data)

    def test_old_invitation_expires(self):
        old = (datetime.now() - timedelta(days=invitations.INVITE_LIFETIME_DAYS + 1)).strftime(invitations.TIME_FORMAT)
        self.exec("UPDATE group_invitations SET created_at = ?", (old,))
        self.login(2)
        self.client.post(f"/invitations/{self.iid}/accept")
        self.assertNotIn(2, self.members())

    def test_expired_invitation_allows_a_fresh_one(self):
        old = (datetime.now() - timedelta(days=30)).strftime(invitations.TIME_FORMAT)
        self.exec("UPDATE group_invitations SET created_at = ?", (old,))
        self.assertEqual(self.invite("u2@mavs.uta.edu").status_code, 302)
        states = [r[0] for r in self.rows("SELECT status FROM group_invitations ORDER BY id")]
        self.assertEqual(states, ["expired", "pending"])

    def test_invitee_already_joined_by_other_route_gets_marked_accepted_without_duplicate_row(self):
        self.exec("INSERT INTO group_memberships (user_id, group_id) VALUES (2, 1)")
        self.login(2)
        self.client.post(f"/invitations/{self.iid}/accept")
        self.assertEqual(len(self.rows("SELECT * FROM group_memberships WHERE user_id = 2")), 1)
        self.assertEqual(self.rows("SELECT status FROM group_invitations")[0][0], "accepted")


class TestCancel(InviteBase):
    def setUp(self):
        super().setUp()
        self.invite("u2@mavs.uta.edu")
        self.iid = self.invite_id()

    def test_creator_cancels_pending(self):
        self.login(1)
        r = self.client.post(f"/invitations/{self.iid}/cancel")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.rows("SELECT status FROM group_invitations")[0][0], "cancelled")

    def test_cancelled_invite_cannot_be_accepted(self):
        self.login(1); self.client.post(f"/invitations/{self.iid}/cancel")
        self.login(2); self.client.post(f"/invitations/{self.iid}/accept")
        self.assertNotIn(2, self.members())

    def test_non_creator_cannot_cancel(self):
        for uid in (2, 4):
            self.login(uid)
            self.assertEqual(self.client.post(f"/invitations/{self.iid}/cancel").status_code, 403)
        self.assertEqual(self.rows("SELECT status FROM group_invitations")[0][0], "pending")

    def test_cannot_cancel_an_answered_invitation(self):
        self.login(2); self.client.post(f"/invitations/{self.iid}/accept")
        self.login(1)
        self.assertEqual(self.client.post(f"/invitations/{self.iid}/cancel").status_code, 400)
        self.assertEqual(self.rows("SELECT status FROM group_invitations")[0][0], "accepted")

    def test_unknown_invitation(self):
        self.login(1)
        self.assertEqual(self.client.post("/invitations/999/cancel").status_code, 404)


class TestPages(InviteBase):
    def test_inbox_empty_state(self):
        self.login(2)
        r = self.client.get("/invitations")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"No invitations right now", r.data)

    def test_inbox_lists_pending_with_buttons(self):
        self.invite("u2@mavs.uta.edu")
        self.login(2)
        body = self.client.get("/invitations").data
        self.assertIn(b"Group One", body)
        self.assertIn(b"Accept", body)
        self.assertIn(b"Decline", body)

    def test_inbox_does_not_show_other_students_invitations(self):
        self.invite("u2@mavs.uta.edu")
        self.login(3)
        self.assertNotIn(b"Group One", self.client.get("/invitations").data)

    def test_manage_page_shows_members_pending_and_history(self):
        self.invite("u2@mavs.uta.edu")
        self.invite("u3@mavs.uta.edu")
        self.login(3); self.client.post(f"/invitations/{self.invite_id()}/decline")
        self.login(1)
        body = self.client.get("/group/1/manage").data
        self.assertIn(b"Member Four", body)
        self.assertIn(b"Student Two", body)
        self.assertIn(b"Declined", body)
        self.assertIn(b"Cancel", body)

    def test_manage_page_never_shows_emails_of_members(self):
        self.login(1)
        self.assertNotIn(b"u4@mavs.uta.edu", self.client.get("/group/1/manage").data)

    def test_manage_page_for_full_group_explains_why_no_invites(self):
        self.exec("INSERT INTO group_memberships (user_id, group_id) VALUES (3, 1)")
        self.login(1)
        self.assertIn(b"can't take new invitations", self.client.get("/group/1/manage").data)

    def test_group_page_links_creator_to_manage_page_only(self):
        self.login(1)
        self.assertIn(b"/group/1/manage", self.client.get("/group/1").data)
        self.login(4)
        self.assertNotIn(b"/group/1/manage", self.client.get("/group/1").data)

    def test_menu_badge_and_dashboard_banner_show_pending_count(self):
        self.invite("u2@mavs.uta.edu")
        self.login(2)
        self.assertIn(b"nav-badge", self.client.get("/my-groups").data)
        self.assertIn(b"1 pending group invitation.", self.client.get("/dashboard").data)
        self.login(3)
        self.assertNotIn(b"nav-badge", self.client.get("/my-groups").data)

    def test_badge_ignores_expired_invitations(self):
        self.invite("u2@mavs.uta.edu")
        old = (datetime.now() - timedelta(days=30)).strftime(invitations.TIME_FORMAT)
        self.exec("UPDATE group_invitations SET created_at = ?", (old,))
        self.login(2)
        self.assertNotIn(b"nav-badge", self.client.get("/my-groups").data)

    def test_notifications_are_not_duplicated_for_same_item(self):
        with app_module.app.app_context():
            conn = REAL_CONNECT(self.db_path)
            conn.row_factory = real_sqlite3.Row
            self.assertTrue(invitations.create_notification(conn, 2, "k", "m", related_id=7))
            self.assertFalse(invitations.create_notification(conn, 2, "k", "m", related_id=7))
            self.assertTrue(invitations.create_notification(conn, 3, "k", "m", related_id=7))
            conn.close()


if __name__ == "__main__":
    unittest.main()
