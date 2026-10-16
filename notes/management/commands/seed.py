"""Create a small, realistic dataset so a reviewer can poke at the API
without writing six POSTs first.

Idempotent: running it twice leaves the same data.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token

from notes.models import Membership, Note, Team

User = get_user_model()

PASSWORD = "correct-horse-battery"


class Command(BaseCommand):
    help = "Create demo users, a team and a few notes."

    def handle(self, *args, **options):
        ada, _ = User.objects.get_or_create(username="ada")
        grace, _ = User.objects.get_or_create(username="grace")
        mallory, _ = User.objects.get_or_create(username="mallory")
        for user in (ada, grace, mallory):
            user.set_password(PASSWORD)
            user.save(update_fields=["password"])

        team, _ = Team.objects.get_or_create(name="Platform", defaults={"slug": "platform"})
        Membership.objects.get_or_create(
            team=team, user=ada, defaults={"role": Membership.Role.ADMIN}
        )
        Membership.objects.get_or_create(team=team, user=grace)

        Note.objects.get_or_create(
            author=ada,
            title="Deploy runbook",
            defaults={
                "body": "1. tag the release 2. run migrations 3. watch error rates",
                "team": team,
                "visibility": Note.Visibility.TEAM,
            },
        )
        Note.objects.get_or_create(
            author=grace,
            title="Standup notes",
            defaults={
                "body": "Blocked on the search index.",
                "team": team,
                "visibility": Note.Visibility.TEAM,
            },
        )
        Note.objects.get_or_create(
            author=ada,
            title="Groceries",
            defaults={"body": "milk, bread, coffee"},
        )
        Note.objects.get_or_create(
            author=mallory,
            title="Not yours",
            defaults={"body": "This one belongs to someone outside the team."},
        )

        self.stdout.write(self.style.SUCCESS("Seeded. Tokens:"))
        for user in (ada, grace, mallory):
            token, _ = Token.objects.get_or_create(user=user)
            self.stdout.write(f"  {user.username:10} {token.key}")
        self.stdout.write(f"\nAll passwords: {PASSWORD}")
