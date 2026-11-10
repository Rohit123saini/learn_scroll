"""X12 L-D #3 / #4: the live-chat cache alias and the per-message mute lookup cache. No DB: the DB query is patched and
session / user are stand-ins, so these run as plain SimpleTestCase (LocMemCache is fine - the logic is the same on Redis)."""
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.core.cache import caches
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from tuitionclass import live_abuse
from tuitionclass.live_cache import live_cache, live_cache_alias

_SESSION = SimpleNamespace(id=11)
_USER = SimpleNamespace(id=5)


class LiveCacheAliasTests(SimpleTestCase):
    def test_falls_back_to_default_without_live_alias(self):
        self.assertEqual(live_cache_alias(), "default")

    def test_uses_live_alias_when_configured(self):
        cfg = {
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "t-default"},
            "live": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "t-live"},
        }
        with override_settings(CACHES=cfg):
            self.assertEqual(live_cache_alias(), "live")
            live_cache.set("tc:probe", 1, 30)
            self.assertEqual(caches["live"].get("tc:probe"), 1)
            self.assertIsNone(caches["default"].get("tc:probe"))
            caches["live"].clear()


@override_settings(LIVE_MUTE_CACHE_SECONDS=20)
class MuteLookupCacheTests(SimpleTestCase):
    def setUp(self):
        live_cache.clear()

    def tearDown(self):
        live_cache.clear()

    def test_not_muted_is_cached(self):
        with mock.patch.object(live_abuse, "_query_chat_mute_until", return_value=None) as q:
            for _ in range(5):
                self.assertIsNone(live_abuse.chat_mute_until(_SESSION, _USER))
        self.assertEqual(q.call_count, 1)

    def test_mute_is_cached_and_not_longer_than_the_db_value(self):
        until = timezone.now() + timedelta(minutes=5)
        with mock.patch.object(live_abuse, "_query_chat_mute_until", return_value=until) as q:
            first = live_abuse.chat_mute_until(_SESSION, _USER)
            second = live_abuse.chat_mute_until(_SESSION, _USER)
        self.assertEqual(q.call_count, 1)
        self.assertLess(abs(first.timestamp() - until.timestamp()), 0.01)
        self.assertLess(abs(second.timestamp() - until.timestamp()), 0.01)

    def test_remember_mute_is_seen_by_the_very_next_message(self):
        until = timezone.now() + timedelta(minutes=3)
        with mock.patch.object(live_abuse, "_query_chat_mute_until", return_value=None) as q:
            self.assertIsNone(live_abuse.chat_mute_until(_SESSION, _USER))     # caches "not muted"
            live_abuse.remember_mute(_SESSION, _USER, until)
            got = live_abuse.chat_mute_until(_SESSION, _USER)
        self.assertIsNotNone(got)
        self.assertEqual(q.call_count, 1)                                       # the mute came from the cache

    def test_forget_mute_makes_the_next_message_ask_the_db(self):
        until = timezone.now() + timedelta(minutes=3)
        live_abuse.remember_mute(_SESSION, _USER, until)
        live_abuse.forget_mute(_SESSION, _USER)
        with mock.patch.object(live_abuse, "_query_chat_mute_until", return_value=None) as q:
            self.assertIsNone(live_abuse.chat_mute_until(_SESSION, _USER))
        self.assertEqual(q.call_count, 1)

    def test_other_user_and_other_session_do_not_share_the_answer(self):
        live_abuse.remember_mute(_SESSION, _USER, timezone.now() + timedelta(minutes=3))
        with mock.patch.object(live_abuse, "_query_chat_mute_until", return_value=None) as q:
            self.assertIsNone(live_abuse.chat_mute_until(_SESSION, SimpleNamespace(id=6)))
            self.assertIsNone(live_abuse.chat_mute_until(SimpleNamespace(id=12), _USER))
        self.assertEqual(q.call_count, 2)

    def test_an_already_ended_cached_mute_falls_back_to_the_db(self):
        live_cache.set(live_abuse._mute_key(_SESSION, _USER), timezone.now().timestamp() - 5, 30)
        with mock.patch.object(live_abuse, "_query_chat_mute_until", return_value=None) as q:
            self.assertIsNone(live_abuse.chat_mute_until(_SESSION, _USER))
        self.assertEqual(q.call_count, 1)

    def test_ensure_can_chat_raises_chat_muted_from_the_cache(self):
        live_abuse.remember_mute(_SESSION, _USER, timezone.now() + timedelta(minutes=2))
        with mock.patch.object(live_abuse, "_query_chat_mute_until", side_effect=AssertionError("DB must not be hit")):
            with self.assertRaises(live_abuse.LiveSafetyError) as ctx:
                live_abuse.ensure_can_chat(_SESSION, _USER)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.machine_code, "chat_muted")

    @override_settings(LIVE_MUTE_CACHE_SECONDS=0)
    def test_zero_turns_the_cache_off(self):
        with mock.patch.object(live_abuse, "_query_chat_mute_until", return_value=None) as q:
            for _ in range(3):
                live_abuse.chat_mute_until(_SESSION, _USER)
        self.assertEqual(q.call_count, 3)

    def test_cache_failure_falls_back_to_the_db(self):
        with mock.patch.object(live_abuse.cache, "get", side_effect=RuntimeError("redis down")), \
                mock.patch.object(live_abuse.cache, "add", side_effect=RuntimeError("redis down")), \
                mock.patch.object(live_abuse, "_query_chat_mute_until", return_value=None) as q:
            self.assertIsNone(live_abuse.chat_mute_until(_SESSION, _USER))
            self.assertIsNone(live_abuse.chat_mute_until(_SESSION, _USER))
        self.assertEqual(q.call_count, 2)
