"""
P-F8 spaced repetition tests - written, NOT RUN (read-only task). Run:

    python manage.py test spaced_repetition core.tests_habit_loop

Every service takes an explicit `now`, so nothing here sleeps or freezes the clock. IST is used because a card's "N days"
means local midnight of the user's timezone (NotificationPreference.timezone).
"""
import uuid
from datetime import date, datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Notification, NotificationPreference
from login.models import User
from post.models import Post, PostSave
from post.study_tools import REVISION_COLLECTION

from . import services, sm2
from .models import RevisionCard, RevisionReview

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 11, 4)


def at(hour, minute=0, day=DAY):
    return datetime.combine(day, time(hour, minute), tzinfo=IST)


def _user(prefix="srs"):
    u = User.objects.create_user(username=f"{prefix}{uuid.uuid4().hex[:8]}", password="Str0ng-pass-123!",
                                 email=f"{prefix}{uuid.uuid4().hex[:6]}@example.com")
    NotificationPreference.objects.create(user=u, timezone="Asia/Kolkata")
    return u


def _post(author=None):
    author = author or User.objects.get_or_create(username="srs_author", defaults={"email": "srs_author@example.com"})[0]
    return Post.objects.create(user=author, title="Newton's laws", content="F = ma", visibility="public",
                               moderation_status="approved")


def _deck_item(user, age_days=5, now=None, collection=REVISION_COLLECTION):
    now = now or at(12)
    post = _post()
    save = PostSave.objects.create(user=user, post=post, collection_name=collection)
    PostSave.objects.filter(pk=save.pk).update(created_at=now - timedelta(days=age_days))
    return post


# ---------------------------------------------------------------------------------------------------- pure SM-2
class Sm2Tests(SimpleTestCase):
    def run_grades(self, grades, state=None):
        state, out = state or sm2.CardState(), []
        for g in grades:
            o = sm2.next_state(state, g)
            state = o.state
            out.append(state.interval_days)
        return state, out

    def test_good_good_good_gives_1_6_then_ease_multiplied(self):
        state, intervals = self.run_grades(["good", "good", "good"])
        self.assertEqual(intervals, [1, 6, 15])  # round(6 * 2.5)
        self.assertEqual(state.repetitions, 3)
        self.assertAlmostEqual(state.ease, 2.5)

    def test_easy_on_a_new_card_starts_at_three_days_and_raises_ease(self):
        o = sm2.next_state(sm2.CardState(), "easy")
        self.assertEqual(o.state.interval_days, 3)
        self.assertAlmostEqual(o.state.ease, 2.6)
        self.assertFalse(o.relearn)

    def test_hard_lowers_ease_but_still_moves_forward(self):
        o = sm2.next_state(sm2.CardState(), "hard")
        self.assertEqual(o.state.interval_days, 1)
        self.assertAlmostEqual(o.state.ease, 2.36)

    def test_again_resets_repetitions_counts_a_lapse_and_asks_for_relearn(self):
        state, _ = self.run_grades(["good", "good", "good"])
        o = sm2.next_state(state, "again")
        self.assertTrue(o.relearn)
        self.assertEqual((o.state.repetitions, o.state.interval_days, o.state.lapses), (0, 1, 1))
        self.assertAlmostEqual(o.state.ease, 2.3)

    def test_ease_never_drops_below_the_floor(self):
        state = sm2.CardState()
        for _ in range(30):
            state = sm2.next_state(state, "again").state
        self.assertGreaterEqual(state.ease, sm2.MIN_EASE)
        self.assertAlmostEqual(state.ease, sm2.MIN_EASE)

    def test_interval_is_capped(self):
        state = sm2.CardState(ease=2.5, interval_days=300, repetitions=5)
        self.assertEqual(sm2.next_state(state, "good", max_interval=365).state.interval_days, 365)
        self.assertEqual(sm2.next_state(state, "good", max_interval=100).state.interval_days, 100)

    def test_interval_always_grows_after_two_successes(self):
        state = sm2.CardState(ease=1.3, interval_days=2, repetitions=2)
        self.assertGreater(sm2.next_state(state, "good").state.interval_days, 2)

    def test_preview_labels_every_button(self):
        p = sm2.preview(sm2.CardState())
        self.assertEqual(p["again"], {"minutes": 10})
        self.assertEqual(p["good"], {"days": 1})
        self.assertEqual(p["easy"], {"days": 3})

    def test_unknown_grade(self):
        self.assertFalse(sm2.is_valid_grade("perfect"))
        with self.assertRaises(KeyError):
            sm2.next_state(sm2.CardState(), "perfect")


# ---------------------------------------------------------------------------------------------------- services
class ReviewServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = _user()

    def test_first_review_creates_the_card_and_schedules_local_midnight(self):
        post = _deck_item(self.u)
        res = services.submit_review(self.u, post.id, "good", at(15, 30))
        card = RevisionCard.objects.get(user=self.u, post=post)
        self.assertEqual((card.interval_days, card.repetitions), (1, 1))
        # 1 day = tomorrow 00:00 IST, not "now + 24h"
        self.assertEqual(card.next_review_at, at(0, 0, DAY + timedelta(days=1)))
        self.assertEqual(res["card"]["interval_days"], 1)
        log = RevisionReview.objects.get(card=card)
        self.assertTrue(log.was_new)
        self.assertEqual((log.grade, log.interval_before, log.interval_after), ("good", 0, 1))

    def test_grading_twice_is_a_409_not_due(self):
        post = _deck_item(self.u)
        services.submit_review(self.u, post.id, "good", at(10))
        with self.assertRaises(services.SrsError) as ctx:
            services.submit_review(self.u, post.id, "good", at(10, 1))
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("not_due", 409))
        self.assertEqual(RevisionReview.objects.filter(user=self.u).count(), 1)

    def test_again_comes_back_in_minutes_and_can_be_graded_again_at_once(self):
        post = _deck_item(self.u)
        services.submit_review(self.u, post.id, "again", at(10))
        card = RevisionCard.objects.get(user=self.u, post=post)
        self.assertTrue(card.relearning)
        self.assertEqual(card.next_review_at, at(10) + timedelta(minutes=10))
        self.assertEqual(card.lapses, 1)
        services.submit_review(self.u, post.id, "good", at(10, 2))  # same session, no 409
        card.refresh_from_db()
        self.assertFalse(card.relearning)
        self.assertEqual((card.repetitions, card.interval_days), (1, 1))
        self.assertEqual(card.next_review_at, at(0, 0, DAY + timedelta(days=1)))

    def test_review_when_due_advances_the_schedule(self):
        post = _deck_item(self.u)
        services.submit_review(self.u, post.id, "good", at(10))
        services.submit_review(self.u, post.id, "good", at(9, 0, DAY + timedelta(days=1)))
        card = RevisionCard.objects.get(user=self.u, post=post)
        self.assertEqual((card.repetitions, card.interval_days), (2, 6))
        self.assertEqual(card.next_review_at, at(0, 0, DAY + timedelta(days=1) + timedelta(days=6)))

    def test_not_in_deck_is_404(self):
        post = _post()
        PostSave.objects.create(user=self.u, post=post, collection_name="default")
        with self.assertRaises(services.SrsError) as ctx:
            services.submit_review(self.u, post.id, "good", at(10))
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("not_in_deck", 404))

    def test_invalid_grade(self):
        post = _deck_item(self.u)
        with self.assertRaises(services.SrsError) as ctx:
            services.submit_review(self.u, post.id, "perfect", at(10))
        self.assertEqual(ctx.exception.code, "invalid_grade")
        self.assertFalse(RevisionCard.objects.exists())

    @override_settings(SRS_NEW_PER_DAY=2)
    def test_new_cards_per_day_cap(self):
        posts = [_deck_item(self.u) for _ in range(3)]
        services.submit_review(self.u, posts[0].id, "good", at(10))
        services.submit_review(self.u, posts[1].id, "good", at(10, 5))
        with self.assertRaises(services.SrsError) as ctx:
            services.submit_review(self.u, posts[2].id, "good", at(10, 10))
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("new_limit", 409))
        # next local day the allowance is back
        services.submit_review(self.u, posts[2].id, "good", at(9, 0, DAY + timedelta(days=1)))

    def test_another_users_deck_does_not_leak(self):
        other = _user("other")
        post = _deck_item(other)
        with self.assertRaises(services.SrsError):
            services.submit_review(self.u, post.id, "good", at(10))


class QueueAndCountsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = _user()

    def test_queue_has_due_cards_first_then_new(self):
        a, b, c = _deck_item(self.u), _deck_item(self.u), _deck_item(self.u)
        services.submit_review(self.u, a.id, "good", at(10))
        services.submit_review(self.u, b.id, "easy", at(10, 1))
        later = at(9, 0, DAY + timedelta(days=1))
        q = services.build_queue(self.u, later)
        self.assertEqual([x.post_id for x in q["due"]], [a.id])        # b (easy = 3 days) is not due yet
        self.assertEqual([x.post_id for x in q["new"]], [c.id])
        counts = services.counts(self.u, later)
        self.assertEqual((counts["due"], counts["new"], counts["total_in_deck"]), (1, 1, 3))

    def test_taking_a_post_out_of_the_deck_pauses_its_card(self):
        a = _deck_item(self.u)
        services.submit_review(self.u, a.id, "good", at(10))
        later = at(9, 0, DAY + timedelta(days=1))
        self.assertEqual(services.counts(self.u, later)["due"], 1)
        PostSave.objects.filter(user=self.u, post=a).update(collection_name="default")
        self.assertEqual(services.counts(self.u, later)["due"], 0)
        PostSave.objects.filter(user=self.u, post=a).update(collection_name=REVISION_COLLECTION)
        self.assertEqual(services.counts(self.u, later)["due"], 1)       # state kept, nothing lost

    def test_deleted_post_is_not_in_the_queue(self):
        a = _deck_item(self.u)
        Post.objects.filter(pk=a.pk).update(is_deleted=True)
        self.assertEqual(services.build_queue(self.u, at(12))["new"], [])

    @override_settings(SRS_MAX_QUEUE=2)
    def test_queue_is_capped(self):
        for _ in range(5):
            _deck_item(self.u)
        q = services.build_queue(self.u, at(12), limit=50)
        self.assertEqual(len(q["due"]) + len(q["new"]), 2)

    @override_settings(SRS_NEW_MIN_AGE_HOURS=24)
    def test_fresh_item_waits_for_the_minimum_age(self):
        _deck_item(self.u, age_days=0, now=at(12))
        self.assertEqual(services.build_queue(self.u, at(13))["new"], [])
        self.assertEqual(len(services.build_queue(self.u, at(12) + timedelta(days=1, minutes=1))["new"]), 1)


class StatsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = _user()

    def test_retention_needs_enough_reviews(self):
        a = _deck_item(self.u)
        services.submit_review(self.u, a.id, "good", at(10))
        self.assertIsNone(services.stats(self.u, at(12))["retention_pct"])

    def test_retention_and_per_day(self):
        a = _deck_item(self.u)
        card = RevisionCard.objects.create(user=self.u, post=a, next_review_at=at(8), interval_days=6, repetitions=2)
        grades = ["good", "good", "good", "again", "easy", "good"]   # 5 of 6 kept
        for i, g in enumerate(grades):
            RevisionReview.objects.create(card=card, user=self.u, grade=g, reviewed_at=at(9, i), was_new=False)
        s = services.stats(self.u, at(12))
        self.assertEqual(s["retention_pct"], 83.3)
        self.assertEqual(s["per_day"][-1], {"date": DAY.isoformat(), "reviews": 6})
        self.assertEqual(len(s["per_day"]), 7)
        self.assertEqual(s["review_streak_days"], 1)

    def test_mature_and_learning_split(self):
        a, b = _deck_item(self.u), _deck_item(self.u)
        RevisionCard.objects.create(user=self.u, post=a, next_review_at=at(8) + timedelta(days=30), interval_days=30)
        RevisionCard.objects.create(user=self.u, post=b, next_review_at=at(8) + timedelta(days=2), interval_days=2)
        s = services.stats(self.u, at(12))
        self.assertEqual((s["mature"], s["learning"]), (1, 1))
        self.assertIsNotNone(s["next_due_at"])


class ReminderCountsTests(TestCase):
    def test_due_plus_capped_new(self):
        u = _user()
        posts = [_deck_item(u, age_days=5) for _ in range(4)]
        RevisionCard.objects.create(user=u, post=posts[0], next_review_at=at(8), interval_days=1, repetitions=1)
        RevisionCard.objects.create(user=u, post=posts[1], next_review_at=at(8) + timedelta(days=3), interval_days=3)
        got = services.reminder_counts([u.pk], at(12), min_new_age_days=2)[u.pk]
        self.assertEqual(got, {"due": 1, "new": 2, "total": 3})   # posts[2], posts[3] are new

    @override_settings(SRS_NEW_PER_DAY=1)
    def test_new_is_capped_per_day(self):
        u = _user()
        for _ in range(5):
            _deck_item(u, age_days=5)
        self.assertEqual(services.reminder_counts([u.pk], at(12), 2)[u.pk]["new"], 1)

    def test_young_items_are_not_new_for_reminders(self):
        u = _user()
        _deck_item(u, age_days=0, now=at(12))
        self.assertEqual(services.reminder_counts([u.pk], at(13), 2), {})


# ---------------------------------------------------------------------------------------------------- API
class ApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = _user()
        self.client = APIClient()
        self.client.force_authenticate(self.u)

    def test_login_required(self):
        anon = APIClient()
        for path in ("/revision/due/", "/revision/stats/", "/revision/config/"):
            self.assertIn(anon.get(path).status_code, (401, 403))
        self.assertIn(anon.post("/revision/review/", {}, format="json").status_code, (401, 403))

    def test_due_returns_new_cards_with_previews(self):
        post = _deck_item(self.u, now=timezone.now())
        r = self.client.get("/revision/due/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["cards"]), 1)
        card = body["cards"][0]
        self.assertEqual(card["post_id"], str(post.id))
        self.assertTrue(card["is_new"])
        self.assertEqual(card["preview"]["again"], {"minutes": 10})
        self.assertEqual(body["counts"]["total_in_deck"], 1)

    def test_review_then_duplicate_then_bad_input(self):
        post = _deck_item(self.u, now=timezone.now())
        ok = self.client.post("/revision/review/", {"post_id": str(post.id), "grade": "good", "duration_ms": 4200}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["success"])
        self.assertEqual(ok.json()["card"]["interval_days"], 1)
        dup = self.client.post("/revision/review/", {"post_id": str(post.id), "grade": "good"}, format="json")
        self.assertEqual((dup.status_code, dup.json()["code"]), (409, "not_due"))
        bad = self.client.post("/revision/review/", {"post_id": str(post.id), "grade": "meh"}, format="json")
        self.assertEqual((bad.status_code, bad.json()["code"]), (400, "invalid_grade"))
        missing = self.client.post("/revision/review/", {"grade": "good"}, format="json")
        self.assertEqual((missing.status_code, missing.json()["code"]), (400, "invalid_post_id"))
        nope = self.client.post("/revision/review/", {"post_id": str(uuid.uuid4()), "grade": "good"}, format="json")
        self.assertEqual((nope.status_code, nope.json()["code"]), (404, "not_in_deck"))

    def test_private_post_of_someone_else_is_not_served(self):
        author = User.objects.create_user(username=f"pa{uuid.uuid4().hex[:6]}", password="x", email="pa@example.com")
        post = Post.objects.create(user=author, content="secret", visibility="private", moderation_status="approved")
        PostSave.objects.create(user=self.u, post=post, collection_name=REVISION_COLLECTION)
        self.assertEqual(self.client.get("/revision/due/").json()["cards"], [])

    def test_stats_endpoint(self):
        r = self.client.get("/revision/stats/?days=7")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["per_day"]), 7)
        self.assertIn("retention_pct", body)

    @override_settings(SRS_ENABLED=False)
    def test_kill_switch(self):
        for r in (self.client.get("/revision/due/"), self.client.get("/revision/stats/"),
                  self.client.post("/revision/review/", {"post_id": str(uuid.uuid4()), "grade": "good"}, format="json")):
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.json()["code"], "feature_disabled")


# ---------------------------------------------------------------------------------------------------- habit loop
NT = Notification.NotifType.REVISION_DECK_REMINDER


class HabitLoopSrsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = _user("hl")
        self.posts = [_deck_item(self.u, age_days=5) for _ in range(4)]
        for p in self.posts[:3]:
            RevisionCard.objects.create(user=self.u, post=p, next_review_at=at(8), interval_days=1, repetitions=1)

    def run_at(self, when):
        from core import habit_loop as hl

        with mock.patch("core.content_digest.push_pending_row", return_value="sent"):
            return hl.run_revision_reminders(when)

    def test_srs_user_gets_the_due_cards_message(self):
        from core import habit_loop as hl

        self.assertEqual(self.run_at(at(18, 10))["created"], 1)
        row = Notification.objects.get(recipient=self.u, notif_type=NT)
        self.assertTrue(row.data["srs"])
        self.assertEqual((row.data["srs_due"], row.data["srs_new"], row.data["deck_size"]), (3, 1, 4))
        self.assertEqual((row.title, row.message), hl.compose_srs_due(3, 1, "en"))

    def test_srs_user_is_nudged_daily_not_every_three_days(self):
        self.run_at(at(18, 10))
        self.assertEqual(self.run_at(at(18, 30, DAY + timedelta(days=1)))["created"], 1)
        self.assertEqual(Notification.objects.filter(recipient=self.u, notif_type=NT).count(), 2)

    def test_nothing_due_means_no_nudge(self):
        RevisionCard.objects.filter(user=self.u).update(next_review_at=at(8) + timedelta(days=9))
        # the 4th deck item is still "new" (1 < srs_min_due 3) so nothing is sent
        self.assertEqual(self.run_at(at(18, 10))["created"], 0)

    def test_snapshot_reports_srs_counts(self):
        from core import habit_loop as hl

        snap = hl.today_snapshot(self.u, at(10))
        self.assertTrue(snap["revision"]["srs"])
        self.assertEqual((snap["revision"]["due"], snap["revision"]["new"]), (4, 1))

    @override_settings(SRS_ENABLED=False)
    def test_with_srs_off_the_old_age_rule_is_used(self):
        from core import habit_loop as hl

        self.assertFalse(hl.revision_stats([self.u.pk], at(12))[self.u.pk]["srs"])
