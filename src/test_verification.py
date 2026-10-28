import os
import sqlite3 as real_sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import app as app_module
import verification

REAL_CONNECT = real_sqlite3.connect


class VerificationBase(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.patcher = patch.object(
            app_module.sqlite3, "connect", lambda *a, **k: REAL_CONNECT(self.db_path)
        )
        self.patcher.start()
        app_module.init_db()
        self.sent = []
        self.mail = patch.object(
            verification, "send_code_email",
            lambda email, code: self.sent.append((email, code)) or True,
        )
        self.mail.start()
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.mail.stop()
        self.patcher.stop()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def register(self, email="new@mavs.uta.edu", password="pw12345"):
        return self.client.post("/register", data={
            "name": "New Student", "email": email,
            "password": password, "confirm_password": password})

    def query(self, sql, args=()):
        conn = REAL_CONNECT(self.db_path)
        try:
            return conn.execute(sql, args).fetchone()
        finally:
            conn.close()

    def logged_in(self):
        with self.client.session_transaction() as s:
            return s.get("user_id")


class TestRegisterFlow(VerificationBase):
    def test_non_uta_emails_rejected(self):
        for email in ("a@gmail.com", "a@uta.edu", "a@mavs.uta.edu.evil.com",
                      "a@b@mavs.uta.edu", "@mavs.uta.edu"):
            self.assertEqual(self.register(email).status_code, 400, email)
        self.assertEqual(self.sent, [])

    def test_register_sends_code_and_does_not_log_in(self):
        r = self.register()
        self.assertEqual(r.status_code, 302)
        self.assertIn("/verify", r.headers["Location"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "new@mavs.uta.edu")
        self.assertEqual(len(self.sent[0][1]), 6)
        self.assertIsNone(self.logged_in())
        self.assertEqual(self.query("SELECT email_verified FROM users")[0], 0)

    def test_code_is_stored_hashed(self):
        self.register()
        stored = self.query("SELECT code_hash FROM email_verifications")[0]
        self.assertNotIn(self.sent[0][1], stored)

    def test_correct_code_verifies_and_logs_in(self):
        self.register()
        r = self.client.post("/verify", data={"code": self.sent[0][1]})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dashboard", r.headers["Location"])
        self.assertIsNotNone(self.logged_in())
        self.assertEqual(self.query("SELECT email_verified FROM users")[0], 1)
        self.assertIsNone(self.query("SELECT * FROM email_verifications"))

    def test_code_with_spaces_is_accepted(self):
        self.register()
        code = self.sent[0][1]
        r = self.client.post("/verify", data={"code": f"{code[:3]} {code[3:]}"})
        self.assertEqual(r.status_code, 302)

    def test_wrong_code_rejected_and_counted(self):
        self.register()
        r = self.client.post("/verify", data={"code": "000000" if self.sent[0][1] != "000000" else "111111"})
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.logged_in())
        self.assertEqual(self.query("SELECT attempts FROM email_verifications")[0], 1)

    def test_garbage_code_rejected(self):
        self.register()
        for bad in ("", "abc", "12345", "1234567"):
            self.assertEqual(self.client.post("/verify", data={"code": bad}).status_code, 400, bad)

    def test_locked_after_too_many_wrong_tries_even_with_right_code(self):
        self.register()
        wrong = "000000" if self.sent[0][1] != "000000" else "111111"
        for _ in range(verification.MAX_ATTEMPTS):
            self.client.post("/verify", data={"code": wrong})
        r = self.client.post("/verify", data={"code": self.sent[0][1]})
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"Too many", r.data)
        self.assertIsNone(self.logged_in())

    def test_expired_code_rejected(self):
        self.register()
        conn = REAL_CONNECT(self.db_path)
        conn.execute("UPDATE email_verifications SET expires_at = ?", (int(time.time()) - 1,))
        conn.commit(); conn.close()
        r = self.client.post("/verify", data={"code": self.sent[0][1]})
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"expired", r.data)

    def test_verify_page_without_pending_signup_goes_to_login(self):
        self.assertIn("/login", self.client.get("/verify").headers["Location"])
        self.assertIn("/login", self.client.post("/verify", data={"code": "123456"}).headers["Location"])

    def test_page_masks_email(self):
        self.register()
        body = self.client.get("/verify").data
        self.assertIn(b"ne***@mavs.uta.edu", body)
        self.assertNotIn(b"new@mavs.uta.edu", body)


class TestResend(VerificationBase):
    def test_resend_blocked_during_cooldown(self):
        self.register()
        r = self.client.post("/verify/resend")
        self.assertEqual(r.status_code, 429)
        self.assertEqual(len(self.sent), 1)

    def test_resend_after_cooldown_issues_new_working_code(self):
        self.register()
        old = self.sent[0][1]
        conn = REAL_CONNECT(self.db_path)
        conn.execute("UPDATE email_verifications SET last_sent_at = ?", (int(time.time()) - 120,))
        conn.commit(); conn.close()
        self.assertEqual(self.client.post("/verify/resend").status_code, 200)
        self.assertEqual(len(self.sent), 2)
        new = self.sent[1][1]
        if new != old:
            self.assertEqual(self.client.post("/verify", data={"code": old}).status_code, 400)
        self.assertEqual(self.client.post("/verify", data={"code": new}).status_code, 302)

    def test_email_failure_is_reported_not_crashed(self):
        self.register()
        conn = REAL_CONNECT(self.db_path)
        conn.execute("UPDATE email_verifications SET last_sent_at = 0")
        conn.commit(); conn.close()
        with patch.object(verification, "send_code_email", lambda e, c: False):
            r = self.client.post("/verify/resend")
        self.assertEqual(r.status_code, 502)

    def test_resend_without_pending_signup_goes_to_login(self):
        self.assertIn("/login", self.client.post("/verify/resend").headers["Location"])


class TestLogin(VerificationBase):
    def test_unverified_user_cannot_log_in_but_is_sent_to_verify(self):
        self.register()
        self.client.get("/logout")
        r = self.client.post("/login", data={"email": "new@mavs.uta.edu", "password": "pw12345"})
        self.assertIn("/verify", r.headers["Location"])
        self.assertIsNone(self.logged_in())
        self.assertEqual(self.client.get("/dashboard").status_code, 302)
        self.assertEqual(len(self.sent), 1)  # cooldown: no second email yet

    def test_wrong_password_for_unverified_user_does_not_leak_or_send(self):
        self.register()
        self.client.get("/logout")
        r = self.client.post("/login", data={"email": "new@mavs.uta.edu", "password": "nope"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.sent), 1)

    def test_verified_user_logs_in_normally(self):
        self.register()
        self.client.post("/verify", data={"code": self.sent[0][1]})
        self.client.get("/logout")
        r = self.client.post("/login", data={"email": "new@mavs.uta.edu", "password": "pw12345"})
        self.assertIn("/dashboard", r.headers["Location"])
        self.assertIsNotNone(self.logged_in())

    def test_accounts_that_existed_before_the_feature_stay_verified(self):
        conn = REAL_CONNECT(self.db_path)
        conn.execute("DROP TABLE email_verifications")
        conn.execute("ALTER TABLE users DROP COLUMN email_verified")
        from werkzeug.security import generate_password_hash
        conn.execute("INSERT INTO users (name, email, password) VALUES ('Old', 'old@mavs.uta.edu', ?)",
                     (generate_password_hash("pw"),))
        conn.commit(); conn.close()
        r = self.client.post("/login", data={"email": "old@mavs.uta.edu", "password": "pw"})
        self.assertIn("/dashboard", r.headers["Location"])
        app_module.init_db()
        self.assertEqual(self.query("SELECT email_verified FROM users WHERE email='old@mavs.uta.edu'")[0], 1)


class TestSending(unittest.TestCase):
    def test_demo_mode_prints_code(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STUDYMORE_SMTP_HOST", None)
            with patch("builtins.print") as p:
                self.assertTrue(verification.send_code_email("a@mavs.uta.edu", "123456"))
        self.assertIn("123456", str(p.call_args))

    def test_smtp_failure_returns_false(self):
        with patch.dict(os.environ, {"STUDYMORE_SMTP_HOST": "127.0.0.1", "STUDYMORE_SMTP_PORT": "1"}):
            with app_module.app.app_context():
                self.assertFalse(verification.send_code_email("a@mavs.uta.edu", "123456"))


if __name__ == "__main__":
    unittest.main()
