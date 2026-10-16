from django.urls import reverse
from rest_framework.test import APITestCase

from notes.models import Membership, Note

from .factories import client_for, make_note, make_team, make_user


class NoteCrudTests(APITestCase):
    def setUp(self):
        self.ada = make_user("ada")
        self.client = client_for(self.ada)

    def test_create_private_note(self):
        response = self.client.post(
            reverse("note-list"), {"title": "Groceries", "body": "milk"}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["visibility"], "private")
        self.assertEqual(response.data["author"]["username"], "ada")
        self.assertIsNone(response.data["team"])

    def test_title_is_required(self):
        response = self.client.post(reverse("note-list"), {"body": "no title"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("title", response.data)

    def test_update_note(self):
        note = make_note(self.ada, title="Draft")
        response = self.client.patch(
            reverse("note-detail", args=[note.pk]), {"title": "Final"}
        )
        self.assertEqual(response.status_code, 200)
        note.refresh_from_db()
        self.assertEqual(note.title, "Final")

    def test_author_is_not_caller_controlled(self):
        """Posting someone else's id in `author` must not change ownership."""
        mallory = make_user("mallory")
        response = self.client.post(
            reverse("note-list"), {"title": "Mine", "author": mallory.pk}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Note.objects.get(pk=response.data["id"]).author, self.ada)


class SoftDeleteTests(APITestCase):
    def setUp(self):
        self.ada = make_user("ada")
        self.client = client_for(self.ada)
        self.note = make_note(self.ada, title="Keep me")

    def test_delete_hides_the_note_but_keeps_the_row(self):
        response = self.client.delete(reverse("note-detail", args=[self.note.pk]))
        self.assertEqual(response.status_code, 204)

        self.assertEqual(self.client.get(reverse("note-list")).data["count"], 0)
        self.note.refresh_from_db()
        self.assertIsNotNone(self.note.deleted_at)

    def test_deleted_note_is_not_retrievable(self):
        self.note.soft_delete()
        response = self.client.get(reverse("note-detail", args=[self.note.pk]))
        self.assertEqual(response.status_code, 404)

    def test_trash_lists_deleted_notes_and_restore_brings_them_back(self):
        self.note.soft_delete()

        trash = self.client.get(reverse("note-trash"))
        self.assertEqual(trash.data["count"], 1)

        restored = self.client.post(reverse("note-restore", args=[self.note.pk]))
        self.assertEqual(restored.status_code, 200)
        self.assertIsNone(restored.data["deleted_at"])
        self.assertEqual(self.client.get(reverse("note-list")).data["count"], 1)


class SearchAndFilterTests(APITestCase):
    def setUp(self):
        self.ada = make_user("ada")
        self.client = client_for(self.ada)
        self.team = make_team("Platform", admin=self.ada)
        make_note(self.ada, title="Deploy runbook", body="how to ship")
        make_note(self.ada, title="Grocery list", body="milk and bread")
        make_note(self.ada, title="Team standup", body="notes", team=self.team)

    def test_search_matches_title(self):
        response = self.client.get(reverse("note-list"), {"q": "runbook"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Deploy runbook")

    def test_search_matches_body_case_insensitively(self):
        response = self.client.get(reverse("note-list"), {"q": "MILK"})
        self.assertEqual(response.data["count"], 1)

    def test_filter_by_team(self):
        response = self.client.get(reverse("note-list"), {"team": self.team.slug})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Team standup")

    def test_filter_by_visibility(self):
        response = self.client.get(reverse("note-list"), {"visibility": "private"})
        self.assertEqual(response.data["count"], 2)

    def test_results_are_paginated(self):
        response = self.client.get(reverse("note-list"), {"page_size": 2})
        self.assertEqual(len(response.data["results"]), 2)
        self.assertEqual(response.data["count"], 3)
        self.assertIsNotNone(response.data["next"])


class ExMemberTests(APITestCase):
    """Someone removed from a team keeps their own notes usable."""

    def setUp(self):
        self.ada = make_user("ada")
        self.grace = make_user("grace")
        self.team = make_team("One", admin=self.ada, members=[self.grace])
        self.note = make_note(self.grace, title="Grace's", body="v1", team=self.team)
        Membership.objects.filter(team=self.team, user=self.grace).delete()

    def test_ex_member_can_still_read_their_own_note(self):
        response = client_for(self.grace).get(
            reverse("note-detail", args=[self.note.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_ex_member_can_still_edit_their_own_note(self):
        """Readable but not editable was the inconsistency."""
        self.note.refresh_from_db()
        response = client_for(self.grace).patch(
            reverse("note-detail", args=[self.note.pk]),
            {"body": "v2"},
            HTTP_IF_MATCH=f'"{self.note.version}"',
        )
        self.assertEqual(response.status_code, 200)

    def test_ex_member_cannot_put_a_new_note_in_that_team(self):
        response = client_for(self.grace).post(
            reverse("note-list"),
            {"title": "new", "visibility": "team", "team": self.team.slug},
        )
        self.assertEqual(response.status_code, 400)


class AdminRestoreTests(APITestCase):
    """A team admin may delete a member's note, so they must be able to
    put it back."""

    def setUp(self):
        self.ada = make_user("ada")
        self.grace = make_user("grace")
        self.team = make_team("One", admin=self.ada, members=[self.grace])
        self.note = make_note(self.grace, title="Grace's", team=self.team)

    def test_admin_can_restore_a_note_they_deleted(self):
        admin = client_for(self.ada)
        self.assertEqual(
            admin.delete(reverse("note-detail", args=[self.note.pk])).status_code, 204
        )
        self.assertEqual(
            admin.post(reverse("note-restore", args=[self.note.pk])).status_code, 200
        )

    def test_admin_sees_the_teams_deleted_notes_in_trash(self):
        self.note.soft_delete()
        response = client_for(self.ada).get(reverse("note-trash"))
        self.assertEqual(response.data["count"], 1)

    def test_a_plain_member_cannot_restore_someone_elses_note(self):
        self.note.soft_delete()
        other = make_user("mallory")
        Membership.objects.create(team=self.team, user=other)
        response = client_for(other).post(
            reverse("note-restore", args=[self.note.pk])
        )
        self.assertEqual(response.status_code, 404)
