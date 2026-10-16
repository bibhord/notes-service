"""Concurrent edits to a shared note.

The scenario these exist for: two teammates open the same note, both
edit, both save. Without a version check the second write silently
destroys the first and neither user is told. Every test here interleaves
two clients deliberately.
"""

from django.urls import reverse
from rest_framework.test import APITestCase

from notes.models import Note, NoteRevision

from .factories import client_for, make_note, make_team, make_user


class LostUpdateTests(APITestCase):
    def setUp(self):
        self.ada = make_user("ada")
        self.grace = make_user("grace")
        self.team = make_team("Platform", admin=self.ada, members=[self.grace])
        self.note = make_note(self.ada, title="Runbook", body="milk", team=self.team)
        self.url = reverse("note-detail", args=[self.note.pk])

    def _read(self, client):
        """Read the note the way a client would, keeping its ETag."""
        response = client.get(self.url)
        return response.data, response["ETag"]

    def test_second_writer_is_rejected_rather_than_winning(self):
        ada, grace = client_for(self.ada), client_for(self.grace)

        _, ada_etag = self._read(ada)
        _, grace_etag = self._read(grace)
        self.assertEqual(ada_etag, grace_etag)  # both saw the same version

        first = ada.patch(
            self.url, {"body": "milk\nbread"}, HTTP_IF_MATCH=ada_etag
        )
        self.assertEqual(first.status_code, 200)

        second = grace.patch(
            self.url, {"body": "milk\ncoffee"}, HTTP_IF_MATCH=grace_etag
        )
        self.assertEqual(second.status_code, 409)

        self.note.refresh_from_db()
        self.assertEqual(self.note.body, "milk\nbread")  # ada's edit survived

    def test_conflict_response_carries_what_to_merge_against(self):
        """A bare 409 just makes the user retype. Hand back the current
        state so a client can show a diff."""
        ada, grace = client_for(self.ada), client_for(self.grace)
        _, stale = self._read(grace)
        ada.patch(self.url, {"body": "ada was here"}, HTTP_IF_MATCH=stale)

        conflict = grace.patch(
            self.url, {"body": "grace was here"}, HTTP_IF_MATCH=stale
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data["your_version"], 1)
        self.assertEqual(conflict.data["current_version"], 2)
        self.assertEqual(conflict.data["current"]["body"], "ada was here")

    def test_retrying_against_the_new_version_succeeds(self):
        ada, grace = client_for(self.ada), client_for(self.grace)
        _, stale = self._read(grace)
        ada.patch(self.url, {"body": "ada was here"}, HTTP_IF_MATCH=stale)

        conflict = grace.patch(self.url, {"body": "mine"}, HTTP_IF_MATCH=stale)
        fresh_etag = conflict["ETag"]

        retry = grace.patch(
            self.url, {"body": "ada was here\nand grace"}, HTTP_IF_MATCH=fresh_etag
        )
        self.assertEqual(retry.status_code, 200)

    def test_version_increments_on_every_write(self):
        client = client_for(self.ada)
        for expected in (2, 3, 4):
            response = client.patch(
                self.url, {"body": f"v{expected}"}, HTTP_IF_MATCH=f'"{expected - 1}"'
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["version"], expected)
            self.assertEqual(response["ETag"], f'"{expected}"')


class PreconditionRequirementTests(APITestCase):
    """Shared notes require the precondition. Private ones do not, because
    a note only one person can reach has no concurrent editors to protect
    against, and a mandatory read-before-write costs every client a round
    trip."""

    def setUp(self):
        self.ada = make_user("ada")
        self.client = client_for(self.ada)
        self.team = make_team("Platform", admin=self.ada)

    def test_blind_write_to_a_shared_note_is_refused(self):
        note = make_note(self.ada, title="Shared", team=self.team)
        response = self.client.patch(
            reverse("note-detail", args=[note.pk]), {"body": "blind"}
        )
        self.assertEqual(response.status_code, 428)

    def test_blind_write_to_a_private_note_is_allowed(self):
        note = make_note(self.ada, title="Mine")
        response = self.client.patch(
            reverse("note-detail", args=[note.pk]), {"body": "fine"}
        )
        self.assertEqual(response.status_code, 200)

    def test_version_may_be_sent_in_the_body_instead_of_a_header(self):
        """For clients that cannot set headers easily."""
        note = make_note(self.ada, title="Shared", team=self.team)
        response = self.client.patch(
            reverse("note-detail", args=[note.pk]),
            {"body": "via body", "version": note.version},
        )
        self.assertEqual(response.status_code, 200)

    def test_weak_etag_and_bare_integer_are_both_accepted(self):
        note = make_note(self.ada, title="Shared", team=self.team)
        url = reverse("note-detail", args=[note.pk])

        self.assertEqual(
            self.client.patch(url, {"body": "a"}, HTTP_IF_MATCH='W/"1"').status_code, 200
        )
        self.assertEqual(
            self.client.patch(url, {"body": "b"}, HTTP_IF_MATCH="2").status_code, 200
        )

    def test_stale_version_on_a_private_note_is_still_honoured(self):
        """Opting in to the check works even where it is not required."""
        note = make_note(self.ada, title="Mine")
        self.client.patch(reverse("note-detail", args=[note.pk]), {"body": "first"})

        response = self.client.patch(
            reverse("note-detail", args=[note.pk]),
            {"body": "stale"},
            HTTP_IF_MATCH='"1"',
        )
        self.assertEqual(response.status_code, 409)


class RevisionTests(APITestCase):
    def setUp(self):
        self.ada = make_user("ada")
        self.grace = make_user("grace")
        self.team = make_team("Platform", admin=self.ada, members=[self.grace])
        self.note = make_note(self.ada, title="Runbook", body="v1", team=self.team)
        self.note.record_revision(self.ada)
        self.url = reverse("note-detail", args=[self.note.pk])

    def test_every_save_leaves_a_revision(self):
        client_for(self.ada).patch(self.url, {"body": "v2"}, HTTP_IF_MATCH='"1"')
        client_for(self.grace).patch(self.url, {"body": "v3"}, HTTP_IF_MATCH='"2"')

        revisions = client_for(self.ada).get(
            reverse("note-revisions", args=[self.note.pk])
        )
        self.assertEqual(revisions.data["count"], 3)

        newest = revisions.data["results"][0]
        self.assertEqual(newest["version"], 3)
        self.assertEqual(newest["body"], "v3")
        self.assertEqual(newest["edited_by"]["username"], "grace")

    def test_a_rejected_write_leaves_no_revision(self):
        before = NoteRevision.objects.filter(note=self.note).count()
        response = client_for(self.grace).patch(
            self.url, {"body": "doomed"}, HTTP_IF_MATCH='"99"'
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(NoteRevision.objects.filter(note=self.note).count(), before)

    def test_an_overwritten_edit_is_recoverable(self):
        """The whole point: the lost line is still in the table."""
        client_for(self.ada).patch(self.url, {"body": "v1\nbread"}, HTTP_IF_MATCH='"1"')
        client_for(self.ada).patch(
            self.url, {"body": "something else entirely"}, HTTP_IF_MATCH='"2"'
        )

        bodies = list(
            NoteRevision.objects.filter(note=self.note).values_list("body", flat=True)
        )
        self.assertIn("v1\nbread", bodies)

    def test_history_is_not_readable_by_an_outsider(self):
        mallory = make_user("mallory")
        response = client_for(mallory).get(
            reverse("note-revisions", args=[self.note.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_creating_a_note_records_revision_one(self):
        response = client_for(self.ada).post(
            reverse("note-list"), {"title": "Fresh", "body": "start"}
        )
        note = Note.objects.get(pk=response.data["id"])
        self.assertEqual(note.revisions.count(), 1)
        self.assertEqual(note.revisions.first().version, 1)
