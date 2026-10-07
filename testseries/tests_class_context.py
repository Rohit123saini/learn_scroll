# testseries/tests_class_context.py
"""
TASK 9 tests — test series that belong to a tuition class.

Run:  python manage.py test testseries.tests_class_context

9.1  a class (and campus) series is ALWAYS free: bridge, publish, start — even for
     a legacy row that still carries a price.
9.2  publishing a class series posts ONE notice-board entry and notifies the pass
     holders (bell + push, preferences respected) — and never twice.
9.3  the list endpoint can be narrowed to one class (`context_type` + `context_id`).

Authored without a Django runtime available — treat the first run as the review.
Celery is eager under `manage.py test` (settings.TESTING); the push provider is mocked.
"""
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Notification
from testseries.bridge import announce_series_published
from testseries.models import Question, TestSeries, TestSeriesPurchase
from tuitionclass import bridge as tc_bridge
from tuitionclass.models import ClassPass, Classroom, Notice, PassPurchase
from tuitionclass.serializers import NoticeSerializer

User = get_user_model()
PUSH = "message.push_utils.send_push_to_users"


def _user(prefix):
    unique = uuid.uuid4().hex[:8]
    try:
        return User.objects.create_user(username=f"{prefix}_{unique}", password="testpass123")
    except TypeError:
        return User.objects.create_user(email=f"{prefix}_{unique}@example.com", password="testpass123")


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _question_dict(order=1):
    return {
        "order": order, "question_type": "mcq", "text": f"Q{order}", "marks": 4,
        "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
        "correct_answer": {"option_id": "a"},
    }


class ClassContextBase(TestCase):
    def setUp(self):
        self.teacher = _user("teacher")
        self.student = _user("student")
        self.outsider = _user("outsider")
        self.classroom = Classroom.objects.create(teacher=self.teacher, title="DSA Batch")
        self.class_pass = ClassPass.objects.create(
            classroom=self.classroom, pass_type=ClassPass.PassType.MONTHLY,
            price=Decimal("100"), validity_days=10,
        )
        PassPurchase.objects.create(
            student=self.student, class_pass=self.class_pass, amount_paid=Decimal("100"), coins_spent=100,
            status=PassPurchase.Status.SUCCESS, is_active=True, expires_at=timezone.now() + timedelta(days=10),
        )
        patcher = mock.patch(PUSH)
        self.push = patcher.start()
        self.addCleanup(patcher.stop)

    @property
    def context_uuid(self):
        return uuid.UUID(int=self.classroom.pk)

    def create_via_bridge(self, **kwargs):
        defaults = dict(classroom=self.classroom, creator=self.teacher, title="Unit test 1",
                        description="Chapter 1-3", questions=[_question_dict()])
        defaults.update(kwargs)
        with self.captureOnCommitCallbacks(execute=True):
            return tc_bridge.create_testseries(**defaults)

    def draft_series(self, **kwargs):
        """A class series that still is a draft (what the viewset's `publish` acts on)."""
        series = TestSeries.objects.create(
            source=TestSeries.Source.TUITIONCLASS, context_type="classroom", context_id=self.context_uuid,
            creator=self.teacher, title="Draft test", duration_minutes=20, **kwargs,
        )
        Question.objects.create(
            series=series, order=1, question_type="mcq", text="Q1", marks=4, negative_marks=0,
            options=[{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], correct_answer={"option_id": "a"},
        )
        series.recompute_total_marks()
        return series


# =====================================================================
# 9.1 — always free
# =====================================================================
class AlwaysFreeTests(ClassContextBase):
    def test_bridge_forces_class_series_free(self):
        series = self.create_via_bridge(is_paid=True, price_coins=50)
        series.refresh_from_db()
        self.assertEqual(series.source, TestSeries.Source.TUITIONCLASS)
        self.assertFalse(series.is_paid)
        self.assertEqual(series.price_coins, 0)

    def test_model_save_forces_free_for_class_and_campus(self):
        for source in (TestSeries.Source.TUITIONCLASS, TestSeries.Source.CAMPUS):
            series = TestSeries.objects.create(source=source, creator=self.teacher, title="x", is_paid=True, price_coins=99)
            self.assertFalse(series.is_paid, source)
            self.assertEqual(series.price_coins, 0, source)

    def test_publish_corrects_a_legacy_paid_class_draft(self):
        series = self.draft_series()
        TestSeries.objects.filter(pk=series.pk).update(is_paid=True, price_coins=40)  # legacy row
        res = _client(self.teacher).post(reverse("testseries-publish", kwargs={"pk": series.pk}))
        self.assertEqual(res.status_code, 200, res.content)
        series.refresh_from_db()
        self.assertEqual(series.status, TestSeries.Status.PUBLISHED)
        self.assertFalse(series.is_paid)
        self.assertEqual(series.price_coins, 0)

    def test_start_never_charges_for_a_legacy_paid_class_series(self):
        series = self.create_via_bridge()
        TestSeries.objects.filter(pk=series.pk).update(is_paid=True, price_coins=40)  # legacy row, never re-saved
        res = _client(self.student).post(
            reverse("testseries-attempt-start", kwargs={"series_id": series.pk}), {}, format="json"
        )
        self.assertEqual(res.status_code, 201, res.content)  # a charge would be 402 (the student has no coins)
        self.assertFalse(TestSeriesPurchase.objects.filter(series=series).exists())


# =====================================================================
# 9.2 — announce: notice board + notification, exactly once
# =====================================================================
class AnnounceTests(ClassContextBase):
    def notices(self, series):
        return Notice.objects.filter(classroom=self.classroom, source_type="testseries", source_id=series.pk)

    def bells(self, user):
        return Notification.objects.filter(recipient=user, notif_type=Notification.NotifType.TESTSERIES_POSTED)

    def test_creating_a_class_series_posts_notice_and_notifies_pass_holders(self):
        series = self.create_via_bridge()
        notice = self.notices(series).get()
        self.assertEqual(notice.posted_by_id, self.teacher.id)
        self.assertTrue(notice.title.startswith("New test: "))
        self.assertIn("Unit test 1", notice.title)

        bell = self.bells(self.student).get()
        self.assertEqual(bell.classroom_id, self.classroom.pk)
        self.assertEqual(bell.data["series_id"], str(series.pk))
        self.assertEqual(bell.data["notice_id"], str(notice.pk))
        self.assertFalse(self.bells(self.teacher).exists())     # the author is not notified of their own test
        self.assertFalse(self.bells(self.outsider).exists())    # no pass -> not in the class

        self.push.assert_called_once()
        self.assertEqual(list(self.push.call_args.args[0]), [self.student.id])
        series.refresh_from_db()
        self.assertIsNotNone(series.announced_at)

    def test_announcing_again_does_nothing(self):
        series = self.create_via_bridge()
        series.refresh_from_db()
        self.assertFalse(announce_series_published(series))
        self.assertEqual(self.notices(series).count(), 1)
        self.assertEqual(self.bells(self.student).count(), 1)
        self.assertEqual(self.push.call_count, 1)

    def test_publish_action_announces_once(self):
        series = self.draft_series()
        url = reverse("testseries-publish", kwargs={"pk": series.pk})
        with self.captureOnCommitCallbacks(execute=True):
            first = _client(self.teacher).post(url)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(self.notices(series).count(), 1)
        self.assertEqual(self.bells(self.student).count(), 1)

        with self.captureOnCommitCallbacks(execute=True):
            again = _client(self.teacher).post(url)
        self.assertEqual(again.status_code, 400)  # already published
        self.assertEqual(self.notices(series).count(), 1)
        self.assertEqual(self.bells(self.student).count(), 1)

    def test_notice_expires_with_the_test_window(self):
        ends = timezone.now() + timedelta(days=2)
        series = self.draft_series(
            delivery_mode=TestSeries.DeliveryMode.SCHEDULED, starts_at=timezone.now() + timedelta(hours=1), ends_at=ends,
        )
        with self.captureOnCommitCallbacks(execute=True):
            _client(self.teacher).post(reverse("testseries-publish", kwargs={"pk": series.pk}))
        self.assertEqual(self.notices(series).get().expires_at, ends)

    def test_failed_hook_releases_the_claim_so_a_retry_works(self):
        series = self.create_via_bridge()  # announced once already
        TestSeries.objects.filter(pk=series.pk).update(announced_at=None)
        Notice.objects.filter(source_id=series.pk).delete()
        Notification.objects.filter(recipient=self.student).delete()
        series.refresh_from_db()

        with mock.patch("tuitionclass.bridge.on_testseries_published", side_effect=RuntimeError("boom")):
            self.assertFalse(announce_series_published(series))
        series.refresh_from_db()
        self.assertIsNone(series.announced_at)

        self.assertTrue(announce_series_published(series))
        self.assertEqual(self.notices(series).count(), 1)

    def test_individual_series_is_not_announced_to_a_class(self):
        series = TestSeries.objects.create(
            source=TestSeries.Source.INDIVIDUAL, creator=self.teacher, title="solo", is_paid=True, price_coins=10,
            status=TestSeries.Status.PUBLISHED,
        )
        self.assertFalse(announce_series_published(series))
        self.assertFalse(Notice.objects.filter(source_type="testseries").exists())

    def test_push_respects_notification_preferences(self):
        with mock.patch("core.services.channels_for", return_value=[]):  # bell only (muted / quiet hours / push off)
            series = self.create_via_bridge()
        self.assertEqual(self.bells(self.student).count(), 1)  # the bell row is still created
        self.push.assert_not_called()
        self.assertEqual(self.notices(series).count(), 1)

    def test_notice_source_is_unique_per_classroom(self):
        series = self.create_via_bridge()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Notice.objects.create(
                    classroom=self.classroom, posted_by=self.teacher, title="dup", message="dup",
                    source_type="testseries", source_id=series.pk,
                )
        # manual notices (no source) are unaffected
        for i in range(2):
            Notice.objects.create(classroom=self.classroom, posted_by=self.teacher, title=f"n{i}", message="m")

    def test_notice_api_exposes_source_read_only(self):
        series = self.create_via_bridge()
        data = NoticeSerializer(self.notices(series).get()).data
        self.assertEqual(data["source_type"], "testseries")
        self.assertEqual(str(data["source_id"]), str(series.pk))
        self.assertIn("source_type", NoticeSerializer.Meta.read_only_fields)


# =====================================================================
# 9.3 — "the tests of this class" list for the class detail screen
# =====================================================================
class ClassSeriesListTests(ClassContextBase):
    def list_ids(self, user, **params):
        res = _client(user).get(reverse("testseries-list"), params)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        rows = body["results"] if isinstance(body, dict) else body
        return {row["id"] for row in rows}

    def test_filter_by_class_for_a_member(self):
        in_class = self.create_via_bridge(title="In class")
        other_room = Classroom.objects.create(teacher=self.teacher, title="Other")
        elsewhere = TestSeries.objects.create(
            source=TestSeries.Source.TUITIONCLASS, context_type="classroom", context_id=uuid.UUID(int=other_room.pk),
            creator=self.teacher, title="Elsewhere", status=TestSeries.Status.PUBLISHED,
        )
        ids = self.list_ids(self.student, context_type="classroom", context_id=self.classroom.pk)
        self.assertIn(str(in_class.pk), ids)
        self.assertNotIn(str(elsewhere.pk), ids)

    def test_a_non_member_sees_nothing_of_the_class(self):
        series = self.create_via_bridge()
        ids = self.list_ids(self.outsider, context_type="classroom", context_id=self.classroom.pk)
        self.assertNotIn(str(series.pk), ids)

    def test_garbage_context_id_is_empty_not_an_error(self):
        self.create_via_bridge()
        self.assertEqual(self.list_ids(self.student, context_type="classroom", context_id="not-an-id"), set())
