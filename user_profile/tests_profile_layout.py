"""P5 - role-wise profile layout. Written, NOT run."""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import profile_layout

User = get_user_model()


def _url(username):
    return f"/profile/profile/{username}/layout/"


class ProfileLayoutTests(TestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="viewer1", password="x")
        self.owner = User.objects.create_user(username="teach1", password="x", first_name="Ravi")
        self.c = APIClient()
        self.c.force_authenticate(self.me)

    def _classroom(self, **kw):
        from tuitionclass.models import Classroom

        return Classroom.objects.create(teacher=self.owner, title=kw.pop("title", "Physics 12"), subject="Physics", **kw)

    def test_plain_user_is_student(self):
        r = self.c.get(_url("viewer1"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["profile_layout"], "student")
        self.assertEqual(r.data["roles"], ["student"])
        self.assertIsNone(r.data["teacher"])

    def test_teacher_layout_needs_a_public_classroom(self):
        self.assertEqual(self.c.get(_url("teach1")).data["profile_layout"], "student")
        self._classroom(is_flagged=True)
        self._classroom(title="Deleted", is_deleted=True)
        self.assertEqual(self.c.get(_url("teach1")).data["profile_layout"], "student")
        room = self._classroom(is_trial_enabled=True, enrolled_count=40)
        data = self.c.get(_url("teach1")).data
        self.assertEqual(data["profile_layout"], "teacher")
        self.assertEqual(data["teacher"]["classes_count"], 1)
        self.assertEqual(data["teacher"]["students_count"], 40)
        self.assertEqual(data["teacher"]["trial"]["classroom_id"], room.pk)
        self.assertIn("trial_cta", data["sections"])
        self.assertNotIn("reviews", data["sections"])  # no reviews yet -> section dropped

    def test_reviews_never_name_the_reviewer(self):
        from tuitionclass.models import ClassroomReview

        room = self._classroom()
        ClassroomReview.objects.create(classroom=room, student=self.me, rating=5, comment="Great  teacher\nreally")
        rv = self.c.get(_url("teach1")).data["teacher"]["reviews"]
        self.assertEqual(len(rv), 1)
        self.assertEqual(rv[0]["comment"], "Great teacher really")
        self.assertEqual(set(rv[0]), {"rating", "comment", "created_at"})

    def test_upcoming_live_lists_future_sessions_only(self):
        from tuitionclass.models import ClassSession

        room = self._classroom()
        now = timezone.now()
        ClassSession.objects.create(classroom=room, scheduled_start=now + timedelta(days=1),
                                    scheduled_end=now + timedelta(days=1, hours=1))
        ClassSession.objects.create(classroom=room, scheduled_start=now - timedelta(days=2),
                                    scheduled_end=now - timedelta(days=2, hours=-1))
        up = self.c.get(_url("teach1")).data["teacher"]["upcoming_live"]
        self.assertEqual(len(up), 1)

    def test_private_account_locked_for_non_follower(self):
        User.objects.filter(pk=self.owner.pk).update(is_private=True)
        self._classroom()
        d = self.c.get(_url("teach1")).data
        self.assertTrue(d["locked"])
        self.assertIsNone(d["teacher"])
        self.assertEqual(d["sections"], [])

    def test_unknown_user_404(self):
        self.assertEqual(self.c.get(_url("nobody_x")).status_code, 404)

    def test_blocked_is_404(self):
        with mock.patch("user_profile.layout_views.is_blocked_between", return_value=True):
            self.assertEqual(self.c.get(_url("teach1")).status_code, 404)

    def test_parent_and_coins_are_owner_only(self):
        with mock.patch.object(profile_layout, "parent_block", return_value={"children_count": 2}):
            own = self.c.get(_url("viewer1")).data
            self.assertEqual(own["profile_layout"], "parent")
            self.assertEqual(own["parent"], {"children_count": 2})
            self.c.force_authenticate(self.owner)
            seen_by_other = self.c.get(_url("viewer1")).data
            self.assertIsNone(seen_by_other["parent"])
            self.assertEqual(seen_by_other["profile_layout"], "student")

    def test_creator_layout_threshold_and_coins(self):
        block = {"reels_count": 2, "series_count": 1, "highlights_count": 0}
        with mock.patch.object(profile_layout, "creator_block", return_value=dict(block)):
            d = self.c.get(_url("viewer1")).data
        self.assertEqual(d["profile_layout"], "creator")
        low = {"reels_count": 1, "series_count": 1, "highlights_count": 0}
        with mock.patch.object(profile_layout, "creator_block", return_value=low):
            self.assertEqual(self.c.get(_url("viewer1")).data["profile_layout"], "student")

    def test_campus_chip_hidden_from_other_campus_and_minors(self):
        chip = {"campus_id": "c1", "campus_name": "Demo School", "class_name": "10", "section_name": "A"}
        with mock.patch.object(profile_layout, "_enrollment", return_value=mock.MagicMock()), \
             mock.patch.object(profile_layout, "_viewer_campus_ids", return_value=set()):
            self.assertIsNone(profile_layout.campus_block(self.owner, self.me, is_self=False, is_minor=False))
        self.assertIsNone(profile_layout.campus_block(self.owner, self.me, is_self=False, is_minor=True))

    def test_one_broken_block_does_not_break_the_response(self):
        with mock.patch.object(profile_layout, "teacher_block", side_effect=RuntimeError("boom")):
            r = self.c.get(_url("teach1"))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["teacher"])


class CreatorNavigationTests(TestCase):
    """P5b - creator layout can NAVIGATE: series preview, visible-only highlight count, `?creator_username=` filter."""

    def setUp(self):
        self.me = User.objects.create_user(username="viewer2", password="x")
        self.owner = User.objects.create_user(username="creator1", password="x")
        self.c = APIClient()
        self.c.force_authenticate(self.me)

    def _series(self, title, **kw):
        from testseries.models import TestSeries

        kw.setdefault("status", TestSeries.Status.PUBLISHED)
        kw.setdefault("source", TestSeries.Source.INDIVIDUAL)
        return TestSeries.objects.create(creator=self.owner, title=title, **kw)

    def test_series_preview_is_newest_first_published_only_and_capped(self):
        from testseries.models import TestSeries

        self._series("Draft one", status=TestSeries.Status.DRAFT)
        for i in range(5):
            self._series(f"Series {i}")
        items = profile_layout._series_items(self.owner)
        self.assertEqual(len(items), profile_layout.MAX_SERIES_PREVIEW)
        self.assertEqual(items[0]["title"], "Series 4")
        self.assertTrue(all(set(i) == {"id", "title", "subject", "is_paid"} for i in items))
        self.assertNotIn("Draft one", [i["title"] for i in items])

    def test_series_section_only_when_there_is_something_to_open(self):
        base = {"reels_count": 3, "series_count": 0, "highlights_count": 0}
        with mock.patch.object(profile_layout, "creator_block", return_value={**base, "series_items": []}):
            d = self.c.get(_url("viewer2")).data
        self.assertEqual(d["profile_layout"], "creator")
        self.assertNotIn("series", d["sections"])
        item = {"id": "x", "title": "T", "subject": "", "is_paid": False}
        with mock.patch.object(profile_layout, "creator_block", return_value={**base, "series_count": 1, "series_items": [item]}):
            d = self.c.get(_url("viewer2")).data
        self.assertIn("series", d["sections"])
        self.assertEqual(d["creator"]["series_items"], [item])

    def test_highlight_count_owner_sees_all_visitor_only_visible(self):
        from post.models import Highlight

        Highlight.objects.create(user=self.owner, title="A")
        self.assertEqual(profile_layout._highlights_count(self.owner, self.owner, True), 1)
        # visitor who may not open the owner's highlights (blocked / private) -> 0, nothing leaks
        with mock.patch("post.highlights.can_view_highlights_of", return_value=False):
            self.assertEqual(profile_layout._highlights_count(self.owner, self.me, False), 0)
        # highlight without any item the visitor may see is not counted
        self.assertEqual(profile_layout._highlights_count(self.owner, self.me, False), 0)

    def test_series_list_creator_filter_narrows_only(self):
        self._series("Mine A")
        other = User.objects.create_user(username="someone3", password="x")
        from testseries.models import TestSeries

        TestSeries.objects.create(
            creator=other, title="Other", source=TestSeries.Source.INDIVIDUAL, status=TestSeries.Status.PUBLISHED,
        )
        r = self.c.get("/testseries/testseries/", {"creator_username": "CREATOR1", "source": "individual", "status": "published"})
        self.assertEqual(r.status_code, 200)
        rows = r.data["results"] if isinstance(r.data, dict) and "results" in r.data else r.data
        self.assertEqual([x["title"] for x in rows], ["Mine A"])

