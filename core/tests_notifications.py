"""
core/tests_notifications.py   (N12 — notification tests)

Run:  python manage.py test core.tests_notifications
Dev dep:  pip install freezegun      (quiet-hours tests skip without it)

Four groups:
  1. CategoryMapTests    — every NotifType is categorised ON PURPOSE.
  2. MuteActorTests      — NotificationMute (N6-BE) via the real API.
  3. QuietHoursTests     — 23:00-07:00 window, midnight wrap, timezones, DST.
  4. BatchingTests       — like/comment/follow through the API collapse into
                           one row. SKIPS until the adapter block is wired.

⚠️ Written WITHOUT core/models.py, core/views.py or the batching code in
front of me. Everything I had to assume lives in the clearly marked
"ADAPTER" blocks — nothing else in this file should need touching.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from .models import Notification, NotificationMute, NotificationPreference
from .services import channels_for, create_notification

try:  # dev-only dependency
    from freezegun import freeze_time
except ImportError:  # pragma: no cover
    freeze_time = None

NT = Notification.NotifType
User = get_user_model()


def _make_user(name):
    # ADAPTER: if your custom User needs more required fields, add them here.
    return User.objects.create_user(username=name, password="pw12345!")


def _all_notif_types():
    """Every NotifType value — works for TextChoices and plain-constant classes."""
    if hasattr(NT, "values"):
        return list(NT.values)
    return [v for k, v in vars(NT).items() if k.isupper() and isinstance(v, str)]


# =====================================================================
# 1. CATEGORY MAP
# =====================================================================
# Categories the client filters on (notification_service.dart, N4-FE).
VALID_CATEGORIES = {"mentions", "follows", "classroom", "tests", "other"}

# Types that are deliberately "other". `category_for()` almost certainly falls
# back to "other" for anything unmapped — so "resolves to other" alone proves
# nothing. A type may only land in "other" if it is listed HERE, i.e. a human
# said so. Add a NotifType without mapping it => test_no_silent_other fails
# and prints the line to paste if "other" really is right.
INTENTIONALLY_OTHER: set = set()  # first run will list the current candidates


class CategoryMapTests(SimpleTestCase):
    def test_every_type_resolves_to_a_valid_category(self):
        for t in _all_notif_types():
            with self.subTest(notif_type=t):
                self.assertIn(Notification.category_for(t), VALID_CATEGORIES)

    def test_no_silent_other(self):
        silent = sorted(
            t for t in _all_notif_types()
            if Notification.category_for(t) == "other" and t not in INTENTIONALLY_OTHER
        )
        self.assertEqual(
            silent, [],
            "NotifType(s) fall into 'other' without being mapped. Map them in "
            "core/models.py, or — if 'other' is really right — add to "
            f"INTENTIONALLY_OTHER:\n{silent!r}",
        )

    def test_intentionally_other_has_no_stale_entries(self):
        types = set(_all_notif_types())
        stale = sorted(
            t for t in INTENTIONALLY_OTHER
            if t not in types or Notification.category_for(t) != "other"
        )
        self.assertEqual(stale, [], f"Stale INTENTIONALLY_OTHER entries: {stale!r}")

    def test_core_types_land_where_the_client_expects(self):
        # Pinned to the Dart filter tabs (follows / mentions).
        self.assertEqual(Notification.category_for(NT.FOLLOW_REQUEST_RECEIVED), "follows")
        self.assertEqual(Notification.category_for(NT.FOLLOW_REQUEST_ACCEPTED), "follows")
        self.assertEqual(Notification.category_for(NT.MENTION), "mentions")


# =====================================================================
# 2. MUTE ACTOR
# =====================================================================
# ADAPTER: URL prefix as the Dart client builds it: '${Api.baseUrl}/core/...'.
# If Api.baseUrl ends in '/api', make this '/api/core/notification-mutes/'.
MUTE_URL = "/core/notification-mutes/"


class MuteActorTests(TestCase):
    def setUp(self):
        self.recipient = _make_user("recipient")
        self.noisy = _make_user("noisy")
        self.friend = _make_user("friend")
        self.bystander = _make_user("bystander")
        self.api = APIClient()
        self.api.force_authenticate(self.recipient)

    def _notify(self, recipient, actor=None):
        return create_notification(
            recipient, NT.FOLLOW_REQUEST_RECEIVED, "title", "msg", actor=actor,
            data={"follow_id": 1},
        )

    def _mute(self, user):
        return self.api.post(f"{MUTE_URL}{user.id}/")

    def test_muted_actor_creates_no_row(self):
        self.assertIn(self._mute(self.noisy).status_code, (200, 201))
        self.assertTrue(NotificationMute.is_muted(self.recipient.id, self.noisy.id))

        self.assertIsNone(self._notify(self.recipient, actor=self.noisy))
        self.assertEqual(Notification.objects.filter(recipient=self.recipient).count(), 0)

    def test_muted_actor_passed_as_raw_id_is_skipped_too(self):
        self._mute(self.noisy)
        self.assertIsNone(self._notify(self.recipient, actor=self.noisy.id))
        self.assertEqual(Notification.objects.count(), 0)

    def test_other_actors_unaffected(self):
        self._mute(self.noisy)
        row = self._notify(self.recipient, actor=self.friend)
        self.assertIsNotNone(row)
        self.assertEqual(row.data.get("actor_id"), self.friend.id)  # Task 6 folding

    def test_system_notification_without_actor_unaffected(self):
        self._mute(self.noisy)
        self.assertIsNotNone(self._notify(self.recipient, actor=None))

    def test_mute_is_per_recipient(self):
        self._mute(self.noisy)  # recipient muted noisy, bystander did NOT
        self.assertIsNotNone(self._notify(self.bystander, actor=self.noisy))

    def test_mute_is_directional(self):
        # recipient muting noisy must not silence recipient's actions towards noisy.
        self._mute(self.noisy)
        self.assertIsNotNone(self._notify(self.noisy, actor=self.recipient))

    def test_mute_is_idempotent(self):
        first = self._mute(self.noisy)
        second = self._mute(self.noisy)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(NotificationMute.objects.count(), 1)

    def test_existing_rows_are_not_removed(self):
        before = self._notify(self.recipient, actor=self.noisy)
        self.assertIsNotNone(before)
        self._mute(self.noisy)
        self.assertTrue(Notification.objects.filter(id=before.id).exists())

    def test_unmute_restores_delivery(self):
        self._mute(self.noisy)
        self.assertIsNone(self._notify(self.recipient, actor=self.noisy))

        self.assertIn(self.api.delete(f"{MUTE_URL}{self.noisy.id}/").status_code, (200, 204))
        self.assertFalse(NotificationMute.is_muted(self.recipient.id, self.noisy.id))
        self.assertIsNotNone(self._notify(self.recipient, actor=self.noisy))

    def test_unmute_is_idempotent(self):
        r = self.api.delete(f"{MUTE_URL}{self.noisy.id}/")  # never muted
        self.assertIn(r.status_code, (200, 204))


# =====================================================================
# 3. QUIET HOURS
# =====================================================================
# ADAPTER: NotificationPreference field names (N9-BE). Rename to match.
QH_ENABLED, QH_START, QH_END, QH_TZ = (
    "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end", "timezone",
)
# ADAPTER: a notif_type the N9-BE docs call "urgent" (bypasses quiet hours).
URGENT_TYPE = None  # e.g. NT.SOMETHING_URGENT; None => that test is skipped

# Window under test: 23:00 -> 07:00 (start inclusive, end exclusive).
QUIET_START, QUIET_END = time(23, 0), time(7, 0)


def _utc_for_local(tz_name, y, mo, d, h, mi):
    """Aware UTC datetime for a wall-clock time in `tz_name` (no DST guessing:
    callers only pass times that exist)."""
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tz_name)).astimezone(ZoneInfo("UTC"))


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("UTC"))


class QuietHoursTests(TestCase):
    TYPE = NT.FOLLOW_REQUEST_ACCEPTED  # ordinary, non-urgent type

    def setUp(self):
        if freeze_time is None:
            self.skipTest("pip install freezegun to run quiet-hours tests")
        self.user = _make_user("sleeper")

    def _configure(self, tz="Asia/Kolkata", start=QUIET_START, end=QUIET_END, enabled=True):
        NotificationPreference.objects.update_or_create(
            user_id=self.user.id,
            defaults={QH_ENABLED: enabled, QH_START: start, QH_END: end, QH_TZ: tz},
        )

    def _channels_at(self, utc_dt):
        with freeze_time(utc_dt):
            return channels_for(self.user, self.TYPE)

    def assertQuiet(self, utc_dt, label=""):
        self.assertEqual(self._channels_at(utc_dt), [], f"expected QUIET at {label or utc_dt}")

    def assertAllowed(self, utc_dt, label=""):
        self.assertTrue(self._channels_at(utc_dt), f"expected ALLOWED at {label or utc_dt}")

    # -- window edges + midnight wrap (23:00 -> 07:00) -------------------
    def test_window_edges_and_midnight_wrap_ist(self):
        self._configure("Asia/Kolkata")
        tz = "Asia/Kolkata"
        cases = [  # (day, hh, mm, quiet?)
            (1, 12, 0, False), (1, 22, 59, False),
            (1, 23, 0, True),            # start inclusive
            (1, 23, 30, True), (1, 23, 59, True),
            (2, 0, 0, True),             # the midnight boundary — naive start<=t<=end breaks here
            (2, 0, 1, True), (2, 3, 0, True),
            (2, 6, 59, True),
            (2, 7, 0, False),            # end exclusive
            (2, 7, 1, False),
        ]
        for day, h, m, quiet in cases:
            with self.subTest(local=f"Oct {day} {h:02d}:{m:02d}"):
                at = _utc_for_local(tz, 2026, 10, day, h, m)
                (self.assertQuiet if quiet else self.assertAllowed)(at)

    def test_non_wrapping_window_still_works(self):
        self._configure("Asia/Kolkata", start=time(13, 0), end=time(15, 0))
        tz = "Asia/Kolkata"
        self.assertAllowed(_utc_for_local(tz, 2026, 10, 1, 12, 59))
        self.assertQuiet(_utc_for_local(tz, 2026, 10, 1, 13, 0))
        self.assertQuiet(_utc_for_local(tz, 2026, 10, 1, 14, 0))
        self.assertAllowed(_utc_for_local(tz, 2026, 10, 1, 15, 0))
        self.assertAllowed(_utc_for_local(tz, 2026, 10, 2, 3, 0))  # night is NOT quiet here

    # -- same instant, different user timezone ---------------------------
    def test_same_instant_depends_on_user_timezone(self):
        instant = _utc(2026, 10, 1, 18, 0)  # 23:30 IST / 03:00+1 JST / 14:00 EDT / 11:00 PDT
        expected = {
            "Asia/Kolkata": True,
            "Asia/Tokyo": True,
            "America/New_York": False,
            "America/Los_Angeles": False,
        }
        for tz, quiet in expected.items():
            with self.subTest(tz=tz):
                self._configure(tz)
                (self.assertQuiet if quiet else self.assertAllowed)(instant, tz)

    def test_utc_midnight_is_not_user_midnight(self):
        # 00:00 UTC is 05:30 IST (quiet) but 20:00 EDT / 17:00 PDT (allowed).
        instant = _utc(2026, 10, 2, 0, 0)
        self._configure("Asia/Kolkata");        self.assertQuiet(instant, "IST")
        self._configure("America/New_York");    self.assertAllowed(instant, "EDT")

    # -- DST (a fixed UTC offset implementation gets these wrong) --------
    def test_dst_spring_forward_new_york(self):
        self._configure("America/New_York")  # 2026-03-08: 02:00 EST -> 03:00 EDT at 07:00Z
        self.assertQuiet(_utc(2026, 3, 8, 6, 30), "01:30 EST")
        self.assertQuiet(_utc(2026, 3, 8, 7, 30), "03:30 EDT")
        self.assertQuiet(_utc(2026, 3, 8, 10, 59), "06:59 EDT")
        self.assertAllowed(_utc(2026, 3, 8, 11, 0), "07:00 EDT (a fixed -5h offset says 06:00 = quiet)")

    def test_dst_fall_back_new_york(self):
        self._configure("America/New_York")  # 2026-11-01: 02:00 EDT -> 01:00 EST at 06:00Z
        self.assertAllowed(_utc(2026, 11, 1, 2, 59), "22:59 EDT, Oct 31")
        self.assertQuiet(_utc(2026, 11, 1, 3, 0), "23:00 EDT, Oct 31")
        self.assertQuiet(_utc(2026, 11, 1, 5, 30), "01:30 EDT (first)")
        self.assertQuiet(_utc(2026, 11, 1, 6, 30), "01:30 EST (second)")
        self.assertQuiet(_utc(2026, 11, 1, 11, 0), "06:00 EST (a fixed -4h offset says 07:00 = allowed)")
        self.assertQuiet(_utc(2026, 11, 1, 11, 59), "06:59 EST")
        self.assertAllowed(_utc(2026, 11, 1, 12, 0), "07:00 EST")

    # -- switches --------------------------------------------------------
    def test_disabled_quiet_hours_never_silence(self):
        self._configure("Asia/Kolkata", enabled=False)
        self.assertAllowed(_utc_for_local("Asia/Kolkata", 2026, 10, 2, 3, 0))

    def test_urgent_type_bypasses_quiet_hours(self):
        if URGENT_TYPE is None:
            self.skipTest("set URGENT_TYPE to a type N9-BE treats as urgent")
        self._configure("Asia/Kolkata")
        with freeze_time(_utc_for_local("Asia/Kolkata", 2026, 10, 2, 3, 0)):
            self.assertTrue(channels_for(self.user, URGENT_TYPE))
            self.assertEqual(channels_for(self.user, self.TYPE), [])  # control


# =====================================================================
# 4. BATCHING (like / comment / follow through the real API)
# =====================================================================
# ADAPTER BLOCK — the ONLY place that knows your URLs & batching shape.
# Implement these five, then the whole class runs. Until then every test
# skips (NotImplementedError) instead of failing for the wrong reason.
#
# ASSUMED SEMANTICS (edit the tests if N-batching differs):
#   * k distinct actors liking/commenting on the SAME post  -> ONE row
#     for the owner, count == k.
#   * different posts -> separate rows.
#   * like -> unlike -> like by the same actor never double-counts.
#   * once the owner has READ the batched row, the next like makes a NEW
#     unread row (otherwise the bell would never light up again).
#   * follow-REQUEST rows are NEVER batched: each carries its own follow_id
#     that the Accept/Decline push buttons (PUSH_ACTIONS) require.
#   * a muted actor's like neither creates nor joins a batch.

def api_like(client, post):            raise NotImplementedError   # POST like (toggle?)
def api_unlike(client, post):          raise NotImplementedError
def api_comment(client, post, text):   raise NotImplementedError
def api_follow(client, target_user):   raise NotImplementedError   # follow/<user_id>/
def make_post(owner):                  raise NotImplementedError   # -> post object

LIKE_TYPE = getattr(NT, "POST_LIKE", None)
COMMENT_TYPE = getattr(NT, "POST_COMMENT", None)
FOLLOW_TYPE = getattr(NT, "NEW_FOLLOWER", None)


def batched_count(row):
    """How many actors a batched row represents. ADAPTER: match your shape."""
    data = row.data or {}
    return data.get("actor_count") or len(data.get("actor_ids", [])) or 1


class BatchingTests(TestCase):
    def setUp(self):
        self.owner = _make_user("owner")
        self.actors = [_make_user(f"fan{i}") for i in range(5)]
        try:
            self.post = make_post(self.owner)
        except NotImplementedError:
            self.skipTest("wire the adapter block (make_post/api_*) to run batching tests")
        if None in (LIKE_TYPE, COMMENT_TYPE, FOLLOW_TYPE):
            self.skipTest("set LIKE_TYPE / COMMENT_TYPE / FOLLOW_TYPE to your real NotifType names")

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def _rows(self, notif_type, recipient=None):
        return Notification.objects.filter(recipient=recipient or self.owner, notif_type=notif_type)

    def test_many_likes_collapse_into_one_row(self):
        for a in self.actors:
            api_like(self._client(a), self.post)
        rows = self._rows(LIKE_TYPE)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(batched_count(rows.first()), len(self.actors))

    def test_many_comments_collapse_into_one_row(self):
        for i, a in enumerate(self.actors):
            api_comment(self._client(a), self.post, f"nice {i}")
        rows = self._rows(COMMENT_TYPE)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(batched_count(rows.first()), len(self.actors))

    def test_different_posts_are_separate_rows(self):
        other_post = make_post(self.owner)
        api_like(self._client(self.actors[0]), self.post)
        api_like(self._client(self.actors[0]), other_post)
        self.assertEqual(self._rows(LIKE_TYPE).count(), 2)

    def test_like_unlike_like_does_not_double_count(self):
        c = self._client(self.actors[0])
        api_like(c, self.post)
        api_unlike(c, self.post)
        api_like(c, self.post)
        rows = self._rows(LIKE_TYPE)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(batched_count(rows.first()), 1)

    def test_new_like_after_read_makes_a_fresh_unread_row(self):
        api_like(self._client(self.actors[0]), self.post)
        self._rows(LIKE_TYPE).update(is_read=True)
        api_like(self._client(self.actors[1]), self.post)
        self.assertTrue(self._rows(LIKE_TYPE).filter(is_read=False).exists())

    def test_muted_actor_does_not_join_a_batch(self):
        # Mute through the real API (no model field names needed).
        self._client(self.owner).post(f"{MUTE_URL}{self.actors[0].id}/")
        api_like(self._client(self.actors[0]), self.post)       # muted
        api_like(self._client(self.actors[1]), self.post)       # not muted
        rows = self._rows(LIKE_TYPE)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(batched_count(rows.first()), 1)

    def test_follow_requests_are_never_batched(self):
        # Private account: each request needs its own follow_id for Accept/Decline.
        # ADAPTER: make `self.owner` private here if your API needs it.
        for a in self.actors[:3]:
            api_follow(self._client(a), self.owner)
        rows = self._rows(NT.FOLLOW_REQUEST_RECEIVED)
        self.assertEqual(rows.count(), 3)
        follow_ids = {r.data.get("follow_id") for r in rows}
        self.assertNotIn(None, follow_ids)
        self.assertEqual(len(follow_ids), 3)

    def test_public_follows_collapse_into_one_row(self):
        for a in self.actors:
            api_follow(self._client(a), self.owner)
        rows = self._rows(FOLLOW_TYPE)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(batched_count(rows.first()), len(self.actors))
