from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

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

        if visibility == Note.Visibility.TEAM:
            if team is None:
                raise serializers.ValidationError(
                    {"team": "A team note must specify a team."}
                )
            user = self.context["request"].user
            if not Membership.objects.filter(team=team, user=user).exists():
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
