from django.contrib.auth import get_user_model
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from .concurrency import etag_for, expected_version_for
from .filters import NoteFilter
from .models import Membership, Note, NoteRevision, Team, VersionConflict
from .permissions import CanEditNote, IsTeamAdmin
from .serializers import (
    MembershipSerializer,
    NoteRevisionSerializer,
    NoteSerializer,
    RegisterSerializer,
    TeamSerializer,
    UserSerializer,
)

User = get_user_model()


class RegisterView(generics.CreateAPIView):
    """Create an account and hand back a token in the same round trip.

    Registering and then immediately asking for a token is two calls for
    something the client always does together, so this returns both.
    """

    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {"user": UserSerializer(user).data, "token": token.key},
            status=201,
        )


class MeView(generics.RetrieveAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class TeamListCreateView(generics.ListCreateAPIView):
    """Teams the caller belongs to. Creating one makes you its first admin."""

    serializer_class = TeamSerializer

    def get_queryset(self):
        return (
            Team.objects.filter(memberships__user=self.request.user)
            .annotate(member_count=Count("memberships", distinct=True))
            .prefetch_related(
                Prefetch(
                    "memberships",
                    queryset=Membership.objects.filter(user=self.request.user),
                )
            )
            # annotate() introduces a GROUP BY, which drops Meta.ordering and
            # makes pagination non-deterministic. Order explicitly.
            .order_by("name")
        )

    def perform_create(self, serializer):
        team = serializer.save()
        Membership.objects.create(
            team=team, user=self.request.user, role=Membership.Role.ADMIN
        )


class TeamDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TeamSerializer
    permission_classes = [permissions.IsAuthenticated, IsTeamAdmin]
    lookup_field = "slug"

    def get_queryset(self):
        return (
            Team.objects.filter(memberships__user=self.request.user)
            .annotate(member_count=Count("memberships", distinct=True))
            .prefetch_related("memberships")
            .order_by("name")
        )


class MembershipListCreateView(generics.ListCreateAPIView):
    serializer_class = MembershipSerializer

    def get_team(self):
        return get_object_or_404(
            Team.objects.filter(memberships__user=self.request.user),
            slug=self.kwargs["slug"],
        )

    def get_queryset(self):
        return Membership.objects.filter(team=self.get_team()).select_related("user")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["team"] = self.get_team()
        return context

    def create(self, request, *args, **kwargs):
        team = self.get_team()
        is_admin = Membership.objects.filter(
            team=team, user=request.user, role=Membership.Role.ADMIN
        ).exists()
        if not is_admin:
            return Response(
                {"detail": "You must be an admin of this team."}, status=403
            )
        return super().create(request, *args, **kwargs)


class MembershipDetailView(generics.DestroyAPIView):
    """Remove someone from a team. Admins only, and never the last admin."""

    serializer_class = MembershipSerializer

    def get_object(self):
        team = get_object_or_404(
            Team.objects.filter(memberships__user=self.request.user),
            slug=self.kwargs["slug"],
        )
        return get_object_or_404(
            Membership.objects.select_related("team", "user"),
            team=team,
            user__username=self.kwargs["username"],
        )

    def perform_destroy(self, instance):
        caller_is_admin = Membership.objects.filter(
            team=instance.team, user=self.request.user, role=Membership.Role.ADMIN
        ).exists()
        if not caller_is_admin:
            raise PermissionDenied("You must be an admin of this team.")

        if instance.is_admin:
            remaining_admins = (
                Membership.objects.filter(
                    team=instance.team, role=Membership.Role.ADMIN
                )
                .exclude(pk=instance.pk)
                .count()
            )
            if remaining_admins == 0:
                raise ValidationError(
                    "Removing this member would leave the team with no admin."
                )
        instance.delete()


class NoteListCreateView(generics.ListCreateAPIView):
    """List notes the caller can see, newest first, or create one.

    The queryset is visible_to(user) before any filter runs, so the filter
    backends narrow an already-safe set rather than deciding access.
    """

    serializer_class = NoteSerializer
    filterset_class = NoteFilter
    ordering_fields = ["updated_at", "created_at", "title"]
    ordering = ["-updated_at"]

    def get_queryset(self):
        return (
            Note.objects.alive()
            .visible_to(self.request.user)
            .select_related("author", "team")
        )

    def perform_create(self, serializer):
        note = serializer.save(author=self.request.user)
        # Revision 1 is the note as created, so history is complete rather
        # than starting at the first edit.
        note.record_revision(self.request.user)


class NoteDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Read, edit or delete one note. DELETE is a soft delete."""

    serializer_class = NoteSerializer
    permission_classes = [permissions.IsAuthenticated, CanEditNote]

    def get_queryset(self):
        return (
            Note.objects.alive()
            .visible_to(self.request.user)
            .select_related("author", "team")
        )

    def retrieve(self, request, *args, **kwargs):
        """Return the note, with its version as an ETag so the client has
        something to send back on the next write."""
        instance = self.get_object()
        response = Response(self.get_serializer(instance).data)
        response["ETag"] = etag_for(instance)
        return response

    def update(self, request, *args, **kwargs):
        """Apply a change, refusing to overwrite a concurrent edit.

        DRF's default would call serializer.save(), which issues an
        unconditional UPDATE. Writing it out here lets the version check
        and the write happen in one statement.
        """
        partial = kwargs.pop("partial", False)
        instance = self.get_object()

        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        expected = expected_version_for(request, instance)

        try:
            note = instance.apply_update(
                editor=request.user,
                expected_version=expected,
                **serializer.validated_data,
            )
        except VersionConflict:
            instance.refresh_from_db()
            return Response(
                {
                    "detail": (
                        "This note was changed by someone else while you were "
                        "editing it. Your change was not applied."
                    ),
                    "your_version": expected,
                    "current_version": instance.version,
                    "current": self.get_serializer(instance).data,
                },
                status=409,
                headers={"ETag": etag_for(instance)},
            )

        response = Response(self.get_serializer(note).data)
        response["ETag"] = etag_for(note)
        return response

    def perform_destroy(self, instance):
        instance.soft_delete()


class NoteTrashView(generics.ListAPIView):
    """Notes the caller deleted, so they can find one to restore."""

    serializer_class = NoteSerializer
    ordering = ["-deleted_at"]

    def get_queryset(self):
        return (
            Note.objects.deleted()
            .filter(author=self.request.user)
            .select_related("author", "team")
        )


class NoteRestoreView(generics.GenericAPIView):
    serializer_class = NoteSerializer

    def get_queryset(self):
        return Note.objects.deleted().filter(author=self.request.user)

    def post(self, request, *args, **kwargs):
        note = get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])
        note.restore()
        return Response(self.get_serializer(note).data, status=200)


class NoteRevisionListView(generics.ListAPIView):
    """Every saved version of a note, newest first.

    Scoped through the same visible_to() queryset as the note itself, so
    history cannot be read by anyone who cannot read the note.
    """

    serializer_class = NoteRevisionSerializer

    def get_queryset(self):
        note = get_object_or_404(
            Note.objects.alive().visible_to(self.request.user),
            pk=self.kwargs["pk"],
        )
        return NoteRevision.objects.filter(note=note).select_related("edited_by")
