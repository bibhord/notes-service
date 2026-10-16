"""Small helpers so tests read as scenarios rather than as setup."""

from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from notes.models import Membership, Note, Team

User = get_user_model()


def make_user(username: str, password: str = "correct-horse-battery") -> User:
    return User.objects.create_user(username=username, password=password)


def client_for(user) -> APIClient:
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


def make_team(name: str, admin, members=()) -> Team:
    team = Team.objects.create(name=name)
    Membership.objects.create(team=team, user=admin, role=Membership.Role.ADMIN)
    for member in members:
        Membership.objects.create(team=team, user=member)
    return team


def make_note(author, title="Note", body="", team=None) -> Note:
    visibility = Note.Visibility.TEAM if team else Note.Visibility.PRIVATE
    return Note.objects.create(
        author=author, title=title, body=body, team=team, visibility=visibility
    )
