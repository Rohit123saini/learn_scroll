"""Slow-mode gate (views/live_chat.py::_enforce_slow_mode). No DB: session/user are stand-ins and
`_can_moderate_session` is patched, so these run as plain SimpleTestCase."""
from types import SimpleNamespace
from unittest import mock

from tuitionclass.live_cache import live_cache as cache   # the cache slow mode really uses (L-D #3)
from django.test import SimpleTestCase
from rest_framework.exceptions import Throttled

from tuitionclass.views import live_chat


class SlowModeGateTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.user = SimpleNamespace(id=7)

    def _session(self, seconds, sid=1):
        return SimpleNamespace(id=sid, chat_slow_mode_seconds=seconds)

    def test_off_never_blocks(self):
        s = self._session(0)
        for _ in range(3):
            live_chat._enforce_slow_mode(s, self.user)

    def test_second_message_inside_gap_is_throttled(self):
        s = self._session(30)
        with mock.patch.object(live_chat, "_can_moderate_session", return_value=False):
            live_chat._enforce_slow_mode(s, self.user)
            with self.assertRaises(Throttled) as ctx:
                live_chat._enforce_slow_mode(s, self.user)
        self.assertTrue(1 <= ctx.exception.wait <= 30)

    def test_moderator_is_exempt(self):
        s = self._session(30)
        with mock.patch.object(live_chat, "_can_moderate_session", return_value=True):
            for _ in range(3):
                live_chat._enforce_slow_mode(s, self.user)

    def test_gap_is_per_user_and_per_session(self):
        with mock.patch.object(live_chat, "_can_moderate_session", return_value=False):
            live_chat._enforce_slow_mode(self._session(30, sid=1), self.user)
            live_chat._enforce_slow_mode(self._session(30, sid=2), self.user)  # other session
            live_chat._enforce_slow_mode(self._session(30, sid=1), SimpleNamespace(id=8))  # other user
