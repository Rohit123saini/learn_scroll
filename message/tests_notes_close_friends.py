# message/tests_notes_close_friends.py
"""
T0.3 — close-friends audience wale notes: close friend ko dikhe, doosre ko nahi.

Run:  python manage.py test message.tests_notes_close_friends

Real `post.CloseFriend` (field `owner`/`friend`) use hota hai — pehle default
'user' tha jo FieldError deta tha aur lookup fail-closed hokar sabke liye hide.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from post.models import CloseFriend

from .models import BlockedUser, NoteAudience, UserNote
from .user_notes import visible_notes_qs

User = get_user_model()


class CloseFriendsNotesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', password='x')
        self.friend = User.objects.create_user(username='friend', password='x')
        self.stranger = User.objects.create_user(username='stranger', password='x')
        CloseFriend.objects.create(owner=self.owner, friend=self.friend)

        self.owner_api = APIClient()
        self.owner_api.force_authenticate(self.owner)
        res = self.owner_api.put(
            '/message/notes/me/',
            {'text': 'only for besties', 'audience': NoteAudience.CLOSE_FRIENDS},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.data)

    def _list_for(self, user):
        api = APIClient()
        api.force_authenticate(user)
        res = api.get('/message/notes/')
        self.assertEqual(res.status_code, 200)
        return [n['text'] for n in res.data['results']]

    def test_close_friend_sees_the_note(self):
        self.assertEqual(self._list_for(self.friend), ['only for besties'])

    def test_non_close_friend_does_not_see_the_note(self):
        self.assertEqual(self._list_for(self.stranger), [])

    def test_direction_matters_owner_is_not_friends_close_friend(self):
        # friend ne owner ko close-friend nahi banaya => friend ka close-friends note owner ko nahi dikhna chahiye
        UserNote.objects.create(
            user=self.friend, text='friend private', audience=NoteAudience.CLOSE_FRIENDS,
            expires_at=UserNote.objects.get(user=self.owner).expires_at,
        )
        self.assertEqual(self._list_for(self.owner), [])

    def test_removed_close_friend_loses_access(self):
        CloseFriend.objects.filter(owner=self.owner, friend=self.friend).delete()
        self.assertEqual(self._list_for(self.friend), [])

    def test_block_still_hides_note_from_close_friend(self):
        BlockedUser.objects.create(blocker=self.owner, blocked=self.friend)
        self.assertEqual(self._list_for(self.friend), [])

    def test_lookup_resolves_without_field_error(self):
        # Regression: "Cannot resolve keyword 'user_id' ... Choices are: owner, owner_id"
        qs = visible_notes_qs(self.friend)
        self.assertEqual([n.text for n in qs], ['only for besties'])
