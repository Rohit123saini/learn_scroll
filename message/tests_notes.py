# message/tests_notes.py
"""
M2-BE — Notes (Instagram-style status). `sticky_notes` ke tests alag file me hain.

Follow / CloseFriend ke asli model/field naam is repo me visible nahi the, isliye
yahan sirf wahi pin kiya hai jo un par nirbhar nahi: validation, replace, +24h,
delete, expiry filter, cleanup, aur FAIL-CLOSED (lookup toote to kuch leak nahi).
Follow/close-friend positive-path ke liye apne models ke saath ek test jodna.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import NoteAudience, UserNote
from .tasks import cleanup_expired_notes

User = get_user_model()

BROKEN_LOOKUPS = dict(
    MESSAGE_REQUESTS_FOLLOW_MODEL='nope.Nope',
    NOTES_CLOSE_FRIEND_MODEL='nope.Nope',
)


class UserNotesTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='x')
        self.bob = User.objects.create_user(username='bob', password='x')
        self.a = APIClient()
        self.a.force_authenticate(self.alice)
        self.b = APIClient()
        self.b.force_authenticate(self.bob)

    def test_put_creates_note_with_24h_expiry(self):
        res = self.a.put('/message/notes/me/', {'text': '  hello   world ', 'emoji': '🔥'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['text'], 'hello world')
        self.assertEqual(res.data['audience'], NoteAudience.FOLLOWERS)
        note = UserNote.objects.get(user=self.alice)
        delta = note.expires_at - timezone.now()
        self.assertTrue(timedelta(hours=23, minutes=59) < delta <= timedelta(hours=24))

    def test_put_replaces_existing_note(self):
        self.a.put('/message/notes/me/', {'text': 'one'}, format='json')
        self.a.put('/message/notes/me/', {'text': 'two', 'audience': 'close_friends'}, format='json')
        self.assertEqual(UserNote.objects.filter(user=self.alice).count(), 1)
        note = UserNote.objects.get(user=self.alice)
        self.assertEqual((note.text, note.audience), ('two', NoteAudience.CLOSE_FRIENDS))

    def test_validation(self):
        self.assertEqual(self.a.put('/message/notes/me/', {'text': 'x' * 61}, format='json').status_code, 400)
        self.assertEqual(self.a.put('/message/notes/me/', {'text': 'x' * 60}, format='json').status_code, 200)
        self.assertEqual(self.a.put('/message/notes/me/', {'text': '   ', 'emoji': ''}, format='json').status_code, 400)
        self.assertEqual(self.a.put('/message/notes/me/', {'text': 'hi', 'audience': 'public'}, format='json').status_code, 400)
        self.assertEqual(self.a.put('/message/notes/me/', {'emoji': '🙂'}, format='json').status_code, 200)

    def test_delete_is_idempotent(self):
        self.a.put('/message/notes/me/', {'text': 'bye'}, format='json')
        self.assertEqual(self.a.delete('/message/notes/me/').status_code, 204)
        self.assertEqual(self.a.delete('/message/notes/me/').status_code, 204)
        self.assertFalse(UserNote.objects.filter(user=self.alice).exists())

    def test_auth_required(self):
        anon = APIClient()
        self.assertIn(anon.get('/message/notes/').status_code, (401, 403))
        self.assertIn(anon.put('/message/notes/me/', {'text': 'x'}, format='json').status_code, (401, 403))

    def test_list_returns_my_note_and_never_my_own_in_results(self):
        self.a.put('/message/notes/me/', {'text': 'mine'}, format='json')
        res = self.a.get('/message/notes/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['my_note']['text'], 'mine')
        self.assertEqual(res.data['results'], [])

    def test_my_note_hidden_once_expired(self):
        self.a.put('/message/notes/me/', {'text': 'old'}, format='json')
        UserNote.objects.filter(user=self.alice).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(self.a.get('/message/notes/').data['my_note'])

    @override_settings(**BROKEN_LOOKUPS)
    def test_fail_closed_when_relation_lookups_break(self):
        for audience in ('followers', 'close_friends'):
            self.a.put('/message/notes/me/', {'text': 'secret', 'audience': audience}, format='json')
            res = self.b.get('/message/notes/')
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.data['results'], [], audience)

    def test_cleanup_deletes_only_expired(self):
        UserNote.objects.create(user=self.alice, text='old', expires_at=timezone.now() - timedelta(hours=1))
        UserNote.objects.create(user=self.bob, text='new', expires_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(cleanup_expired_notes()['deleted'], 1)
        self.assertEqual(list(UserNote.objects.values_list('text', flat=True)), ['new'])
