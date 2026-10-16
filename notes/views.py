from django.contrib.auth import get_user_model
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from .models import Membership, Team
from .permissions import IsTeamAdmin
from .serializers import (
    MembershipSerializer,
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
