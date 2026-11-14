import os

from test_invitations import InviteBase

CSS = os.path.join(os.path.dirname(__file__), "static", "ui.css")


class MobileLayoutTests(InviteBase):
    def test_availability_has_short_and_full_day_names(self):
        self.login(1)
        html = self.client.get("/availability").get_data(as_text=True)
        self.assertIn('class="d-full">Monday', html)
        self.assertIn('class="d-short">Mon', html)

    def test_phone_rules_exist(self):
        css = open(CSS, encoding="utf-8").read()
        self.assertIn("@media (max-width:640px)", css)
        self.assertIn("minmax(0,1fr)", css)      # grid fits the screen
        self.assertIn("padding-bottom:76px", css)  # help button never covers the last lines

    def test_every_page_sets_a_viewport(self):
        self.login(1)
        for url in ("/dashboard", "/group/1/heatmap", "/search", "/availability", "/profile", "/notifications", "/campus"):
            html = self.client.get(url).get_data(as_text=True)
            self.assertIn('name="viewport"', html, url)
