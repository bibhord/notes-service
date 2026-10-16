from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify


class Team(models.Model):
    """A small group of people who share notes with each other."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="Membership",
        related_name="teams",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug(self.name)
        super().save(*args, **kwargs)

    @staticmethod
    def _unique_slug(name: str) -> str:
        base = slugify(name)[:120] or "team"
        candidate = base
        suffix = 2
        while Team.objects.filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate


class Membership(models.Model):
    """Join table between users and teams, carrying the user's role.

    Role is stored here rather than on the user because the same person is
    routinely an admin of one team and an ordinary member of another.
    """

    class Role(models.TextChoices):
        MEMBER = "member", "Member"
        ADMIN = "admin", "Admin"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "user"], name="unique_membership_per_team"
            )
        ]
        ordering = ["team__name", "user__username"]

    def __str__(self) -> str:
        return f"{self.user} in {self.team} ({self.role})"

    @property
    def is_admin(self) -> bool:
        return self.role == self.Role.ADMIN


class NoteQuerySet(models.QuerySet):
    def alive(self):
        """Exclude soft-deleted notes."""
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)

    def visible_to(self, user):
        """Restrict to notes this user is allowed to read.

        This is the single place that decides what a user can see. Views
        start from this queryset rather than from Note.objects.all(), so a
        forgotten permission check on a new endpoint returns nothing rather
        than leaking another team's notes.
        """
        if not user.is_authenticated:
            return self.none()
        team_ids = Membership.objects.filter(user=user).values("team_id")
        return self.filter(
            Q(author=user) | Q(visibility=Note.Visibility.TEAM, team_id__in=team_ids)
        )

    def search(self, term: str):
        """Case-insensitive substring match over title and body.

        LIKE scans are fine at this size and work identically on SQLite and
        Postgres. See the README for when this needs to become a real index.
        """
        term = (term or "").strip()
        if not term:
            return self
        return self.filter(Q(title__icontains=term) | Q(body__icontains=term))


class Note(models.Model):
    """A note, owned by one person, optionally shared with one team."""

    class Visibility(models.TextChoices):
        PRIVATE = "private", "Private"
        TEAM = "team", "Team"

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notes",
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="notes",
        null=True,
        blank=True,
    )
    visibility = models.CharField(
        max_length=10,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = NoteQuerySet.as_manager()

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(fields=["author", "-updated_at"]),
            models.Index(fields=["team", "-updated_at"]),
        ]
        constraints = [
            # A team note must name a team; a private note must not.
            # Enforced in the database so a future code path cannot create
            # a note that is shared with nobody but marked as shared.
            models.CheckConstraint(
                condition=(
                    Q(visibility="team", team__isnull=False)
                    | Q(visibility="private", team__isnull=True)
                ),
                name="team_visibility_requires_team",
            )
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self):
        if self.visibility == self.Visibility.TEAM and self.team is None:
            raise ValidationError({"team": "A team note must specify a team."})
        if self.visibility == self.Visibility.PRIVATE and self.team is not None:
            raise ValidationError({"team": "A private note cannot belong to a team."})

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        """Mark as deleted without removing the row.

        Notes are the product. Losing one to a mis-click is worse than
        carrying dead rows, so DELETE sets a timestamp and /restore/ clears it.
        """
        if self.deleted_at is None:
            self.deleted_at = timezone.now()
            self.save(update_fields=["deleted_at"])

    def restore(self) -> None:
        if self.deleted_at is not None:
            self.deleted_at = None
            self.save(update_fields=["deleted_at"])
