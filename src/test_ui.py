import sqlite3

import student_profile
from test_invitations import InviteBase


class UiTests(InviteBase):
    def avail(self, uid, day, start, end):
        self.exec(
            "INSERT INTO user_availability (user_id, day_of_week, start_time, end_time) VALUES (?,?,?,?)",
            (uid, day, start, end),
        )

    def test_overlap_requires_login(self):
        self.assertEqual(self.client.get("/api/availability/overlap").status_code, 401)

    def test_overlap_counts_group_mates_only(self):
        self.avail(4, "Monday", "09:00", "10:00")   # shares group 1 with user 1
        self.avail(3, "Monday", "09:00", "10:00")   # not in any shared group
        self.avail(1, "Tuesday", "10:00", "11:00")  # the viewer's own slot is not counted
        self.login(1)
        data = self.client.get("/api/availability/overlap").get_json()
        self.assertEqual(data["counts"], {"Monday|09:00|10:00": 1})

    def test_overlap_returns_counts_not_names(self):
        self.avail(4, "Friday", "13:00", "14:00")
        self.login(1)
        body = self.client.get("/api/availability/overlap").get_data(as_text=True)
        self.assertNotIn("Member Four", body)

    def test_campus_page_needs_login(self):
        self.assertEqual(self.client.get("/campus").status_code, 302)

    def test_campus_page_lists_buildings(self):
        self.login(1)
        html = self.client.get("/campus").get_data(as_text=True)
        self.assertIn("Central Library", html)
        self.assertIn("Off campus", html)

    def test_pages_carry_ui_layer_and_user(self):
        self.login(1)
        html = self.client.get("/availability").get_data(as_text=True)
        self.assertIn("ui.js", html)
        self.assertIn("ui.css", html)
        self.assertIn("window.SM", html)
        self.assertIn("Creator", html)

    def test_avatar_color_reaches_nav(self):
        conn = sqlite3.connect(self.db_path)
        student_profile.ensure_table(conn)
        conn.execute("INSERT INTO user_profiles (user_id, avatar_color) VALUES (1, 'teal')")
        conn.commit()
        conn.close()
        self.login(1)
        html = self.client.get("/availability").get_data(as_text=True)
        self.assertIn("#0f8d74", html)

    def test_dashboard_greets_by_display_name(self):
        self.login(1, "Creator")
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn('data-greet', html)

    def test_logged_out_pages_have_no_user(self):
        html = self.client.get("/login").get_data(as_text=True)
        self.assertIn('"user": null', html.replace('"user":null', '"user": null'))
