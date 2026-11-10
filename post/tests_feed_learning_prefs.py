"""
S8 - Learning preferences -> feed. NOT RUN by the author (read-only task):
    python manage.py test post.tests_feed_learning_prefs

content_level / content_languages add +POINTS_PREF on a hashtag match (boost only, nothing is hidden);
a focus-schedule window turns Exam Mode's study-only filter on while it runs.
"""
import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from core.models import NotificationPreference
from user_profile.models import UserPreference

from . import feed_context, feed_exam
from .models import Post

User = get_user_model()
IST = ZoneInfo("Asia/Kolkata")


def _post(user, content, **kw):
    kw.setdefault("category", "entertainment")
    return Post.objects.create(user=user, content=content, post_type="text", visibility="public",
                               moderation_status="approved", **kw)


class PureTests(SimpleTestCase):
    def test_preference_tags_from_level_and_languages(self):
        p = feed_exam.ExamProfile(content_level="beginner", languages=("hi",))
        tags = feed_exam.preference_tags(p)
        self.assertIn("basics", tags)
        self.assertIn("hindi", tags)
        self.assertEqual(feed_exam.preference_tags(feed_exam.EMPTY), [])

    def test_level_or_language_alone_counts_as_a_profile(self):
        self.assertTrue(feed_exam.has_profile(feed_exam.ExamProfile(content_level="advanced")))
        self.assertTrue(feed_exam.has_profile(feed_exam.ExamProfile(languages=("en",))))
        self.assertFalse(feed_exam.has_profile(feed_exam.EMPTY))

    def test_old_style_construction_still_works(self):
        p = feed_exam.ExamProfile("jee", "12", ("physics",), True)
        self.assertEqual((p.content_level, p.languages), ("", ()))


class LoadProfileTests(TestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")

    def test_level_and_languages_are_loaded_and_lower_cased(self):
        UserPreference.objects.create(user=self.me, content_level="intermediate", content_languages=["HI", "en"])
        p = feed_exam.load_profile(self.me)
        self.assertEqual(p.content_level, "intermediate")
        self.assertEqual(p.languages, ("hi", "en"))
        self.assertFalse(p.exam_mode)

    def test_focus_schedule_window_means_exam_mode_while_it_runs(self):
        UserPreference.objects.create(
            user=self.me, focus_schedule_enabled=True,
            focus_schedule=[{"days": [], "start": "09:00", "end": "11:00"}],
        )
        NotificationPreference.objects.create(user=self.me, timezone="Asia/Kolkata")
        inside = datetime.datetime(2026, 10, 26, 10, 0, tzinfo=IST)
        outside = datetime.datetime(2026, 10, 26, 15, 0, tzinfo=IST)
        with mock.patch("django.utils.timezone.now", return_value=inside):
            self.assertTrue(feed_exam.load_profile(self.me).exam_mode)
        with mock.patch("django.utils.timezone.now", return_value=outside):
            self.assertFalse(feed_exam.load_profile(self.me).exam_mode)

    def test_schedule_switched_off_means_no_window_effect(self):
        UserPreference.objects.create(
            user=self.me, focus_schedule_enabled=False,
            focus_schedule=[{"days": [], "start": "00:00", "end": "23:59"}],
        )
        self.assertFalse(feed_exam.load_profile(self.me).exam_mode)

    def test_a_broken_schedule_never_breaks_the_feed(self):
        UserPreference.objects.create(user=self.me, focus_schedule_enabled=True, focus_schedule=[{"junk": 1}, "x", None])
        self.assertFalse(feed_exam.load_profile(self.me).exam_mode)

    def test_context_carries_pref_tags(self):
        UserPreference.objects.create(user=self.me, content_level="advanced", content_languages=["en"])
        with mock.patch.object(feed_context, "load_weak_topics", return_value=()):
            ctx = feed_context.load_context(self.me)
        self.assertIn("olympiad", ctx.pref_tags)
        self.assertIn("english", ctx.pref_tags)
        self.assertTrue(ctx.is_learner)


class BoostTests(TestCase):
    def setUp(self):
        cache.clear()
        self.other = User.objects.create_user(username="author", password="x")
        self.p_basics = _post(self.other, "basics", hashtags=["basics"])
        self.p_hindi = _post(self.other, "hindi", hashtags=["hindi"])
        self.p_plain = _post(self.other, "plain")

    def _points(self, **kw):
        base = dict(subjects=[], is_learner=False, campus_authors=set(), class_authors=set())
        base.update(kw)
        expr = feed_context.context_expression(feed_context.ContextSignals(**base), 12)
        return {p.content: p.pts for p in Post.objects.annotate(pts=expr)}

    def test_level_and_language_tags_boost_matching_posts_only(self):
        pts = self._points(pref_tags=("basics", "hindi"))
        self.assertEqual(pts["basics"], feed_context.POINTS_PREF)
        self.assertEqual(pts["hindi"], feed_context.POINTS_PREF)
        self.assertEqual(pts["plain"], 0.0)

    def test_it_is_a_boost_not_a_filter(self):
        self._points(pref_tags=("basics",))
        self.assertEqual(Post.objects.count(), 3)  # nothing hidden

    def test_no_pref_tags_no_extra_expression(self):
        self.assertIsNone(feed_context.context_expression(
            feed_context.ContextSignals([], False, set(), set()), 12,
        ))
