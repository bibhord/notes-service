from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from .models import Membership, Note, NoteRevision, Team

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email"]
        read_only_fields = fields


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = User
        fields = ["id", "username", "email", "password"]

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class MembershipSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    username = serializers.CharField(write_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "username", "role", "joined_at"]
        read_only_fields = ["id", "user", "joined_at"]

    def validate_username(self, value):
        try:
            self._user = User.objects.get(username=value)
        except User.DoesNotExist:
            raise serializers.ValidationError("No user with that username.")
        return value

    def create(self, validated_data):
        validated_data.pop("username")
        team = self.context["team"]
        membership, created = Membership.objects.get_or_create(
            team=team,
            user=self._user,
            defaults={"role": validated_data.get("role", Membership.Role.MEMBER)},
        )
        if not created:
            raise serializers.ValidationError(
                {"username": "That user is already on this team."}
            )
        return membership


class TeamSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = ["id", "name", "slug", "created_at", "member_count", "my_role"]
        read_only_fields = ["id", "slug", "created_at", "member_count", "my_role"]

    def get_my_role(self, obj) -> str | None:
        membership = next(
            (m for m in obj.memberships.all() if m.user_id == self.context["request"].user.id),
            None,
        )
        return membership.role if membership else None


class NoteSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    team = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Team.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Note
        fields = [
            "id",
            "title",
            "body",
            "visibility",
            "team",
            "author",
            "created_at",
            "updated_at",
            "deleted_at",
            "version",
        ]
        read_only_fields = [
            "id",
            "author",
            "created_at",
            "updated_at",
            "deleted_at",
            "version",
        ]

    def validate(self, attrs):
        """Keep visibility and team consistent, and refuse to post into a
        team the caller is not on.

        PATCH sends partial data, so each field falls back to the value
        already on the instance rather than to a default.
        """
        instance = self.instance
        visibility = attrs.get(
            "visibility",
            instance.visibility if instance else Note.Visibility.PRIVATE,
        )
        team = attrs.get("team", instance.team if instance else None)

        # Un-sharing: "make this private" is unambiguous, so clear the team
        # rather than rejecting the request for still having one. Only do
        # this when the caller said `private` explicitly and said nothing
        # about the team -- passing both private and a team is a real
        # contradiction and still fails below.
        if (
            attrs.get("visibility") == Note.Visibility.PRIVATE
            and "team" not in attrs
        ):
            team = None

        user = self.context["request"].user

        # Who can see a note is the author's call, not the team's.
        #
        # Editing a shared note is collaborative -- any team member can fix
        # the text. Re-pointing it is not. Without this check a plain member
        # could move someone else's note into an unrelated team they happen
        # to belong to, exposing it to people the author has no relationship
        # with, or flip it to private and cut the real team off.
        if instance is not None and user.id != instance.author_id:
            if visibility != instance.visibility or team != instance.team:
                raise PermissionDenied(
                    "Only the author can change who a note is shared with."
                )

        if visibility == Note.Visibility.TEAM:
            if team is None:
                raise serializers.ValidationError(
                    {"team": "A team note must specify a team."}
                )
            # Membership is checked when a note is pointed at a team, not on
            # every subsequent edit. Someone who leaves a team keeps their
            # own notes readable (they are the author) and editable; what
            # they lose is the ability to put anything new there. Re-checking
            # on every write left ex-members able to read a note they could
            # no longer fix.
            team_is_changing = instance is None or team != instance.team
            if team_is_changing and not Membership.objects.filter(
                team=team, user=user
            ).exists():
                raise serializers.ValidationError(
                    {"team": "You are not a member of that team."}
                )
        elif team is not None:
            raise serializers.ValidationError(
                {"team": "A private note cannot belong to a team."}
            )

        attrs["visibility"] = visibility
        attrs["team"] = team
        return attrs


class NoteRevisionSerializer(serializers.ModelSerializer):
    edited_by = UserSerializer(read_only=True)

    class Meta:
        model = NoteRevision
        fields = ["id", "version", "title", "body", "edited_by", "created_at"]
        read_only_fields = fields


class MembershipRoleSerializer(serializers.ModelSerializer):
    """Promote or demote an existing member. Role is the only field a
    membership has that can meaningfully change."""

    user = UserSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "role", "joined_at"]
        read_only_fields = ["id", "user", "joined_at"]
