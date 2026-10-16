"""A browser asks for text/html. Make sure that is not a 500.

The JSON tests all pass with no template engine configured, so this gap
is invisible until someone opens an endpoint in a browser.
"""

from django.urls import reverse
from rest_framework.test import APITestCase

from .factories import client_for, make_user


class BrowsableApiTests(APITestCase):
    def setUp(self):
        self.client = client_for(make_user("ada"))

    def test_list_renders_html_for_a_browser(self):
        response = self.client.get(reverse("note-list"), headers={"accept": "text/html"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])

    def test_list_still_returns_json_by_default(self):
        response = self.client.get(reverse("note-list"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response["Content-Type"])
