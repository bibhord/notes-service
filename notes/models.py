from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.text import slugify


class VersionConflict(Exception):
    """Raised when an update targets a version that is no longer current."""


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

    def manageable_by(self, user):
        """Notes this user may delete or restore.

        Wider than authorship: a team admin can remove a member's note from
        the team's space, so they must be able to put it back. Narrower than
        visible_to(), which includes notes you can merely read.
        """
        if not user.is_authenticated:
            return self.none()
        admin_team_ids = Membership.objects.filter(
            user=user, role=Membership.Role.ADMIN
        ).values("team_id")
        return self.filter(
            Q(author=user)
            | Q(visibility=Note.Visibility.TEAM, team_id__in=admin_team_ids)
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
        # PROTECT, not CASCADE. Cascading would let one team admin
        # hard-delete every note in the team, including other people's,
        # with no undo -- which contradicts the rest of this service, where
        # nothing is destroyed by a single action. The database refuses,
        # and the API turns that into a 409.
        on_delete=models.PROTECT,
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

    # Bumped on every write. Clients send the version they last read and
    # the update only lands if it still matches -- see apply_update().
    version = models.PositiveIntegerField(default=1)

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

    @transaction.atomic
    def apply_update(self, *, editor, expected_version=None, **fields):
        """Write changes, refusing to clobber a concurrent edit.

        The guard is a single conditional UPDATE -- the version is matched
        and incremented in the same statement -- so there is no window
        between checking and writing for another writer to slip into.

        `expected_version=None` skips the check and takes the last-writer-
        wins behaviour. The view decides when that is acceptable.
        """
        rows = Note.objects.filter(pk=self.pk)
        if expected_version is not None:
            rows = rows.filter(version=expected_version)

        changed = rows.update(
            version=F("version") + 1,
            updated_at=timezone.now(),
            **fields,
        )
        if changed == 0:
            raise VersionConflict(
                f"This note has changed since version {expected_version}."
            )

        self.refresh_from_db()
        self.record_revision(editor)
        return self

    def record_revision(self, editor) -> "NoteRevision":
        """Snapshot the current state. Called after every successful write."""
        return NoteRevision.objects.create(
            note=self,
            edited_by=editor,
            title=self.title,
            body=self.body,
            version=self.version,
        )


class NoteRevision(models.Model):
    """One row per save, so a lost edit is recoverable and "who changed
    this" is answerable.

    Full snapshots rather than diffs: note bodies are small, and storing
    the whole text means reading any revision is one row with no replay.
    """

    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="revisions")
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="note_revisions",
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["note", "version"], name="unique_revision_per_version"
            )
        ]
        indexes = [models.Index(fields=["note", "-version"])]

    def __str__(self) -> str:
        return f"{self.note_id} v{self.version}"
