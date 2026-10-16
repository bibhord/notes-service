"""The tests that matter most: what one user can see and change of another's.

A note-taking service that leaks one team's notes to another is not a
note-taking service with a bug, it is a different and much worse product.
So these cases are written as the attacker's view: given a note I should
not have, what does every verb return?
"""

from django.urls import reverse
from rest_framework.test import APITestCase

from notes.models import Membership, Note

from .factories import client_for, make_note, make_team, make_user


class PrivateNoteIsolationTests(APITestCase):
    def setUp(self):
        self.ada = make_user("ada")
        self.mallory = make_user("mallory")
        self.note = make_note(self.ada, title="Ada's private note")
        self.client = client_for(self.mallory)

    def test_other_users_note_is_absent_from_the_list(self):
        response = self.client.get(reverse("note-list"))
        self.assertEqual(response.data["count"], 0)

    def test_other_users_note_reads_as_404_not_403(self):
        """404, not 403: a 403 confirms the note exists."""
        response = self.client.get(reverse("note-detail", args=[self.note.pk]))
        self.assertEqual(response.status_code, 404)

    def test_other_users_note_cannot_be_edited(self):
        response = self.client.patch(
            reverse("note-detail", args=[self.note.pk]), {"title": "mine now"}
        )
        self.assertEqual(response.status_code, 404)
        self.note.refresh_from_db()
        self.assertEqual(self.note.title, "Ada's private note")

    def test_other_users_note_cannot_be_deleted(self):
        response = self.client.delete(reverse("note-detail", args=[self.note.pk]))
        self.assertEqual(response.status_code, 404)
        self.note.refresh_from_db()
        self.assertIsNone(self.note.deleted_at)

    def test_search_cannot_be_used_to_confirm_contents(self):
        """A targeted search must not turn into an oracle for notes you
        cannot read."""
        make_note(self.ada, title="Q4 layoffs", body="confidential")
        response = self.client.get(reverse("note-list"), {"q": "layoffs"})
        self.assertEqual(response.data["count"], 0)


class TeamSharingTests(APITestCase):
    def setUp(self):
        self.ada = make_user("ada")
        self.grace = make_user("grace")
        self.outsider = make_user("mallory")
        self.team = make_team("Platform", admin=self.ada, members=[self.grace])
        self.note = make_note(self.ada, title="Runbook", team=self.team)

    def test_team_member_sees_the_note(self):
        response = client_for(self.grace).get(reverse("note-list"))
        self.assertEqual(response.data["count"], 1)

    def test_team_member_can_edit_a_shared_note(self):
        response = client_for(self.grace).patch(
            reverse("note-detail", args=[self.note.pk]), {"body": "updated steps"}
        )
        self.assertEqual(response.status_code, 200)

    def test_plain_member_cannot_delete_someone_elses_team_note(self):
        """Editing is shared; deleting is not."""
        response = client_for(self.grace).delete(
            reverse("note-detail", args=[self.note.pk])
        )
        self.assertEqual(response.status_code, 403)
        self.note.refresh_from_db()
        self.assertIsNone(self.note.deleted_at)

    def test_author_can_delete_their_own_team_note(self):
        response = client_for(self.ada).delete(
            reverse("note-detail", args=[self.note.pk])
        )
        self.assertEqual(response.status_code, 204)

    def test_team_admin_can_delete_a_members_note(self):
        grace_note = make_note(self.grace, title="Grace's", team=self.team)
        response = client_for(self.ada).delete(
            reverse("note-detail", args=[grace_note.pk])
        )
        self.assertEqual(response.status_code, 204)

    def test_outsider_sees_nothing(self):
        response = client_for(self.outsider).get(reverse("note-list"))
        self.assertEqual(response.data["count"], 0)

    def test_removing_a_member_revokes_access_immediately(self):
        Membership.objects.filter(team=self.team, user=self.grace).delete()
        response = client_for(self.grace).get(reverse("note-detail", args=[self.note.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_post_a_note_into_a_team_you_are_not_on(self):
        response = client_for(self.outsider).post(
            reverse("note-list"),
            {"title": "sneaking in", "visibility": "team", "team": self.team.slug},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("team", response.data)

    def test_cannot_move_a_private_note_into_a_foreign_team(self):
        own_note = make_note(self.outsider, title="mine")
        response = client_for(self.outsider).patch(
            reverse("note-detail", args=[own_note.pk]),
            {"visibility": "team", "team": self.team.slug},
        )
        self.assertEqual(response.status_code, 400)


class VisibilityConsistencyTests(APITestCase):
    def setUp(self):
        self.ada = make_user("ada")
        self.client = client_for(self.ada)
        self.team = make_team("Platform", admin=self.ada)

    def test_team_visibility_without_a_team_is_rejected(self):
        response = self.client.post(
            reverse("note-list"), {"title": "orphan", "visibility": "team"}
        )
        self.assertEqual(response.status_code, 400)

    def test_private_note_with_a_team_is_rejected(self):
        response = self.client.post(
            reverse("note-list"),
            {"title": "confused", "visibility": "private", "team": self.team.slug},
        )
        self.assertEqual(response.status_code, 400)

    def test_sharing_a_private_note_with_my_own_team_works(self):
        note = make_note(self.ada, title="promote me")
        response = self.client.patch(
            reverse("note-detail", args=[note.pk]),
            {"visibility": "team", "team": self.team.slug},
        )
        self.assertEqual(response.status_code, 200)
        note.refresh_from_db()
        self.assertEqual(note.visibility, Note.Visibility.TEAM)
        self.assertEqual(note.team, self.team)
