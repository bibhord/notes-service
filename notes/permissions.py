from rest_framework import permissions

from .models import Membership, Note


class IsTeamAdmin(permissions.BasePermission):
    """Allow writes only to admins of the team in the URL."""

    message = "You must be an admin of this team."

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return Membership.objects.filter(
            team=obj, user=request.user, role=Membership.Role.ADMIN
        ).exists()


class CanEditNote(permissions.BasePermission):
    """Who may change a note.

    Reading is already handled by the queryset, so by the time an object
    reaches here the user can at least see it. The remaining question is
    whether they may change it.

    The author can always edit and delete. A team note can be edited by any
    member of that team, because a shared note nobody but the author can fix
    is not really shared. Deleting is narrower: author or team admin only,
    since deletion is the destructive one.
    """

    message = "You do not have permission to change this note."

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True

        user = request.user
        if obj.author_id == user.id:
            return True
        if obj.visibility != Note.Visibility.TEAM:
            return False

        membership = Membership.objects.filter(team_id=obj.team_id, user=user).first()
        if membership is None:
            return False
        if request.method == "DELETE":
            return membership.is_admin
        return True
