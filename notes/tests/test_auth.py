from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from .factories import client_for, make_user


class RegistrationTests(APITestCase):
    def test_register_returns_a_usable_token(self):
        response = self.client.post(
            reverse("register"),
            {"username": "ada", "password": "correct-horse-battery"},
        )
        self.assertEqual(response.status_code, 201)
        token = response.data["token"]

        authed = APIClient()
        authed.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        me = authed.get(reverse("me"))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["username"], "ada")

    def test_weak_password_is_rejected(self):
        response = self.client.post(
            reverse("register"), {"username": "ada", "password": "123"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)

    def test_duplicate_username_is_rejected(self):
        make_user("ada")
        response = self.client.post(
            reverse("register"),
            {"username": "ada", "password": "correct-horse-battery"},
        )
        self.assertEqual(response.status_code, 400)


class AuthenticationRequiredTests(APITestCase):
    def test_anonymous_cannot_list_notes(self):
        response = self.client.get(reverse("note-list"))
        self.assertEqual(response.status_code, 401)

    def test_token_grants_access(self):
        user = make_user("ada")
        response = client_for(user).get(reverse("note-list"))
        self.assertEqual(response.status_code, 200)
