import unittest

from werkzeug.security import generate_password_hash

from test_private_groups import PrivateGroupBase


class AccountRouteTests(PrivateGroupBase):
    def setUp(self):
        super().setUp()
        conn = self.db()
        conn.execute(
            "UPDATE users SET password = ? WHERE id IN (1, 2, 3, 4)",
            (generate_password_hash("secret123"),),
        )
        conn.commit()
        conn.close()

    def user_exists(self, uid):
        conn = self.db()
        n = conn.execute("SELECT COUNT(*) FROM users WHERE id = ?", (uid,)).fetchone()[0]
        conn.close()
        return n == 1

    def test_requires_login(self):
        self.assertEqual(self.client.get("/account/delete").status_code, 302)
        resp = self.client.post("/account/delete", data={"password": "secret123", "confirm": "DELETE"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(self.user_exists(1))

    def test_page_loads_when_logged_in(self):
        self.login(2)
        resp = self.client.get("/account/delete")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Delete your account", resp.data)

    def test_wrong_password_keeps_account(self):
        self.login(2)
        resp = self.client.post("/account/delete", data={"password": "nope", "confirm": "DELETE"})
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(self.user_exists(2))

    def test_missing_confirmation_keeps_account(self):
        self.login(2)
        resp = self.client.post("/account/delete", data={"password": "secret123", "confirm": "delete me"})
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(self.user_exists(2))

    def test_correct_password_deletes_and_logs_out(self):
        self.login(4)
        resp = self.client.post("/account/delete", data={"password": "secret123", "confirm": "DELETE"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"has been deleted", resp.data)
        self.assertFalse(self.user_exists(4))
        with self.client.session_transaction() as s:
            self.assertNotIn("user_id", s)
        conn = self.db()
        n = conn.execute("SELECT COUNT(*) FROM group_memberships WHERE user_id = 4").fetchone()[0]
        conn.close()
        self.assertEqual(n, 0)

    def test_deleting_creator_passes_group_on_and_keeps_others(self):
        self.login(1)
        self.client.post("/account/delete", data={"password": "secret123", "confirm": "DELETE"})
        self.assertFalse(self.user_exists(1))
        for uid in (2, 3, 4):
            self.assertTrue(self.user_exists(uid))
        conn = self.db()
        owner = conn.execute("SELECT creator_user_id FROM study_groups WHERE id = 1").fetchone()[0]
        conn.close()
        self.assertEqual(owner, 4)


if __name__ == "__main__":
    unittest.main()