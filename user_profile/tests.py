from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import BlockUser, Follow

User = get_user_model()


class FollowModelTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")

    def test_self_follow_blocked_at_db_level(self):
        with self.assertRaises(IntegrityError):
            Follow.objects.create(follower=self.alice, following=self.alice)


class FollowAPITests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")
        self.client.force_authenticate(user=self.alice)

    def test_cannot_follow_yourself(self):
        url = reverse("follow-user", args=[self.alice.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_follow_then_unfollow_updates_counts(self):
        url = reverse("follow-user", args=[self.bob.id])
        self.client.post(url)
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 1)
        self.assertEqual(self.bob.followers_count, 1)

        self.client.post(url)  # unfollow
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 0)
        self.assertEqual(self.bob.followers_count, 0)

    def test_blocked_user_cannot_be_followed(self):
        BlockUser.objects.create(blocker=self.bob, blocked=self.alice)
        url = reverse("follow-user", args=[self.bob.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_private_profile_hides_bio_from_non_follower(self):
        self.bob.is_private = True
        self.bob.bio = "secret bio"
        self.bob.save()

        url = reverse("user-profile-detail", args=[self.bob.username])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_restricted_view"])
        self.assertNotIn("bio", response.data["data"])

    def test_blocked_user_gets_404_on_profile_lookup(self):
        BlockUser.objects.create(blocker=self.bob, blocked=self.alice)
        url = reverse("user-profile-detail", args=[self.bob.username])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)