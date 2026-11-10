"""
F2 (story tray / upload / limits) backend tests - NOT RUN when written (read-only session).
Run: python manage.py test post.tests_story_f2

Covers:
  * active cap        STORY_MAX_ACTIVE_PER_USER -> 429 `story_limit_active` + Retry-After, staff exempt, expired /
                      soft-deleted stories do not count, upload-queue style request (Idempotency-Key) is refused too and
                      leaves no idempotency record behind (so the same key can be retried later)
  * expires_at clamp  never longer than 24h, never shorter than 30 min (view level + `clean_expires_at` unit level)
  * limits endpoint   GET /post/stories/limits/
  * expiry sweep      `expire_old_stories` (batch UPDATE) + the two beat entries in settings.CELERY_BEAT_SCHEDULE
  * live order        GET /post/stories/live/ -> joinable classes first, then the rest, newest start first in each group
"""
import shutil
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from . import story_limits
from .models import IdempotencyRecord, Story
from .tests_story_stickers import _TMP_MEDIA, image_upload, make_story, make_user


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class _Base(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def _create(self, headers=None, **data):
        payload = {"media": image_upload(), "media_type": "image", **data}
        return self.client.post(reverse("story-create"), payload, format="multipart", **(headers or {}))


# ---------------------------------------------------------------------------------------------------------------
# Active cap
# ---------------------------------------------------------------------------------------------------------------
@override_settings(STORY_MAX_ACTIVE_PER_USER=3)
class StoryActiveCapTests(_Base):
    def setUp(self):
        self.user = make_user("f2_cap_user")
        self.client.force_authenticate(self.user)

    def _fill(self, n, *, hours_left=10):
        rows = []
        for _ in range(n):
            s = make_story(self.user)
            Story.objects.filter(id=s.id).update(expires_at=timezone.now() + timedelta(hours=hours_left))
            rows.append(s)
        return rows

    def test_below_cap_can_post(self):
        self._fill(2)
        res = self._create()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(story_limits.active_count(self.user), 3)

    def test_at_cap_is_refused_with_429_and_retry_after(self):
        rows = self._fill(3, hours_left=10)
        # the OLDEST-ending story frees the first slot -> it decides Retry-After
        Story.objects.filter(id=rows[0].id).update(expires_at=timezone.now() + timedelta(hours=2))
        before = Story.objects.filter(user=self.user).count()

        res = self._create()

        self.assertEqual(res.status_code, 429, getattr(res, "data", None))
        self.assertEqual(res.data["code"], story_limits.CODE_ACTIVE_LIMIT)
        self.assertEqual(res.data["code"], "story_limit_active")
        self.assertEqual(res.data["limit"], 3)
        self.assertEqual(res.data["active"], 3)
        self.assertFalse(res.data["success"])
        self.assertGreater(res.data["retry_after"], 7100)  # ~2h until the earliest-ending story frees a slot
        self.assertLess(res.data["retry_after"], 7300)
        self.assertEqual(res["Retry-After"], str(res.data["retry_after"]))
        self.assertEqual(Story.objects.filter(user=self.user).count(), before)  # nothing was created

    def test_retry_after_is_at_least_a_minute(self):
        rows = self._fill(3)
        Story.objects.filter(id=rows[0].id).update(expires_at=timezone.now() + timedelta(seconds=5))
        res = self._create()
        self.assertEqual(res.status_code, 429)
        self.assertGreaterEqual(res.data["retry_after"], 60)

    def test_expired_and_deleted_stories_do_not_count(self):
        rows = self._fill(3)
        # one ended by itself (not swept yet), one removed by the owner -> only 1 live story left
        Story.objects.filter(id=rows[0].id).update(expires_at=timezone.now() - timedelta(minutes=1))
        rows[1].soft_delete()
        self.assertEqual(story_limits.active_count(self.user), 1)
        res = self._create()
        self.assertEqual(res.status_code, 201, res.data)

    def test_other_users_stories_do_not_count(self):
        other = make_user("f2_cap_other")
        for _ in range(3):
            make_story(other)
        res = self._create()
        self.assertEqual(res.status_code, 201, res.data)

    def test_staff_is_exempt(self):
        staff = make_user("f2_cap_staff", is_staff=True)
        for _ in range(5):
            make_story(staff)
        self.assertIsNone(story_limits.check_active_limit(staff))
        self.assertTrue(story_limits.is_exempt(staff))
        # a normal user in the same situation is NOT exempt
        for _ in range(3):
            make_story(self.user)
        self.assertIsNotNone(story_limits.check_active_limit(self.user))

    def test_upload_queue_request_is_refused_and_leaves_no_idempotency_record(self):
        rows = self._fill(3)
        key = "f2-queue-key-0001"
        res = self._create(headers={"HTTP_IDEMPOTENCY_KEY": key})
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data["code"], "story_limit_active")
        # a failed (4xx) first request leaves no record -> the very same key is usable once a slot is free
        self.assertFalse(IdempotencyRecord.objects.filter(user=self.user, key=key).exists())
        rows[0].soft_delete()
        res = self._create(headers={"HTTP_IDEMPOTENCY_KEY": key})
        self.assertEqual(res.status_code, 201, res.data)

    def test_cap_is_checked_before_anything_else_is_created(self):
        self._fill(3)
        res = self._create(caption="hello")
        self.assertEqual(res.status_code, 429)
        self.assertFalse(Story.objects.filter(user=self.user, caption="hello").exists())

    def test_limits_helpers_respect_settings(self):
        self.assertEqual(story_limits.max_active(), 3)
        with override_settings(STORY_MAX_ACTIVE_PER_USER=0):
            self.assertEqual(story_limits.max_active(), 1)  # never below 1


class StoryActiveCapDefaultTests(SimpleTestCase):
    def test_defaults(self):
        self.assertEqual(story_limits.DEFAULT_MAX_ACTIVE, 30)
        self.assertEqual(story_limits.DEFAULT_MAX_LIFETIME_HOURS, 24)
        self.assertEqual(story_limits.DEFAULT_MIN_LIFETIME_MINUTES, 30)


# ---------------------------------------------------------------------------------------------------------------
# expires_at clamp
# ---------------------------------------------------------------------------------------------------------------
class CleanExpiresAtUnitTests(SimpleTestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_none_stays_none(self):
        self.assertIsNone(story_limits.clean_expires_at(None, self.now))

    def test_too_far_is_pulled_to_max(self):
        out = story_limits.clean_expires_at(self.now + timedelta(days=30), self.now)
        self.assertEqual(out, self.now + timedelta(hours=24))

    def test_too_soon_is_pushed_to_min(self):
        out = story_limits.clean_expires_at(self.now + timedelta(minutes=1), self.now)
        self.assertEqual(out, self.now + timedelta(minutes=30))

    def test_in_the_past_is_pushed_to_min(self):
        out = story_limits.clean_expires_at(self.now - timedelta(days=1), self.now)
        self.assertEqual(out, self.now + timedelta(minutes=30))

    def test_inside_the_window_is_kept(self):
        v = self.now + timedelta(hours=6)
        self.assertEqual(story_limits.clean_expires_at(v, self.now), v)

    def test_edges_are_kept(self):
        lo = self.now + timedelta(minutes=30)
        hi = self.now + timedelta(hours=24)
        self.assertEqual(story_limits.clean_expires_at(lo, self.now), lo)
        self.assertEqual(story_limits.clean_expires_at(hi, self.now), hi)

    @override_settings(STORY_MAX_LIFETIME_HOURS=12, STORY_MIN_LIFETIME_MINUTES=60)
    def test_settings_change_the_window(self):
        self.assertEqual(
            story_limits.clean_expires_at(self.now + timedelta(hours=20), self.now), self.now + timedelta(hours=12)
        )
        self.assertEqual(
            story_limits.clean_expires_at(self.now + timedelta(minutes=45), self.now), self.now + timedelta(minutes=60)
        )


class StoryExpiresAtClampViewTests(_Base):
    def setUp(self):
        self.user = make_user("f2_clamp_user")
        self.client.force_authenticate(self.user)

    def _expires_after_create(self, delta):
        sent = (timezone.now() + delta).isoformat()
        res = self._create(expires_at=sent)
        self.assertEqual(res.status_code, 201, res.data)
        return Story.objects.get(id=res.data["id"]).expires_at - timezone.now()

    def test_client_cannot_make_a_story_live_for_ever(self):
        left = self._expires_after_create(timedelta(days=3650))
        self.assertLessEqual(left, timedelta(hours=24))
        self.assertGreater(left, timedelta(hours=23, minutes=50))

    def test_client_cannot_make_a_story_end_at_once(self):
        left = self._expires_after_create(timedelta(seconds=1))
        self.assertGreaterEqual(left, timedelta(minutes=29))
        self.assertLessEqual(left, timedelta(minutes=30))

    def test_a_past_expires_at_is_lifted_to_the_minimum(self):
        left = self._expires_after_create(-timedelta(days=2))
        self.assertGreaterEqual(left, timedelta(minutes=29))

    def test_a_shorter_story_inside_the_window_is_kept(self):
        left = self._expires_after_create(timedelta(hours=3))
        self.assertGreater(left, timedelta(hours=2, minutes=55))
        self.assertLessEqual(left, timedelta(hours=3))

    def test_no_expires_at_uses_the_24h_default(self):
        res = self._create()
        self.assertEqual(res.status_code, 201, res.data)
        left = Story.objects.get(id=res.data["id"]).expires_at - timezone.now()
        self.assertGreater(left, timedelta(hours=23, minutes=55))
        self.assertLessEqual(left, timedelta(hours=24))


# ---------------------------------------------------------------------------------------------------------------
# Limits endpoint
# ---------------------------------------------------------------------------------------------------------------
@override_settings(STORY_MAX_ACTIVE_PER_USER=5)
class StoryLimitsEndpointTests(_Base):
    def setUp(self):
        self.user = make_user("f2_limits_user")

    def test_requires_login(self):
        res = self.client.get(reverse("story-limits"))
        self.assertIn(res.status_code, (401, 403))

    def test_reports_quota_and_lifetime(self):
        make_story(self.user)
        make_story(self.user)
        ended = make_story(self.user)
        Story.objects.filter(id=ended.id).update(expires_at=timezone.now() - timedelta(minutes=5))  # does not count
        self.client.force_authenticate(self.user)
        res = self.client.get(reverse("story-limits"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["max_active"], 5)
        self.assertEqual(res.data["active"], 2)
        self.assertEqual(res.data["remaining"], 3)
        self.assertEqual(res.data["max_lifetime_hours"], 24)
        self.assertEqual(res.data["min_lifetime_minutes"], 30)

    def test_remaining_never_goes_below_zero(self):
        for _ in range(7):
            make_story(self.user)
        self.client.force_authenticate(self.user)
        res = self.client.get(reverse("story-limits"))
        self.assertEqual(res.data["remaining"], 0)
        self.assertEqual(res.data["active"], 7)


# ---------------------------------------------------------------------------------------------------------------
# Expiry sweep (+ beat wiring)
# ---------------------------------------------------------------------------------------------------------------
class ExpireOldStoriesSweepTests(APITestCase):
    def setUp(self):
        self.user = make_user("f2_sweep_user")

    def _ended(self, minutes_ago=5):
        s = make_story(self.user)
        Story.objects.filter(id=s.id).update(expires_at=timezone.now() - timedelta(minutes=minutes_ago))
        return s

    def test_sweeps_only_ended_stories_and_returns_the_count(self):
        from .tasks import expire_old_stories

        a, b = self._ended(), self._ended(120)
        live = make_story(self.user)
        self.assertEqual(expire_old_stories(), 2)
        for s in (a, b):
            s.refresh_from_db()
            self.assertTrue(s.is_deleted)
            self.assertIsNotNone(s.deleted_at)
        live.refresh_from_db()
        self.assertFalse(live.is_deleted)
        self.assertIsNone(live.deleted_at)

    def test_deleted_at_is_not_before_expires_at(self):
        """post/highlights.py: `deleted_at >= expires_at` = ended by itself (archive-able)."""
        from .tasks import expire_old_stories

        s = self._ended()
        expire_old_stories()
        s.refresh_from_db()
        self.assertGreaterEqual(s.deleted_at, s.expires_at)

    def test_already_deleted_stories_are_left_alone(self):
        from .tasks import expire_old_stories

        s = self._ended()
        early = timezone.now() - timedelta(hours=1)
        Story.objects.filter(id=s.id).update(is_deleted=True, deleted_at=early)
        self.assertEqual(expire_old_stories(), 0)
        s.refresh_from_db()
        self.assertEqual(s.deleted_at, early)  # not overwritten

    def test_second_run_is_a_no_op(self):
        from .tasks import expire_old_stories

        self._ended()
        self.assertEqual(expire_old_stories(), 1)
        self.assertEqual(expire_old_stories(), 0)

    def test_nothing_to_do(self):
        from .tasks import expire_old_stories

        make_story(self.user)
        self.assertEqual(expire_old_stories(), 0)

    def test_many_rows_are_swept_in_batches(self):
        from . import tasks

        for _ in range(5):
            self._ended()
        live = make_story(self.user)
        with mock.patch.object(tasks, "STORY_EXPIRE_BATCH_SIZE", 2):
            self.assertEqual(tasks.expire_old_stories(), 5)
        self.assertEqual(Story.objects.filter(user=self.user, is_deleted=True).count(), 5)
        live.refresh_from_db()
        self.assertFalse(live.is_deleted)

    def test_batch_cap_leaves_the_rest_for_the_next_run(self):
        from . import tasks

        for _ in range(5):
            self._ended()
        with mock.patch.object(tasks, "STORY_EXPIRE_BATCH_SIZE", 2), \
                mock.patch.object(tasks, "STORY_EXPIRE_MAX_BATCHES", 1):
            self.assertEqual(tasks.expire_old_stories(), 2)
        self.assertEqual(tasks.expire_old_stories(), 3)  # the next beat tick carries on

    def test_swept_story_leaves_the_viewer_list_but_stays_in_the_owner_archive(self):
        from .tasks import expire_old_stories

        s = self._ended()
        expire_old_stories()
        self.client.force_authenticate(self.user)
        ids = [str(r["id"]) for r in _rows(self.client.get(reverse("story-archive")))]
        self.assertIn(str(s.id), ids)

    def test_hard_delete_purges_only_rows_soft_deleted_over_30_days_ago(self):
        from .tasks import hard_delete_ancient_stories

        old = make_story(self.user)
        Story.objects.filter(id=old.id).update(is_deleted=True, deleted_at=timezone.now() - timedelta(days=45))
        recent = make_story(self.user)
        Story.objects.filter(id=recent.id).update(is_deleted=True, deleted_at=timezone.now() - timedelta(days=2))
        self.assertEqual(hard_delete_ancient_stories(), 1)
        self.assertFalse(Story.objects.filter(id=old.id).exists())
        self.assertTrue(Story.objects.filter(id=recent.id).exists())


class StoryBeatScheduleTests(SimpleTestCase):
    def _entry_for(self, task_name):
        rows = [v for v in settings.CELERY_BEAT_SCHEDULE.values() if v.get("task") == task_name]
        self.assertEqual(len(rows), 1, f"{task_name} must be registered exactly once in CELERY_BEAT_SCHEDULE")
        return rows[0]

    def test_expire_job_is_scheduled_and_the_name_matches_the_registered_task(self):
        from .tasks import expire_old_stories

        self.assertEqual(expire_old_stories.name, "post.tasks.expire_old_stories")
        self._entry_for(expire_old_stories.name)

    def test_purge_job_is_scheduled_and_the_name_matches_the_registered_task(self):
        from .tasks import hard_delete_ancient_stories

        self.assertEqual(hard_delete_ancient_stories.name, "post.tasks.hard_delete_ancient_stories")
        self._entry_for(hard_delete_ancient_stories.name)

    def test_expire_runs_often_and_purge_runs_rarely(self):
        expire = self._entry_for("post.tasks.expire_old_stories")["schedule"]
        purge = self._entry_for("post.tasks.hard_delete_ancient_stories")["schedule"]
        # crontab objects: expire fires every N minutes (several minutes per hour), purge only once a week
        self.assertGreater(len(expire.minute), 1)
        self.assertEqual(len(purge.day_of_week), 1)
        self.assertEqual(len(purge.hour), 1)


def _rows(res):
    return res.data["results"] if isinstance(res.data, dict) and "results" in res.data else res.data


# ---------------------------------------------------------------------------------------------------------------
# Live endpoint order (live_map_for is mocked - the tuitionclass fixtures are not what is under test here)
# ---------------------------------------------------------------------------------------------------------------
class StoryLiveOrderTests(APITestCase):
    def setUp(self):
        self.viewer = make_user("f2_live_viewer")
        self.t_join_old = make_user("f2_live_join_old")
        self.t_join_new = make_user("f2_live_join_new")
        self.t_follow_old = make_user("f2_live_follow_old")
        self.t_follow_new = make_user("f2_live_follow_new")
        self.now = timezone.now()
        self.client.force_authenticate(self.viewer)

    def _live(self, minutes_ago, can_join, session_id):
        started = (self.now - timedelta(minutes=minutes_ago)).isoformat() if minutes_ago is not None else None
        return {
            "session_id": session_id, "classroom_id": session_id, "title": f"Class {session_id}",
            "started_at": started, "viewer_count": 0, "can_join": can_join,
        }

    def _get(self, live_map):
        with mock.patch("post.story_live.live_map_for", return_value=live_map):
            res = self.client.get(reverse("story-live"))
        self.assertEqual(res.status_code, 200)
        return [row["user"]["username"] for row in res.data["results"]]

    def test_joinable_first_then_the_rest_newest_start_first_in_each_group(self):
        live_map = {
            self.t_follow_new.id: self._live(1, False, 1),     # follower-only, newest overall
            self.t_join_old.id: self._live(50, True, 2),       # member, oldest
            self.t_follow_old.id: self._live(40, False, 3),    # follower-only, older
            self.t_join_new.id: self._live(10, True, 4),       # member, newer
        }
        self.assertEqual(
            self._get(live_map),
            [self.t_join_new.username, self.t_join_old.username, self.t_follow_new.username, self.t_follow_old.username],
        )

    def test_unknown_start_time_goes_last_inside_its_group(self):
        live_map = {
            self.t_join_old.id: self._live(None, True, 1),
            self.t_join_new.id: self._live(5, True, 2),
        }
        self.assertEqual(self._get(live_map), [self.t_join_new.username, self.t_join_old.username])

    def test_inactive_teacher_is_left_out(self):
        self.t_join_old.is_active = False
        self.t_join_old.save(update_fields=["is_active"])
        live_map = {
            self.t_join_old.id: self._live(5, True, 1),
            self.t_follow_old.id: self._live(9, False, 2),
        }
        self.assertEqual(self._get(live_map), [self.t_follow_old.username])

    def test_nobody_live_is_an_empty_list(self):
        self.assertEqual(self._get({}), [])

    def test_requires_login(self):
        self.client.force_authenticate(None)
        res = self.client.get(reverse("story-live"))
        self.assertIn(res.status_code, (401, 403))
