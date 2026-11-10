"""H18 study digest tests (written, NOT run). Run: python manage.py test core.tests_content_digest"""
from datetime import datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from login.models import User
from post.models import Post, TopicFollow
from user_profile.models import UserPreference

from . import content_digest as cd
from .models import Notification, NotificationPreference

IST = ZoneInfo("Asia/Kolkata")
NT = Notification.NotifType.CONTENT_DIGEST
SUNDAY = datetime(2026, 10, 25).date()  # a Sunday


def at(hour, day=SUNDAY):
    return datetime.combine(day, time(hour), tzinfo=IST)


def _user(name):
    return User.objects.create_user(username=name, password="Str0ng-pass-123!", email=f"{name}@example.com")


def _post(author, created, **kw):
    fields = {"visibility": "public", "moderation_status": "approved", **kw}
    p = Post.objects.create(user=author, content="x", **fields)
    Post.objects.filter(pk=p.pk).update(created_at=created)
    return p


class DigestBase(TestCase):
    def setUp(self):
        self.me = _user("dg_me")
        self.author = _user("dg_author")
        self.pref = NotificationPreference.objects.create(
            user=self.me, content_digest_frequency="daily", content_digest_hour=18, timezone="Asia/Kolkata",
        )
        UserPreference.for_user(self.me)
        up = UserPreference.for_user(self.me)
        up.focus_subjects = ["physics"]
        up.save()
        self.now = at(18)
        self.recent = self.now - timedelta(hours=5)


class BuildTests(DigestBase):
    def test_counts_new_notes_per_subject_and_posts_per_followed_topic(self):
        for _ in range(5):
            _post(self.author, self.recent, kind=Post.KIND_RESOURCE, subcategory="Physics")
        _post(self.author, self.recent, kind=Post.KIND_RESOURCE, subcategory="Chemistry")  # not my subject
        TopicFollow.objects.create(user=self.me, topic="kinematics")
        for _ in range(3):
            _post(self.author, self.recent, hashtags=["kinematics"])
        _post(self.author, self.now - timedelta(days=3), hashtags=["kinematics"])  # older than the window
        items = cd.build_items(self.me, self.pref, self.now - timedelta(days=1))
        self.assertEqual(items, [
            {"kind": "notes", "subject": "physics", "count": 5},
            {"kind": "topic", "topic": "kinematics", "count": 3},
        ])
        title, msg = cd.compose(items, "en")
        self.assertEqual(msg, "Physics: 5 new notes · #kinematics: 3 new posts")
        self.assertIn("नए नोट्स", cd.compose(items, "hi")[1])

    def test_own_private_and_unapproved_posts_do_not_count(self):
        _post(self.me, self.recent, kind=Post.KIND_RESOURCE, subcategory="physics")
        Post.objects.filter(user=self.me).update(created_at=self.recent)
        _post(self.author, self.recent, kind=Post.KIND_RESOURCE, subcategory="physics", visibility="followers")
        p = _post(self.author, self.recent, kind=Post.KIND_RESOURCE, subcategory="physics")
        Post.objects.filter(pk=p.pk).update(moderation_status="pending")
        self.assertEqual(cd.build_items(self.me, self.pref, self.now - timedelta(days=1)), [])

    def test_section_switches(self):
        _post(self.author, self.recent, kind=Post.KIND_RESOURCE, subcategory="physics")
        TopicFollow.objects.create(user=self.me, topic="optics")
        _post(self.author, self.recent, hashtags=["optics"])
        self.pref.content_digest_topics = False
        kinds = {i["kind"] for i in cd.build_items(self.me, self.pref, self.now - timedelta(days=1))}
        self.assertEqual(kinds, {"notes"})


class RunTests(DigestBase):
    def _run(self, when):
        with mock.patch("core.tasks.push_for_notification_row") as push:
            return cd.run(now=when), push

    def test_creates_one_row_pushes_and_is_idempotent(self):
        _post(self.author, self.recent, kind=Post.KIND_RESOURCE, subcategory="physics")
        summary, push = self._run(self.now)
        self.assertEqual(summary["created"], 1)
        row = Notification.objects.get(recipient=self.me, notif_type=NT)
        self.assertEqual(row.data["push_state"], "sent")
        push.assert_called_once()
        summary, push = self._run(self.now + timedelta(hours=1))  # same local day
        self.assertEqual(Notification.objects.filter(recipient=self.me, notif_type=NT).count(), 1)
        push.assert_not_called()

    def test_not_before_the_chosen_hour_and_never_empty(self):
        _post(self.author, self.recent, kind=Post.KIND_RESOURCE, subcategory="physics")
        self.assertEqual(self._run(at(17))[0]["created"], 0)
        Post.objects.all().delete()
        summary, _ = self._run(self.now)
        self.assertEqual((summary["created"], summary["empty"]), (0, 1))
        self.assertFalse(Notification.objects.filter(notif_type=NT).exists())

    def test_weekly_only_on_the_weekly_day(self):
        self.pref.content_digest_frequency = "weekly"
        self.pref.save()
        _post(self.author, self.now - timedelta(days=2), kind=Post.KIND_RESOURCE, subcategory="physics")
        monday = SUNDAY + timedelta(days=1)
        self.assertEqual(self._run(at(18, monday))[0]["created"], 0)
        self.assertEqual(self._run(at(18, SUNDAY))[0]["created"], 1)

    def test_quiet_hours_hold_the_push_then_release_it(self):
        from datetime import time as t
        self.pref.quiet_start, self.pref.quiet_end = t(17, 0), t(21, 0)
        self.pref.save()
        _post(self.author, self.recent, kind=Post.KIND_RESOURCE, subcategory="physics")
        _, push = self._run(self.now)
        push.assert_not_called()
        row = Notification.objects.get(recipient=self.me, notif_type=NT)
        self.assertEqual(row.data["push_state"], "pending")  # bell row exists, push waits
        _, push = self._run(at(21, SUNDAY).replace(minute=30))
        push.assert_called_once()
        row.refresh_from_db()
        self.assertEqual(row.data["push_state"], "sent")

    def test_pending_push_expires(self):
        from datetime import time as t
        self.pref.quiet_start, self.pref.quiet_end = t(17, 0), t(21, 0)
        self.pref.save()
        _post(self.author, self.recent, kind=Post.KIND_RESOURCE, subcategory="physics")
        self._run(self.now)
        # the row is stamped with the real clock; the test runs on a simulated one, so line them up
        Notification.objects.filter(notif_type=NT).update(created_at=self.now)
        _, push = self._run(self.now + timedelta(hours=13))
        push.assert_not_called()
        self.assertEqual(Notification.objects.get(notif_type=NT).data["push_state"], "expired")

    def test_muted_type_off_and_push_disabled(self):
        _post(self.author, self.recent, kind=Post.KIND_RESOURCE, subcategory="physics")
        self.pref.muted_types = [NT]
        self.pref.save()
        self.assertEqual(self._run(self.now)[0]["skipped"], 1)
        self.assertFalse(Notification.objects.filter(notif_type=NT).exists())

    def test_off_means_off(self):
        self.pref.content_digest_frequency = "off"
        self.pref.save()
        _post(self.author, self.recent, kind=Post.KIND_RESOURCE, subcategory="physics")
        self.assertEqual(self._run(self.now)[0]["checked"], 0)

    def test_missing_pref_row_is_created_for_a_topic_follower(self):
        u = _user("dg_follower")
        TopicFollow.objects.create(user=u, topic="optics")
        self.assertEqual(cd.ensure_prefs(), 1)
        self.assertEqual(NotificationPreference.objects.get(user=u).content_digest_frequency, "weekly")


class PreferenceApiTests(TestCase):
    def setUp(self):
        self.u = _user("dg_api")
        self.c = APIClient()
        self.c.force_authenticate(self.u)

    def test_patch_digest_settings(self):
        r = self.c.patch("/core/notification-preferences/me/", {
            "content_digest_frequency": "daily", "content_digest_hour": 7,
            "content_digest_notes": False, "timezone": "Asia/Kolkata",
        }, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["content_digest_frequency"], "daily")
        self.assertFalse(r.json()["content_digest_notes"])
        self.assertEqual(self.c.patch("/core/notification-preferences/me/", {"content_digest_hour": 24}, format="json").status_code, 400)
        self.assertEqual(self.c.patch("/core/notification-preferences/me/", {"content_digest_frequency": "hourly"}, format="json").status_code, 400)
