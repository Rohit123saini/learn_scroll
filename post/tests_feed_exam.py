# post/tests_feed_exam.py
"""
Exam profile -> feed: exam / class hashtag boost, weak-topic boost, focus
subjects, and Exam Mode (study-only filter on every pool).
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import FloatField
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from user_profile.models import UserPreference

from . import feed_context, feed_exam, feed_mix
from .models import Post

User = get_user_model()


def _post(user, content, **kw):
    kw.setdefault("category", "entertainment")
    return Post.objects.create(user=user, content=content, post_type="text", visibility="public",
                               moderation_status="approved", **kw)


def _ctx(**kw):
    base = dict(subjects=[], is_learner=False, campus_authors=set(), class_authors=set())
    base.update(kw)
    return feed_context.ContextSignals(**base)


# ---------------------------------------------------------------- pure
class PureTests(SimpleTestCase):
    def test_class_tags(self):
        self.assertEqual(feed_exam.class_tags("12"), ["class12", "class_12", "12th"])
        self.assertEqual(feed_exam.class_tags("dropper"), [])
        self.assertEqual(feed_exam.class_tags(""), [])

    def test_profile_tags_combine_exam_and_class(self):
        p = feed_exam.ExamProfile(exam_target="jee", class_level="11")
        tags = feed_exam.profile_tags(p)
        self.assertIn("jeemains", tags)
        self.assertIn("class11", tags)
        self.assertEqual(feed_exam.profile_tags(feed_exam.EMPTY), [])
        self.assertEqual(feed_exam.profile_tags(feed_exam.ExamProfile(exam_target="other")), [])

    def test_has_profile(self):
        self.assertFalse(feed_exam.has_profile(feed_exam.EMPTY))
        self.assertTrue(feed_exam.has_profile(feed_exam.ExamProfile(subjects=("Physics",))))

    def test_weak_topics_threshold_and_order(self):
        rows = (
            [("Kinematics", False)] * 3 + [("Kinematics", True)] * 1        # 25% of 4
            + [("Optics", False)] * 4                                       # 0% of 4
            + [("Algebra", True)] * 5                                       # 100% - strong
            + [("Thermo", False)] * 2                                       # too few answers
            + [("", False)] * 9                                             # blank topic ignored
            + [("Waves", False)] * 2 + [("Waves", True)] * 2                # exactly 50% - not weak
        )
        out = feed_context.weak_topics_from_rows(rows, min_answers=3, max_accuracy=0.5, cap=5)
        self.assertEqual(out, ["optics", "kinematics"])  # weakest first, lower-cased

    def test_weak_topics_cap_and_ties(self):
        rows = [("B", False)] * 3 + [("A", False)] * 3 + [("C", False)] * 5
        out = feed_context.weak_topics_from_rows(rows, 3, 0.5, 2)
        self.assertEqual(out, ["c", "a"])  # same accuracy -> more answers first, then name


# ---------------------------------------------------------------- DB
class ProfileLoadTests(TestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")

    def test_no_row_means_empty(self):
        self.assertEqual(feed_exam.load_profile(self.me), feed_exam.EMPTY)

    def test_loads_effective_exam_mode(self):
        pref = UserPreference.for_user(self.me)
        pref.exam_target, pref.class_level = "neet", "12"
        pref.focus_subjects = ["Biology", " "]
        pref.exam_mode = True
        pref.exam_date = timezone.localdate() - timedelta(days=1)   # exam nikal chuka
        pref.save()
        p = feed_exam.load_profile(self.me)
        self.assertEqual((p.exam_target, p.class_level, p.subjects), ("neet", "12", ("Biology",)))
        self.assertFalse(p.exam_mode)

    def test_load_context_picks_up_profile(self):
        pref = UserPreference.for_user(self.me)
        pref.exam_target, pref.focus_subjects = "jee", ["Physics"]
        pref.save()
        with mock.patch.object(feed_context, "load_weak_topics", return_value=("optics",)):
            ctx = feed_context.load_context(self.me)
        self.assertTrue(ctx.is_learner)
        self.assertIn("physics", ctx.subjects)
        self.assertIn("jeemains", ctx.exam_tags)
        self.assertEqual(ctx.weak_topics, ("optics",))

    def test_weak_topics_failure_does_not_break_context(self):
        with mock.patch.object(feed_context, "load_weak_topics", side_effect=RuntimeError("boom")):
            ctx = feed_context.load_context(self.me)
        self.assertEqual(ctx.weak_topics, ())


class BoostTests(TestCase):
    def setUp(self):
        cache.clear()
        self.other = User.objects.create_user(username="other", password="x")
        self.p_jee = _post(self.other, "jee", hashtags=["jeemains"])
        self.p_c12 = _post(self.other, "c12", hashtags=["class12"])
        self.p_optics = _post(self.other, "optics", subcategory="optics")
        self.p_optics_tag = _post(self.other, "optics_tag", hashtags=["optics"])
        self.p_plain = _post(self.other, "plain")

    def _points(self, ctx):
        expr = feed_context.context_expression(ctx, 12)
        return {p.content: p.pts for p in Post.objects.annotate(pts=expr)}

    def test_exam_and_class_tags_boost(self):
        pts = self._points(_ctx(exam_tags=("jeemains", "class12")))
        self.assertEqual(pts["jee"], feed_context.POINTS_EXAM)
        self.assertEqual(pts["c12"], feed_context.POINTS_EXAM)
        self.assertEqual(pts["plain"], 0.0)

    def test_weak_topic_boost_by_subcategory_and_hashtag(self):
        pts = self._points(_ctx(weak_topics=("optics",)))
        self.assertEqual(pts["optics"], feed_context.POINTS_WEAK)
        self.assertEqual(pts["optics_tag"], feed_context.POINTS_WEAK)
        self.assertEqual(pts["plain"], 0.0)

    def test_existing_four_arg_context_still_works(self):
        ctx = feed_context.ContextSignals(["physics"], True, set(), set())
        self.assertEqual((ctx.exam_tags, ctx.weak_topics), ((), ()))

    def test_no_exam_signals_no_extra_expression(self):
        self.assertIsNone(feed_context.context_expression(_ctx(), 12))


class ExamModeFilterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.teacher = User.objects.create_user(username="teacher", password="x")
        self.other = User.objects.create_user(username="other", password="x")
        self.p_edu = _post(self.other, "edu", category="education")
        self.p_sub = _post(self.other, "sub", subcategory="physics")
        self.p_exam_tag = _post(self.other, "examtag", hashtags=["neet"])
        self.p_teacher = _post(self.teacher, "teach")
        self.p_fun = _post(self.other, "fun")
        self.p_meme = _post(self.other, "meme", hashtags=["memes"])

    def _set(self, **fields):
        pref = UserPreference.for_user(self.me)
        for k, v in fields.items():
            setattr(pref, k, v)
        pref.save()

    def _contents(self, ctx=None):
        qs = feed_exam.apply_exam_mode(self.me, Post.objects.all(), ctx)
        return set(qs.values_list("content", flat=True))

    def test_mode_off_leaves_queryset_alone(self):
        self._set(exam_target="neet")
        self.assertEqual(len(self._contents()), 6)

    def test_on_keeps_only_study_content(self):
        self._set(exam_mode=True, exam_target="neet", focus_subjects=["Physics"])
        got = self._contents(_ctx(class_authors={self.teacher.pk}))
        self.assertEqual(got, {"edu", "sub", "examtag", "teach"})

    def test_on_without_any_profile_still_keeps_education(self):
        self._set(exam_mode=True)
        self.assertEqual(self._contents(), {"edu"})

    def test_weak_topics_and_ctx_subjects_are_kept(self):
        self._set(exam_mode=True)
        _post(self.other, "weakpost", hashtags=["optics"])
        got = self._contents(_ctx(subjects=["physics"], weak_topics=("optics",)))
        self.assertEqual(got, {"edu", "sub", "weakpost"})

    def test_expired_exam_date_turns_filter_off(self):
        self._set(exam_mode=True, exam_date=timezone.localdate() - timedelta(days=1))
        self.assertEqual(len(self._contents()), 6)

    def test_empty_result_is_not_backfilled(self):
        Post.objects.filter(category="education").delete()
        Post.objects.exclude(category="entertainment").delete()
        self._set(exam_mode=True)
        self.assertEqual(self._contents(), set())

    def test_no_user_returns_unchanged(self):
        qs = feed_exam.apply_exam_mode(None, Post.objects.all())
        self.assertEqual(qs.count(), 6)


class PoolIntegrationTests(TestCase):
    """build_pool_ids end to end: Exam Mode narrows EVERY pool."""

    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        self.followed = User.objects.create_user(username="followed", password="x")
        self.edu = _post(self.author, "edu", category="education")
        self.fun = _post(self.author, "fun")
        self.followed_fun = _post(self.followed, "ffun")
        self.followed_edu = _post(self.followed, "fedu", category="education")

    def _all_ids(self):
        pools = feed_mix.build_pool_ids(
            self.me, Post.objects.all(), {self.followed.pk}, lambda: (0, 0), seen_ids=set(),
        )
        return {i for ids in pools.values() for i in ids}

    def test_off_shows_everything(self):
        ids = self._all_ids()
        for p in (self.edu, self.fun, self.followed_fun, self.followed_edu):
            self.assertIn(p.id, ids)

    def test_on_drops_non_study_posts_from_every_pool_including_following(self):
        pref = UserPreference.for_user(self.me)
        pref.exam_mode = True
        pref.save()
        ids = self._all_ids()
        self.assertIn(self.edu.id, ids)
        self.assertIn(self.followed_edu.id, ids)
        self.assertNotIn(self.fun.id, ids)
        self.assertNotIn(self.followed_fun.id, ids)  # followed accounts bhi (explicit choice by the student)

    def test_exam_hashtag_lifts_a_post_in_recommended(self):
        pref = UserPreference.for_user(self.me)
        pref.exam_target = "jee"
        pref.save()
        tagged = _post(self.author, "tagged", hashtags=["jeemains"])
        pools = feed_mix.build_pool_ids(self.me, Post.objects.all(), set(), lambda: (0, 0), seen_ids=set())
        order = [i for i in pools["recommended"] + pools["trending"] if i in (tagged.id, self.fun.id)]
        self.assertEqual(order[0], tagged.id)


class WeakTopicsDbTests(TestCase):
    """load_weak_topics against real testseries rows (Question.topic + QuestionResponse.is_correct)."""

    def setUp(self):
        cache.clear()
        from testseries.models import TestSeries

        self.me = User.objects.create_user(username="me", password="x")
        self.creator = User.objects.create_user(username="creator", password="x")
        self.series = TestSeries.objects.create(
            source=TestSeries.Source.INDIVIDUAL, creator=self.creator, title="Mock",
            is_paid=False, price_coins=0, duration_minutes=30,
        )
        self.cfg = feed_context.get_config({"weak_cache_seconds": 0})
        self._order = 0

    def _answer(self, topic, correct, student=None):
        from testseries.models import Question, QuestionResponse, TestAttempt

        self._order += 1
        q = Question.objects.create(
            series=self.series, order=self._order, question_type="mcq", text=f"Q{self._order}", marks=4,
            negative_marks=1, topic=topic,
            options=[{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], correct_answer={"option_id": "a"},
        )
        attempt = TestAttempt.objects.create(
            series=self.series, student=student or self.me, attempt_number=self._order,
            started_at=timezone.now(),
        )
        QuestionResponse.objects.create(
            attempt=attempt, question=q, answer_data={}, is_auto_graded=True, is_correct=correct,
        )

    def test_finds_topics_the_student_keeps_missing(self):
        for _ in range(3):
            self._answer("Kinematics", False)
        self._answer("Kinematics", True)
        for _ in range(3):
            self._answer("Algebra", True)
        self.assertEqual(feed_context.load_weak_topics(self.me, self.cfg), ("kinematics",))

    def test_other_students_answers_are_ignored(self):
        other = User.objects.create_user(username="other", password="x")
        for _ in range(4):
            self._answer("Optics", False, student=other)
        self.assertEqual(feed_context.load_weak_topics(self.me, self.cfg), ())

    def test_ungraded_and_untopiced_answers_are_ignored(self):
        for _ in range(4):
            self._answer("", False)
        for _ in range(4):
            self._answer("Optics", None)
        self.assertEqual(feed_context.load_weak_topics(self.me, self.cfg), ())

    def test_result_is_cached_per_user(self):
        for _ in range(3):
            self._answer("Optics", False)
        cfg = feed_context.get_config({"weak_cache_seconds": 60})
        self.assertEqual(feed_context.load_weak_topics(self.me, cfg), ("optics",))
        for _ in range(5):
            self._answer("Waves", False)
        self.assertEqual(feed_context.load_weak_topics(self.me, cfg), ("optics",))  # cache hit
        cache.clear()
        self.assertEqual(set(feed_context.load_weak_topics(self.me, cfg)), {"optics", "waves"})


class ProfileReuseTests(TestCase):
    def test_apply_exam_mode_reuses_profile_carried_by_ctx(self):
        me = User.objects.create_user(username="me", password="x")
        ctx = _ctx(profile=feed_exam.ExamProfile(exam_mode=True))
        with self.assertNumQueries(0):  # queryset lazy hai + profile ctx se aaya => koi DB hit nahi
            qs = feed_exam.apply_exam_mode(me, Post.objects.all(), ctx)
        self.assertIsNotNone(qs)

    def test_load_context_stores_profile(self):
        me = User.objects.create_user(username="me", password="x")
        pref = UserPreference.for_user(me)
        pref.exam_mode = True
        pref.save()
        with mock.patch.object(feed_context, "load_weak_topics", return_value=()):
            ctx = feed_context.load_context(me)
        self.assertTrue(ctx.profile.exam_mode)
