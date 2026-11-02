"""
Tests for the Notification Center (notifications.py).

Users in every test:
  1 Creator       creates Group One and is a member
  2 Student Two   a student who gets invited
  3 Student Three another student (used to check privacy)
  4 Member Four   already a member of Group One

Run from the src folder:  python -m unittest test_notifications
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

import app as app_module
import notifications
from invitations import TIME_FORMAT, create_notification


def stamp(**ago):
    """A created_at value some time in the past, e.g. stamp(hours=3)."""
    return (datetime.now() - timedelta(**ago)).strftime(TIME_FORMAT)


class NotificationBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "test.db")
        self.old_database = app_module.app.config["DATABASE"]
        app_module.app.config["DATABASE"] = self.path
        app_module.init_db()

        conn = self.connect()
        for uid, name in ((1, "Creator"), (2, "Student Two"), (3, "Student Three"), (4, "Member Four")):
            conn.execute(
                "INSERT INTO users (id, name, email, password) VALUES (?, ?, ?, 'x')",
                (uid, name, f"u{uid}@mavs.uta.edu"),
            )
        meeting = datetime.now() + timedelta(days=7)
        conn.execute(
            """INSERT INTO study_groups
               (id, creator_user_id, group_name, course_code, section, description,
                meeting_type, meeting_date, meeting_time, location, meeting_link,
                max_members, status)
               VALUES (1, 1, 'Group One', 'CSE 3311', '001', 'desc', 'In-Person',
                       ?, ?, 'ERB 128', '', 5, 'open')""",
            (meeting.strftime("%Y-%m-%d"), meeting.strftime("%H:%M")),
        )
        conn.executemany(
            "INSERT INTO group_memberships (user_id, group_id) VALUES (?, 1)", [(1,), (4,)]
        )
        conn.commit()
        conn.close()
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.app.config["DATABASE"] = self.old_database
        self.temp.cleanup()

    # ---- helpers -------------------------------------------------------

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def run_sql(self, sql, args=()):
        conn = self.connect()
        conn.execute(sql, args)
        conn.commit()
        conn.close()

    def rows(self, sql, args=()):
        conn = self.connect()
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def login(self, uid, name=None):
        with self.client.session_transaction() as s:
            s.clear()
            if uid is not None:
                s["user_id"] = uid
                s["user_name"] = name or f"User {uid}"

    def add_note(self, user_id, kind="general", message="Hello", link_url="/my-groups",
                 group_id=None, related_id=None, created_at=None, is_read=0):
        """Insert a notification directly and return its id."""
        conn = self.connect()
        notifications.ensure_notification_schema(conn)
        cursor = conn.execute(
            """INSERT INTO notifications
               (user_id, kind, message, link_url, group_id, related_id, is_read, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, kind, message, link_url, group_id, related_id, is_read,
             created_at or stamp(seconds=0)),
        )
        conn.commit()
        conn.close()
        return cursor.lastrowid

    def invite_student_two(self):
        """Creator invites Student Two through the real invitation route."""
        self.login(1, "Creator")
        self.client.post("/group/1/invite", data={"email": "u2@mavs.uta.edu"})
        return self.rows("SELECT id FROM group_invitations ORDER BY id DESC")[0][0]

    def note_for(self, user_id, kind):
        return self.rows(
            "SELECT * FROM notifications WHERE user_id = ? AND kind = ?", (user_id, kind)
        )[0]

    def page(self, show=None):
        url = "/notifications" + (f"?show={show}" if show else "")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)


# ------------------------------------------------------------------------
# Rules with no Flask involved
# ------------------------------------------------------------------------

class TestSafeLink(unittest.TestCase):
    def test_links_inside_studymore_are_kept(self):
        for link in ("/invitations", "/group/4/discussions/2", "/group/1/manage"):
            self.assertEqual(notifications.safe_link(link), link)

    def test_outside_or_broken_links_are_refused(self):
        for link in (None, "", "https://evil.example", "//evil.example",
                     "/\\evil.example", "javascript:alert(1)", "group/1"):
            self.assertIsNone(notifications.safe_link(link), link)


class TestTimeAgo(unittest.TestCase):
    NOW = datetime(2026, 11, 1, 18, 0, 0)

    def ago(self, **delta):
        return notifications.time_ago((self.NOW - timedelta(**delta)).strftime(TIME_FORMAT), self.NOW)

    def test_recent_times(self):
        self.assertEqual(self.ago(seconds=20), "Just now")
        self.assertEqual(self.ago(minutes=5), "5 min ago")
        self.assertEqual(self.ago(hours=3), "3 hr ago")

    def test_days(self):
        self.assertEqual(self.ago(days=1), "Yesterday")
        self.assertEqual(self.ago(days=4), "4 days ago")

    def test_older_dates(self):
        self.assertEqual(self.ago(days=20), "Oct 12")
        self.assertEqual(self.ago(days=400), "Sep 27, 2025")

    def test_bad_or_future_values(self):
        self.assertEqual(notifications.time_ago("not a date", self.NOW), "")
        self.assertEqual(notifications.time_ago(None, self.NOW), "")
        self.assertEqual(self.ago(minutes=-5), "Just now")


# ------------------------------------------------------------------------
# The Notification Center page
# ------------------------------------------------------------------------

class TestAccessAndPrivacy(NotificationBase):
    def test_logged_out_students_are_sent_to_login(self):
        note_id = self.add_note(2)
        self.login(None)
        for method, url in (("get", "/notifications"),
                            ("post", f"/notifications/{note_id}/open"),
                            ("post", f"/notifications/{note_id}/read"),
                            ("post", "/notifications/read-all")):
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/login", response.location)
        self.assertEqual(self.rows("SELECT is_read FROM notifications")[0][0], 0)

    def test_students_only_see_their_own_notifications(self):
        self.add_note(2, message="For Student Two only")
        self.login(3)
        self.assertNotIn("For Student Two only", self.page())

    def test_someone_elses_notification_is_a_404_and_is_not_changed(self):
        note_id = self.add_note(2)
        self.login(3)
        for action in ("open", "read", "unread"):
            self.assertEqual(self.client.post(f"/notifications/{note_id}/{action}").status_code, 404)
        self.assertEqual(self.client.post("/notifications/999/open").status_code, 404)
        self.assertEqual(self.rows("SELECT is_read FROM notifications")[0][0], 0)

    def test_mark_all_read_only_touches_my_notifications(self):
        self.add_note(2)
        self.add_note(3)
        self.login(2)
        self.client.post("/notifications/read-all")
        read = {r["user_id"]: r["is_read"] for r in self.rows("SELECT user_id, is_read FROM notifications")}
        self.assertEqual(read, {2: 1, 3: 0})


class TestListAndEmptyStates(NotificationBase):
    def test_empty_state_for_a_new_student(self):
        self.login(3)
        html = self.page()
        self.assertIn("No notifications yet", html)
        self.assertNotIn("Mark all as read", html)

    def test_newest_first_with_unread_highlighted(self):
        old_id = self.add_note(2, message="Older one", created_at=stamp(days=2), is_read=1)
        new_id = self.add_note(2, message="Newer one", created_at=stamp(minutes=5))
        self.login(2)
        html = self.page()
        self.assertLess(html.index("Newer one"), html.index("Older one"))
        self.assertIn(f'class="notif-card is-unread" id="notification-{new_id}"', html)
        self.assertIn(f'class="notif-card" id="notification-{old_id}"', html)
        self.assertIn("5 min ago", html)

    def test_unread_filter_and_all_caught_up_state(self):
        self.add_note(2, message="Still unread")
        self.add_note(2, message="Already read", is_read=1)
        self.login(2)
        html = self.page("unread")
        self.assertIn("Still unread", html)
        self.assertNotIn("Already read", html)
        self.client.post("/notifications/read-all")
        html = self.page("unread")
        self.assertIn("all caught up", html)
        self.assertIn("See all notifications", html)

    def test_unknown_filter_falls_back_to_all(self):
        self.add_note(2, message="Shown anyway", is_read=1)
        self.login(2)
        self.assertIn("Shown anyway", self.page("bogus"))

    def test_unknown_kinds_still_display(self):
        self.add_note(2, kind="session_reminder", message="Study session soon")
        self.login(2)
        html = self.page()
        self.assertIn("Study session soon", html)
        self.assertIn("notif-update", html)

    def test_long_lists_show_the_newest_fifty(self):
        for i in range(notifications.PAGE_LIMIT + 5):
            self.add_note(2, message=f"Note {i:02d}", created_at=stamp(minutes=100 - i), is_read=1)
        self.login(2)
        html = self.page()
        self.assertEqual(html.count('class="notif-card'), notifications.PAGE_LIMIT)
        self.assertIn("Note 54", html)
        self.assertNotIn("Note 00", html)
        self.assertIn("Showing your 50 newest notifications", html)

    def test_menu_badge_shows_the_unread_count(self):
        self.add_note(2)
        self.add_note(2)
        self.add_note(2, is_read=1)
        self.login(2)
        html = self.client.get("/my-groups").get_data(as_text=True)
        self.assertIn('Notifications<span class="nav-badge">2</span>', html)
        self.client.post("/notifications/read-all")
        html = self.client.get("/my-groups").get_data(as_text=True)
        self.assertIn("Notifications</a>", html)


class TestReadAndUnread(NotificationBase):
    def test_mark_read_then_unread_keeps_the_filter(self):
        note_id = self.add_note(2)
        self.login(2)
        response = self.client.post(f"/notifications/{note_id}/read", data={"show": "unread"})
        self.assertIn("show=unread", response.location)
        self.assertEqual(self.rows("SELECT is_read FROM notifications")[0][0], 1)
        self.client.post(f"/notifications/{note_id}/unread")
        self.assertEqual(self.rows("SELECT is_read FROM notifications")[0][0], 0)

    def test_mark_all_read_reports_how_many(self):
        for _ in range(3):
            self.add_note(2)
        self.login(2)
        self.client.post("/notifications/read-all")
        self.assertIn("Marked 3 notifications as read.", self.page())
        self.client.post("/notifications/read-all")
        self.assertIn("all caught up", self.page())


class TestOpeningNotifications(NotificationBase):
    def test_open_marks_read_and_goes_to_the_link(self):
        note_id = self.add_note(2, link_url="/my-groups")
        self.login(2)
        response = self.client.post(f"/notifications/{note_id}/open")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/my-groups"))
        self.assertEqual(self.rows("SELECT is_read FROM notifications")[0][0], 1)

    def test_links_outside_studymore_are_never_followed(self):
        note_id = self.add_note(2, link_url="https://evil.example/phish")
        self.login(2)
        html = self.page()
        self.assertNotIn(f"/notifications/{note_id}/open", html)
        response = self.client.post(f"/notifications/{note_id}/open")
        self.assertNotIn("evil.example", response.location)
        self.assertIn("/notifications", response.location)
        self.assertIn("no page to open", self.page())

    def test_deleted_group_is_explained_instead_of_opened(self):
        note_id = self.add_note(2, group_id=1, link_url="/group/1")
        self.run_sql("DELETE FROM study_groups WHERE id = 1")
        self.login(2)
        self.assertIn("This study group no longer exists.", self.page())
        response = self.client.post(f"/notifications/{note_id}/open")
        self.assertIn("/notifications", response.location)


# ------------------------------------------------------------------------
# Working with Group Invitations and the Discussion Board
# ------------------------------------------------------------------------

class TestInvitationNotifications(NotificationBase):
    def test_invitation_shows_up_for_the_invited_student(self):
        self.invite_student_two()
        self.login(2)
        html = self.page()
        self.assertIn("Creator invited you to join Group One (CSE 3311).", html)
        self.assertIn("notif-invite", html)
        self.assertIn('Notifications<span class="nav-badge">1</span>', html)

    def test_open_goes_to_the_invitations_page(self):
        self.invite_student_two()
        note = self.note_for(2, "group_invitation")
        self.login(2)
        response = self.client.post(f"/notifications/{note['id']}/open")
        self.assertTrue(response.location.endswith("/invitations"))

    def test_accepting_notifies_the_creator_and_settles_the_invitation(self):
        invitation_id = self.invite_student_two()
        self.login(2, "Student Two")
        self.client.post(f"/invitations/{invitation_id}/accept")

        # Student Two answered from the Invitations page, so nothing is left to do.
        html = self.page()
        self.assertIn("You already accepted this invitation.", html)
        self.assertNotIn("is-unread", html)
        self.assertNotIn("Mark as unread", html)
        conn = self.connect()
        self.assertEqual(notifications.unread_count(conn, 2), 0)
        conn.close()

        # The creator hears about it, and Open goes to the Members page.
        self.login(1, "Creator")
        html = self.page()
        self.assertIn("Student Two accepted your invitation to Group One.", html)
        note = self.note_for(1, "invitation_accepted")
        response = self.client.post(f"/notifications/{note['id']}/open")
        self.assertTrue(response.location.endswith("/group/1/manage"))

    def test_declining_notifies_the_creator(self):
        invitation_id = self.invite_student_two()
        self.login(2, "Student Two")
        self.client.post(f"/invitations/{invitation_id}/decline")
        self.login(1, "Creator")
        html = self.page()
        self.assertIn("Student Two declined your invitation to Group One.", html)
        self.assertIn("notif-declined", html)

    def test_cancelled_invitation_is_explained(self):
        invitation_id = self.invite_student_two()
        self.login(1, "Creator")
        self.client.post(f"/invitations/{invitation_id}/cancel")
        self.login(2)
        html = self.page()
        self.assertIn("The group creator cancelled this invitation.", html)
        self.assertNotIn("is-unread", html)

    def test_expired_invitation_is_explained(self):
        invitation_id = self.invite_student_two()
        self.run_sql("UPDATE group_invitations SET created_at = ? WHERE id = ?",
                     (stamp(days=8), invitation_id))
        self.login(2)
        self.assertIn("This invitation has expired.", self.page())
        self.assertNotIn("Notifications<span", self.client.get("/my-groups").get_data(as_text=True))

    def test_repeated_invitation_activity_is_not_duplicated(self):
        invitation_id = self.invite_student_two()
        self.invite_student_two()  # refused: Student Two already has a pending invitation
        conn = self.connect()
        self.assertFalse(create_notification(
            conn, 2, "group_invitation", "again", related_id=invitation_id))
        conn.close()
        self.assertEqual(len(self.rows("SELECT * FROM notifications WHERE user_id = 2")), 1)


class TestDiscussionNotifications(NotificationBase):
    def start_discussion_with_reply(self):
        """Member Four posts, the Creator replies. Returns (post_id, reply notification)."""
        self.login(4)
        self.client.post("/group/1/discussions", data={"content": "Who has the Exam 2 notes?"})
        post_id = self.rows("SELECT id FROM discussion_posts")[0][0]
        self.login(1)
        self.client.post(f"/group/1/discussions/{post_id}/replies", data={"content": "I do!"})
        return post_id, self.note_for(4, "discussion_reply")

    def test_reply_shows_up_and_opens_the_discussion(self):
        post_id, note = self.start_discussion_with_reply()
        self.login(4)
        html = self.page()
        self.assertIn("replied to your discussion in Group One.", html)
        self.assertIn("notif-reply", html)
        response = self.client.post(f"/notifications/{note['id']}/open")
        self.assertTrue(response.location.endswith(f"/group/1/discussions/{post_id}"))

    def test_student_who_left_the_group_is_not_sent_to_an_error_page(self):
        _, note = self.start_discussion_with_reply()
        self.run_sql("DELETE FROM group_memberships WHERE user_id = 4")
        self.login(4)
        html = self.page()
        self.assertIn("You are no longer a member of this group.", html)
        self.assertNotIn(f"/notifications/{note['id']}/open\"", html)
        response = self.client.post(f"/notifications/{note['id']}/open")
        self.assertIn("/notifications", response.location)
        self.assertIn("no longer a member", self.page())

    def test_removed_reply_is_explained(self):
        _, note = self.start_discussion_with_reply()
        self.run_sql("DELETE FROM discussion_replies")
        self.login(4)
        self.assertIn("This reply was removed.", self.page())


if __name__ == "__main__":
    unittest.main()
