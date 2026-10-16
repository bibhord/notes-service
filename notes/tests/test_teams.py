from django.urls import reverse
from rest_framework.test import APITestCase

from notes.models import Membership, Team

from .factories import client_for, make_team, make_user


class TeamTests(APITestCase):
    def setUp(self):
        self.ada = make_user("ada")
        self.client = client_for(self.ada)

    def test_creating_a_team_makes_you_its_admin(self):
        response = self.client.post(reverse("team-list"), {"name": "Platform"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["slug"], "platform")

        listed = self.client.get(reverse("team-list"))
        self.assertEqual(listed.data["results"][0]["my_role"], "admin")
        self.assertEqual(listed.data["results"][0]["member_count"], 1)

    def test_teams_you_are_not_on_are_invisible(self):
        make_team("Secret", admin=make_user("grace"))
        response = self.client.get(reverse("team-list"))
        self.assertEqual(response.data["count"], 0)

    def test_duplicate_names_get_distinct_slugs(self):
        first = self.client.post(reverse("team-list"), {"name": "Platform"})
        second = self.client.post(reverse("team-list"), {"name": "Platform"})
        self.assertNotEqual(first.data["slug"], second.data["slug"])


class MembershipTests(APITestCase):
    def setUp(self):
        self.ada = make_user("ada")
        self.grace = make_user("grace")
        self.team = make_team("Platform", admin=self.ada)

    def test_admin_can_add_a_member(self):
        response = client_for(self.ada).post(
            reverse("membership-list", args=[self.team.slug]), {"username": "grace"}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["user"]["username"], "grace")
        self.assertEqual(response.data["role"], "member")

    def test_plain_member_cannot_add_people(self):
        Membership.objects.create(team=self.team, user=self.grace)
        outsider = make_user("mallory")
        response = client_for(self.grace).post(
            reverse("membership-list", args=[self.team.slug]),
            {"username": outsider.username},
        )
        self.assertEqual(response.status_code, 403)

    def test_adding_an_unknown_user_is_a_400(self):
        response = client_for(self.ada).post(
            reverse("membership-list", args=[self.team.slug]), {"username": "nobody"}
        )
        self.assertEqual(response.status_code, 400)

    def test_adding_the_same_person_twice_is_rejected(self):
        url = reverse("membership-list", args=[self.team.slug])
        client_for(self.ada).post(url, {"username": "grace"})
        again = client_for(self.ada).post(url, {"username": "grace"})
        self.assertEqual(again.status_code, 400)

    def test_admin_can_remove_a_member(self):
        Membership.objects.create(team=self.team, user=self.grace)
        response = client_for(self.ada).delete(
            reverse("membership-detail", args=[self.team.slug, "grace"])
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            Membership.objects.filter(team=self.team, user=self.grace).exists()
        )

    def test_cannot_remove_the_last_admin(self):
        response = client_for(self.ada).delete(
            reverse("membership-detail", args=[self.team.slug, "ada"])
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            Membership.objects.filter(team=self.team, user=self.ada).exists()
        )

    def test_outsider_cannot_see_the_member_list(self):
        mallory = make_user("mallory")
        response = client_for(mallory).get(
            reverse("membership-list", args=[self.team.slug])
        )
        self.assertEqual(response.status_code, 404)


class TeamDeletionTests(APITestCase):
    """Deleting a team used to cascade and hard-delete every note in it,
    including other people's, with no undo."""

    def setUp(self):
        self.ada = make_user("ada")
        self.grace = make_user("grace")
        self.team = make_team("One", admin=self.ada, members=[self.grace])
        self.url = reverse("team-detail", args=[self.team.slug])

    def test_deleting_a_team_with_notes_is_refused(self):
        from notes.models import Note

        from .factories import make_note

        make_note(self.grace, title="Grace's work", team=self.team)

        response = client_for(self.ada).delete(self.url)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Note.objects.count(), 1)
        self.assertTrue(Team.objects.filter(pk=self.team.pk).exists())

    def test_a_soft_deleted_note_still_blocks_deletion(self):
        """It is in the trash, not gone. Deleting the team would make it
        unrecoverable."""
        from .factories import make_note

        note = make_note(self.grace, title="trashed", team=self.team)
        note.soft_delete()

        self.assertEqual(client_for(self.ada).delete(self.url).status_code, 409)

    def test_an_empty_team_can_be_deleted(self):
        response = client_for(self.ada).delete(self.url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Team.objects.filter(pk=self.team.pk).exists())


class LeavingAndRolesTests(APITestCase):
    def setUp(self):
        self.ada = make_user("ada")
        self.grace = make_user("grace")
        self.team = make_team("One", admin=self.ada, members=[self.grace])

    def _url(self, username):
        return reverse("membership-detail", args=[self.team.slug, username])

    def test_a_member_can_remove_themselves(self):
        """A team you cannot leave is a trap."""
        response = client_for(self.grace).delete(self._url("grace"))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            Membership.objects.filter(team=self.team, user=self.grace).exists()
        )

    def test_the_last_admin_cannot_leave(self):
        response = client_for(self.ada).delete(self._url("ada"))
        self.assertEqual(response.status_code, 400)

    def test_an_admin_can_leave_once_another_admin_exists(self):
        Membership.objects.filter(team=self.team, user=self.grace).update(role="admin")
        self.assertEqual(client_for(self.ada).delete(self._url("ada")).status_code, 204)

    def test_admin_can_promote_a_member(self):
        response = client_for(self.ada).patch(self._url("grace"), {"role": "admin"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role"], "admin")

    def test_a_member_cannot_promote_themselves(self):
        response = client_for(self.grace).patch(self._url("grace"), {"role": "admin"})
        self.assertEqual(response.status_code, 403)

    def test_the_last_admin_cannot_be_demoted(self):
        response = client_for(self.ada).patch(self._url("ada"), {"role": "member"})
        self.assertEqual(response.status_code, 400)
