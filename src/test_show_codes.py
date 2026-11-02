import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, Response, session

import verification


def make_app():
    app = Flask(__name__)
    app.secret_key = "test"
    return app


class ShowCodesTests(unittest.TestCase):
    def test_off_by_default_nothing_stored(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STUDYMORE_SMTP_HOST", None)
            os.environ.pop("STUDYMORE_SHOW_CODES", None)
            with make_app().test_request_context("/"):
                self.assertTrue(verification.send_code_email("a@mavs.uta.edu", "123456"))
                self.assertNotIn("demo_code", session)

    def test_switch_on_stores_code(self):
        with patch.dict(os.environ, {"STUDYMORE_SHOW_CODES": "1"}):
            os.environ.pop("STUDYMORE_SMTP_HOST", None)
            with make_app().test_request_context("/"):
                verification.send_code_email("a@mavs.uta.edu", "123456")
                self.assertEqual(session["demo_code"]["code"], "123456")

    def test_banner_appears_on_verify_pages_only(self):
        page = Response("<html><body><p>hi</p></body></html>", mimetype="text/html")
        with make_app().test_request_context("/"):
            verification.remember_demo_code("654321", "verification")
            with patch.object(verification, "request", SimpleNamespace(endpoint="verification.verify")):
                out = verification.show_demo_code_banner(page)
                self.assertIn("654321", out.get_data(as_text=True))
            page2 = Response("<html><body><p>hi</p></body></html>", mimetype="text/html")
            with patch.object(verification, "request", SimpleNamespace(endpoint="dashboard.dashboard")):
                out2 = verification.show_demo_code_banner(page2)
                self.assertNotIn("654321", out2.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
