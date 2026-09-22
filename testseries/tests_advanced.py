# testseries/tests_advanced.py
"""
Tests for the advanced test-series features: pricing policy, question
gating, server-side timer + autosave, negative marking, certificates,
scheduled/live windows, context access control, result release,
leaderboard/analytics, live video + proctoring (LiveKit mocked), bulk /
CSV answer-key import, public share + verify endpoints.

Run:  python manage.py test testseries.tests_advanced

⚠️ Authored WITHOUT a Django runtime available — treat the first run as the
real review. `_make_user()` is the one seam to adapt if your `User` model needs
other required fields (same convention as testseries/tests.py). No test
touches LiveKit, S3 or the coin ledger: LiveKit calls are mocked, and series
are created through the ORM as free (`is_paid=False`) so `start` needs no
coins — the pricing policy itself is tested through the API.
"""
import contextlib
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from . import tasks
from .models import (
    Question, TestAttempt, TestCertificate, TestLiveSession, TestRecording, TestSeries,
)

User = get_user_model()

# Context access: tests drive the resolver through this module-level set.
ALLOWED_CONTEXT_IDS = set()


def _resolver(*, user, context_type):
    return set(ALLOWED_CONTEXT_IDS)


def _make_user(prefix="user"):
    unique = uuid.uuid4().hex[:8]
    try:
        return User.objects.create_user(username=f"{prefix}_{unique}", password="testpass123")
    except TypeError:
        return User.objects.create_user(email=f"{prefix}_{unique}@example.com", password="testpass123")


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def _mcq(series, order, *, correct="a", marks=4, negative=1, **extra):
    return Question.objects.create(
        series=series, order=order, question_type="mcq", text=f"Q{order}", marks=marks,
        negative_marks=negative,
        options=[{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
        correct_answer={"option_id": correct}, **extra,
    )


def _series(creator, *, questions=2, published=True, **kwargs):
    """A FREE individual series built through the ORM (legacy-style, so no coins)."""
    defaults = dict(
        source=TestSeries.Source.INDIVIDUAL, creator=creator, title="Mock test",
        is_paid=False, price_coins=0, duration_minutes=30,
    )
    defaults.update(kwargs)
    series = TestSeries.objects.create(**defaults)
    for i in range(1, questions + 1):
        _mcq(series, i)
    series.recompute_total_marks()
    if published:
        series.status = TestSeries.Status.PUBLISHED
        series.ensure_share_slug(save=False)
        series.save()
    return series


def _answers(series, choices):
    """{question_id: {"option_id": x}} — `choices` in question order, None = blank."""
    out = {}
    for question, choice in zip(series.questions.order_by("order"), choices):
        out[str(question.id)] = {"option_id": choice} if choice else {}
    return out


class _Base(TestCase):
    def setUp(self):
        ALLOWED_CONTEXT_IDS.clear()
        self.teacher = _make_user("teacher")
        self.student = _make_user("student")
        self.other = _make_user("other")

    def start(self, series, user=None):
        res = _client(user or self.student).post(
            reverse("testseries-attempt-start", kwargs={"series_id": series.id}), {}, format="json"
        )
        return res

    def submit(self, attempt_id, answers, user=None, **extra):
        return _client(user or self.student).post(
            reverse("testseries-attempt-submit", kwargs={"pk": attempt_id}),
            {"answers": answers, **extra}, format="json",
        )


# =====================================================================
class PricingPolicyApiTests(_Base):
    def create(self, payload):
        return _client(self.teacher).post(reverse("testseries-list"), payload, format="json")

    def test_individual_series_must_be_paid(self):
        res = self.create({"title": "Free try", "is_paid": False})
        self.assertEqual(res.status_code, 400)
        self.assertIn("is_paid", res.json())

    def test_paid_needs_a_price(self):
        res = self.create({"title": "Paid", "is_paid": True, "price_coins": 0})
        self.assertEqual(res.status_code, 400)
        self.assertIn("price_coins", res.json())

    def test_paid_with_price_is_created(self):
        res = self.create({"title": "Paid", "is_paid": True, "price_coins": 50, "duration_minutes": 30})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["source"], "individual")

    @override_settings(TESTSERIES_PRICING_POLICY={"individual": {"mode": "optional"}})
    def test_policy_is_configurable(self):
        res = self.create({"title": "Free allowed now", "is_paid": False})
        self.assertEqual(res.status_code, 201, res.content)

    def test_campus_series_is_forced_free_at_model_level(self):
        series = TestSeries.objects.create(
            source=TestSeries.Source.CAMPUS, creator=self.teacher, title="Campus", is_paid=True, price_coins=99,
        )
        self.assertFalse(series.is_paid)
        self.assertEqual(series.price_coins, 0)

    def test_legacy_free_individual_can_still_be_edited(self):
        series = _series(self.teacher, published=False)
        res = _client(self.teacher).patch(
            reverse("testseries-detail", kwargs={"pk": series.id}), {"title": "Renamed"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.content)

    def test_publish_is_blocked_for_a_free_individual_draft(self):
        series = _series(self.teacher, published=False)
        res = _client(self.teacher).post(reverse("testseries-publish", kwargs={"pk": series.id}))
        self.assertEqual(res.status_code, 400)
        self.assertIn("is_paid", res.json())


# =====================================================================
class QuestionGatingTests(_Base):
    def test_series_detail_hides_questions_from_non_creators(self):
        series = _series(self.teacher)
        url = reverse("testseries-detail", kwargs={"pk": series.id})
        as_student = _client(self.student).get(url).json()
        self.assertNotIn("questions", as_student)
        self.assertEqual(as_student["question_count"], 2)
        as_creator = _client(self.teacher).get(url).json()
        self.assertEqual(len(as_creator["questions"]), 2)
        self.assertIn("correct_answer", as_creator["questions"][0])

    def test_questions_endpoint_needs_an_attempt(self):
        series = _series(self.teacher)
        url = reverse("testseries-question-list", kwargs={"series_pk": series.id})
        self.assertEqual(_client(self.student).get(url).status_code, 403)
        self.assertEqual(self.start(series).status_code, 201)
        res = _client(self.student).get(url)
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        rows = payload["results"] if isinstance(payload, dict) else payload
        self.assertNotIn("correct_answer", rows[0])
        self.assertNotIn("explanation", rows[0])

    def test_draft_questions_are_not_readable_by_others(self):
        series = _series(self.teacher, published=False)
        url = reverse("testseries-question-list", kwargs={"series_pk": series.id})
        self.assertEqual(_client(self.student).get(url).status_code, 403)
        self.assertEqual(_client(self.teacher).get(url).status_code, 200)


# =====================================================================
class AttemptTimerAndGradingTests(_Base):
    def test_attempt_gets_server_side_deadline(self):
        series = _series(self.teacher, duration_minutes=30)
        res = self.start(series)
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()
        self.assertIsNotNone(data["started_at"])
        self.assertIsNotNone(data["deadline_at"])
        self.assertIsNotNone(data["server_time"])
        attempt = TestAttempt.objects.get(pk=data["id"])
        self.assertEqual(attempt.deadline_at - attempt.started_at, timedelta(minutes=30))

    def test_negative_marking_is_net_and_blank_is_free(self):
        series = _series(self.teacher, questions=3)  # +4 / -1 each
        attempt_id = self.start(series).json()["id"]
        res = self.submit(attempt_id, _answers(series, ["a", "b", None]))  # right, wrong, blank
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(data["status"], "checked")
        self.assertEqual(data["final_score"], 3)  # 4 - 1, the blank costs nothing
        penalties = sorted(r["penalty"] for r in data["responses"])
        self.assertEqual(penalties, [0, 0, 1])

    def test_net_score_never_goes_below_zero(self):
        series = _series(self.teacher, questions=2)
        attempt_id = self.start(series).json()["id"]
        data = self.submit(attempt_id, _answers(series, ["b", "b"])).json()
        self.assertEqual(data["final_score"], 0)

    def test_garbage_answer_shapes_do_not_500(self):
        series = _series(self.teacher, questions=2)
        attempt_id = self.start(series).json()["id"]
        q1, q2 = [str(q.id) for q in series.questions.order_by("order")]
        res = self.submit(attempt_id, {q1: "a", q2: ["a", ["nested"]]})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["final_score"], 0)

    def test_submit_is_idempotent(self):
        series = _series(self.teacher)
        attempt_id = self.start(series).json()["id"]
        first = self.submit(attempt_id, _answers(series, ["a", "a"]))
        second = self.submit(attempt_id, _answers(series, ["b", "b"]))
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["final_score"], second.json()["final_score"])
        self.assertEqual(TestAttempt.objects.get(pk=attempt_id).responses.count(), 2)

    def test_late_submit_grades_the_autosave_not_the_late_payload(self):
        series = _series(self.teacher)
        attempt_id = self.start(series).json()["id"]
        client = _client(self.student)
        save = client.patch(
            reverse("testseries-attempt-save-progress", kwargs={"pk": attempt_id}),
            {"answers": _answers(series, ["a", None])}, format="json",
        )
        self.assertEqual(save.status_code, 200, save.content)
        TestAttempt.objects.filter(pk=attempt_id).update(deadline_at=timezone.now() - timedelta(minutes=5))

        data = self.submit(attempt_id, _answers(series, ["a", "a"])).json()  # late payload has 2 correct
        self.assertTrue(data["submitted_late"])
        self.assertEqual(data["final_score"], 4)  # only the autosaved answer counted

    def test_autosave_is_refused_after_the_deadline(self):
        series = _series(self.teacher)
        attempt_id = self.start(series).json()["id"]
        TestAttempt.objects.filter(pk=attempt_id).update(deadline_at=timezone.now() - timedelta(minutes=5))
        res = _client(self.student).patch(
            reverse("testseries-attempt-save-progress", kwargs={"pk": attempt_id}),
            {"answers": _answers(series, ["a", "a"])}, format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_auto_submit_task_closes_expired_attempts_from_the_draft(self):
        series = _series(self.teacher)
        attempt_id = self.start(series).json()["id"]
        attempt = TestAttempt.objects.get(pk=attempt_id)
        attempt.save_progress(_answers(series, ["a", "a"]))
        TestAttempt.objects.filter(pk=attempt_id).update(deadline_at=timezone.now() - timedelta(minutes=5))

        closed = tasks.auto_submit_expired_attempts()
        self.assertEqual(closed, 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, TestAttempt.Status.CHECKED)
        self.assertEqual(attempt.final_score, 8)
        self.assertFalse(attempt.submitted_late)  # server closed it; nothing late to penalise

    def test_legacy_untimed_attempts_are_never_auto_closed(self):
        series = _series(self.teacher)
        attempt_id = self.start(series).json()["id"]
        TestAttempt.objects.filter(pk=attempt_id).update(deadline_at=None, started_at=None)
        self.assertEqual(tasks.auto_submit_expired_attempts(), 0)


# =====================================================================
class DeliveryWindowTests(_Base):
    def test_scheduled_test_cannot_start_early(self):
        series = _series(
            self.teacher, delivery_mode="scheduled",
            starts_at=timezone.now() + timedelta(hours=1), ends_at=timezone.now() + timedelta(hours=3),
        )
        res = self.start(series)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["code"], "not_started")

    def test_ended_window_cannot_start(self):
        series = _series(
            self.teacher, delivery_mode="scheduled",
            starts_at=timezone.now() - timedelta(hours=3), ends_at=timezone.now() - timedelta(hours=1),
        )
        self.assertEqual(self.start(series).json()["code"], "ended")

    def test_deadline_is_capped_by_window_end(self):
        end = timezone.now() + timedelta(minutes=10)
        series = _series(
            self.teacher, delivery_mode="scheduled", duration_minutes=60,
            starts_at=timezone.now() - timedelta(minutes=5), ends_at=end,
        )
        attempt = TestAttempt.objects.get(pk=self.start(series).json()["id"])
        self.assertEqual(attempt.deadline_at, end)

    def test_live_late_entry_closes(self):
        series = _series(
            self.teacher, delivery_mode="live", late_entry_minutes=5,
            starts_at=timezone.now() - timedelta(minutes=20), ends_at=timezone.now() + timedelta(hours=1),
        )
        self.assertEqual(self.start(series).json()["code"], "late_closed")

    def test_window_config_is_validated_on_create(self):
        res = _client(self.teacher).post(
            reverse("testseries-list"),
            {"title": "Live", "is_paid": True, "price_coins": 5, "delivery_mode": "live"}, format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("starts_at", res.json())


# =====================================================================
class ContextAccessTests(_Base):
    def _campus_series(self):
        context_id = uuid.uuid4()
        series = _series(
            self.teacher, source=TestSeries.Source.CAMPUS, context_type="section", context_id=context_id,
        )
        return series, context_id

    @override_settings(TESTSERIES_CONTEXT_ACCESS={
        "section": "testseries.tests_advanced._resolver", "classroom": "testseries.tests_advanced._resolver",
    })
    def test_non_members_cannot_see_or_start_a_campus_series(self):
        series, context_id = self._campus_series()
        listing = _client(self.student).get(reverse("testseries-list")).json()
        rows = listing["results"] if isinstance(listing, dict) else listing
        self.assertNotIn(str(series.id), [r["id"] for r in rows])
        self.assertEqual(self.start(series).status_code, 403)

        ALLOWED_CONTEXT_IDS.add(context_id)  # now a member
        rows = _client(self.student).get(reverse("testseries-list")).json()
        rows = rows["results"] if isinstance(rows, dict) else rows
        self.assertIn(str(series.id), [r["id"] for r in rows])
        self.assertEqual(self.start(series).status_code, 201)

    @override_settings(TESTSERIES_CONTEXT_ACCESS={"section": "does.not.exist", "classroom": "does.not.exist"})
    def test_a_broken_resolver_fails_closed(self):
        series, _ = self._campus_series()
        self.assertEqual(self.start(series).status_code, 403)

    def test_creator_always_sees_their_own_series(self):
        series, _ = self._campus_series()
        rows = _client(self.teacher).get(reverse("testseries-list")).json()
        rows = rows["results"] if isinstance(rows, dict) else rows
        self.assertIn(str(series.id), [r["id"] for r in rows])


# =====================================================================
@mock.patch("testseries.models._notify")
class CertificationTests(_Base):
    def _certified(self, **kw):
        return _series(
            self.teacher, questions=2, pass_percentage=50, certificate_enabled=True,
            certificate_title="Physics Foundations", **kw,
        )

    def test_passing_attempt_earns_exactly_one_certificate(self, _notify):
        series = self._certified()  # 8 marks total, pass at 50% = 4
        first = self.start(series).json()["id"]
        data = self.submit(first, _answers(series, ["a", "a"])).json()
        self.assertTrue(data["passed"])
        self.assertIsNotNone(data["certificate_code"])
        self.assertEqual(TestCertificate.objects.filter(series=series, student=self.student).count(), 1)

        # A retry that also passes must not mint a duplicate.
        series.attempts_allowed = 2
        series.save(update_fields=["attempts_allowed"])
        second = self.start(series).json()["id"]
        self.submit(second, _answers(series, ["a", "a"]))
        self.assertEqual(TestCertificate.objects.filter(series=series, student=self.student).count(), 1)

    def test_failing_attempt_gets_no_certificate(self, _notify):
        series = self._certified()
        attempt_id = self.start(series).json()["id"]
        data = self.submit(attempt_id, _answers(series, ["b", "b"])).json()
        self.assertFalse(data["passed"])
        self.assertIsNone(data["certificate_code"])
        self.assertFalse(TestCertificate.objects.exists())

    def test_series_without_certificates_only_reports_pass_fail(self, _notify):
        series = _series(self.teacher, questions=2, pass_percentage=50)
        attempt_id = self.start(series).json()["id"]
        data = self.submit(attempt_id, _answers(series, ["a", "a"])).json()
        self.assertTrue(data["passed"])
        self.assertFalse(TestCertificate.objects.exists())

    def test_certificate_endpoints_and_public_verify(self, _notify):
        series = self._certified()
        attempt_id = self.start(series).json()["id"]
        self.submit(attempt_id, _answers(series, ["a", "a"]))
        cert = TestCertificate.objects.get()

        mine = _client(self.student).get(reverse("testseries-attempt-certificate", kwargs={"pk": attempt_id}))
        self.assertEqual(mine.status_code, 200)
        self.assertEqual(mine.json()["code"], cert.code)
        self.assertEqual(mine.json()["title"], "Physics Foundations")

        listing = _client(self.student).get(reverse("testseries-certificate-mine")).json()
        rows = listing["results"] if isinstance(listing, dict) else listing
        self.assertEqual([r["code"] for r in rows], [cert.code])

        # Public, UNAUTHENTICATED verification (case-insensitive code).
        verify = _client().get(reverse("testseries-certificate-verify", kwargs={"code": cert.code.lower()}))
        self.assertEqual(verify.status_code, 200)
        self.assertTrue(verify.json()["valid"])
        self.assertNotIn("student", verify.json())  # no internal ids

        pdf = _client(self.student).get(reverse("testseries-attempt-certificate-pdf", kwargs={"pk": attempt_id}))
        self.assertIn(pdf.status_code, (200, 501))  # 501 = reportlab not installed

    def test_unknown_code_is_404_and_revoked_certificate_is_flagged(self, _notify):
        self.assertEqual(
            _client().get(reverse("testseries-certificate-verify", kwargs={"code": "LS-NOPE-NOPE-NOPE"})).status_code, 404
        )
        series = self._certified()
        attempt_id = self.start(series).json()["id"]
        self.submit(attempt_id, _answers(series, ["a", "a"]))
        cert = TestCertificate.objects.get()
        res = _client(self.teacher).post(
            reverse("testseries-revoke-certificate", kwargs={"pk": series.id}),
            {"code": cert.code, "reason": "Cheating"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        verify = _client().get(reverse("testseries-certificate-verify", kwargs={"code": cert.code}))
        self.assertFalse(verify.json()["valid"])

    def test_someone_elses_certificate_is_not_readable(self, _notify):
        series = self._certified()
        attempt_id = self.start(series).json()["id"]
        self.submit(attempt_id, _answers(series, ["a", "a"]))
        res = _client(self.other).get(reverse("testseries-attempt-certificate", kwargs={"pk": attempt_id}))
        self.assertEqual(res.status_code, 403)

    def test_certificate_config_requires_a_pass_mark(self, _notify):
        res = _client(self.teacher).post(
            reverse("testseries-list"),
            {"title": "T", "is_paid": True, "price_coins": 5, "certificate_enabled": True}, format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("pass_percentage", res.json())


# =====================================================================
class ResultReleaseAndAnalyticsTests(_Base):
    def test_manual_release_hides_the_score_until_the_creator_releases(self):
        series = _series(self.teacher, result_release="manual")
        attempt_id = self.start(series).json()["id"]
        self.submit(attempt_id, _answers(series, ["a", "a"]))
        url = reverse("testseries-attempt-detail", kwargs={"pk": attempt_id})

        hidden = _client(self.student).get(url).json()
        self.assertIsNone(hidden["final_score"])
        self.assertEqual(hidden["responses"], [])
        self.assertFalse(hidden["results_released"])
        self.assertEqual(
            _client(self.student).get(reverse("testseries-attempt-solutions", kwargs={"pk": attempt_id})).status_code, 403
        )
        self.assertEqual(_client(self.teacher).get(url).json()["final_score"], 8)  # creator sees it

        res = _client(self.teacher).post(reverse("testseries-release-results", kwargs={"pk": series.id}))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(_client(self.student).get(url).json()["final_score"], 8)

    def test_leaderboard_ranks_best_attempts_and_ties_share_a_rank(self):
        series = _series(self.teacher, questions=2)
        scores = {}
        for user, picks in ((self.student, ["a", "a"]), (self.other, ["a", "b"]), (_make_user("third"), ["a", "b"])):
            attempt_id = self.start(series, user).json()["id"]
            scores[user.id] = self.submit(attempt_id, _answers(series, picks), user).json()["final_score"]
        res = _client(self.student).get(reverse("testseries-leaderboard", kwargs={"pk": series.id}))
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()["results"]
        self.assertEqual([r["rank"] for r in rows], [1, 2, 2])
        self.assertTrue(rows[0]["is_me"])

    def test_analytics_and_solutions(self):
        series = _series(self.teacher, questions=2)
        q1 = series.questions.get(order=1)
        q1.topic, q1.explanation = "Kinematics", "Because A."
        q1.save()
        attempt_id = self.start(series).json()["id"]
        self.submit(
            attempt_id, _answers(series, ["a", "b"]),
            timings={str(q1.id): 42},
        )
        analytics = _client(self.student).get(reverse("testseries-attempt-analytics", kwargs={"pk": attempt_id}))
        self.assertEqual(analytics.status_code, 200, analytics.content)
        body = analytics.json()
        self.assertEqual(body["standing"]["rank"], 1)
        topics = {t["topic"]: t for t in body["topics"]}
        self.assertEqual(topics["Kinematics"]["accuracy"], 100.0)
        self.assertEqual(body["time"]["total_seconds"], 42)

        solutions = _client(self.student).get(reverse("testseries-attempt-solutions", kwargs={"pk": attempt_id}))
        first = solutions.json()["results"][0]
        self.assertEqual(first["explanation"], "Because A.")
        self.assertEqual(first["correct_answer"], {"option_id": "a"})

    def test_solutions_can_be_switched_off(self):
        series = _series(self.teacher, show_solutions=False)
        attempt_id = self.start(series).json()["id"]
        self.submit(attempt_id, _answers(series, ["a", "a"]))
        res = _client(self.student).get(reverse("testseries-attempt-solutions", kwargs={"pk": attempt_id}))
        self.assertEqual(res.status_code, 403)


# =====================================================================
class ImportAndPublishTests(_Base):
    CSV = (
        "type,question,option_a,option_b,correct,marks,negative,topic\n"
        "mcq,Two plus two?,3,4,B,4,1,Maths\n"
        "msq,Even numbers?,2,3,A,4,1,Maths\n"
    )

    def _draft(self):
        return TestSeries.objects.create(
            source=TestSeries.Source.INDIVIDUAL, creator=self.teacher, title="Draft", is_paid=True, price_coins=10,
        )

    def test_csv_import_creates_a_gradable_test(self):
        series = self._draft()
        upload = SimpleUploadedFile("q.csv", self.CSV.encode(), content_type="text/csv")
        res = _client(self.teacher).post(
            reverse("testseries-questions-import", kwargs={"pk": series.id}), {"file": upload}, format="multipart"
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["created"], 2)
        series.refresh_from_db()
        self.assertEqual(series.total_marks, 8)
        self.assertEqual(series.questions.get(order=1).correct_answer, {"option_id": "b"})
        self.assertTrue(
            _client(self.teacher).get(reverse("testseries-answer-key", kwargs={"pk": series.id})).json()["complete"]
        )

    def test_bad_csv_imports_nothing_and_reports_rows(self):
        series = self._draft()
        bad = "question,option_a,option_b,correct\nOk?,x,y,A\nBroken?,x,y,\n"
        upload = SimpleUploadedFile("q.csv", bad.encode(), content_type="text/csv")
        res = _client(self.teacher).post(
            reverse("testseries-questions-import", kwargs={"pk": series.id}), {"file": upload}, format="multipart"
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["errors"][0]["row"], 3)
        self.assertEqual(series.questions.count(), 0)

    def test_bulk_json_is_all_or_nothing(self):
        series = self._draft()
        good = {"question_type": "mcq", "text": "Q", "marks": 2,
                "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], "correct_answer": {"option_id": "a"}}
        bad = {"question_type": "mcq", "text": "Broken", "marks": 2, "options": [], "correct_answer": {}}
        res = _client(self.teacher).post(
            reverse("testseries-questions-bulk", kwargs={"pk": series.id}), {"questions": [good, bad]}, format="json"
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(series.questions.count(), 0)
        res = _client(self.teacher).post(
            reverse("testseries-questions-bulk", kwargs={"pk": series.id}), {"questions": [good, dict(good)]}, format="json"
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual([q.order for q in series.questions.order_by("order")], [1, 2])

    def test_only_the_creator_can_import(self):
        series = self._draft()
        upload = SimpleUploadedFile("q.csv", self.CSV.encode(), content_type="text/csv")
        res = _client(self.other).post(
            reverse("testseries-questions-import", kwargs={"pk": series.id}), {"file": upload}, format="multipart"
        )
        self.assertIn(res.status_code, (403, 404))

    @mock.patch("testseries.tasks.notify_followers_new_testseries")
    def test_publish_mints_share_slug_and_public_preview_has_no_questions(self, _notify):
        series = self._draft()
        _mcq(series, 1)
        series.recompute_total_marks()
        res = _client(self.teacher).post(reverse("testseries-publish", kwargs={"pk": series.id}))
        self.assertEqual(res.status_code, 200, res.content)
        slug = res.json()["share_slug"]
        self.assertTrue(slug)

        preview = _client().get(reverse("testseries-public", kwargs={"slug": slug}))  # anonymous
        self.assertEqual(preview.status_code, 200, preview.content)
        body = preview.json()
        self.assertEqual(body["question_count"], 1)
        self.assertNotIn("questions", body)
        self.assertNotIn("correct_answer", json.dumps(body))

    def test_campus_series_never_has_a_public_preview(self):
        series = _series(self.teacher, source=TestSeries.Source.CAMPUS, context_type="section", context_id=uuid.uuid4())
        res = _client().get(reverse("testseries-public", kwargs={"slug": series.share_slug}))
        self.assertEqual(res.status_code, 404)

    def test_publish_refuses_an_incomplete_answer_key(self):
        series = self._draft()
        question = _mcq(series, 1)
        Question.objects.filter(pk=question.pk).update(correct_answer={})
        res = _client(self.teacher).post(reverse("testseries-publish", kwargs={"pk": series.id}))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["missing_question_orders"], [1])


# =====================================================================
@contextlib.contextmanager
def _live_patches():
    """Everything that would talk to LiveKit, as inspectable mocks (the yielded
    dict is keyed by function name: `patched["start_recording"].call_count`)."""
    with mock.patch.multiple(
        "testseries.views_advanced.live",
        livekit_url=mock.DEFAULT, issue_token=mock.DEFAULT, ensure_room=mock.DEFAULT,
        end_room=mock.DEFAULT, start_recording=mock.DEFAULT, stop_recording=mock.DEFAULT,
    ) as patched:
        patched["livekit_url"].return_value = "wss://lk.example"
        patched["issue_token"].return_value = "jwt-token"
        patched["start_recording"].side_effect = lambda room: f"EG_{uuid.uuid4().hex[:8]}"
        yield patched


class LiveAndProctoringTests(_Base):
    def _live_series(self, **kw):
        return _series(
            self.teacher, delivery_mode="live", record_live=True,
            starts_at=timezone.now() - timedelta(minutes=1), ends_at=timezone.now() + timedelta(hours=1),
            late_entry_minutes=30, **kw,
        )

    def test_host_goes_live_and_the_session_is_recorded(self):
        series = self._live_series()
        with _live_patches() as patched:
            res = _client(self.teacher).post(reverse("testseries-live-start", kwargs={"pk": series.id}))
            self.assertEqual(res.status_code, 200, res.content)
            self.assertEqual(res.json()["token"], "jwt-token")
            # A second press must not start a second recording.
            _client(self.teacher).post(reverse("testseries-live-start", kwargs={"pk": series.id}))
            self.assertEqual(patched["start_recording"].call_count, 1)
        self.assertEqual(TestLiveSession.objects.get(series=series).status, "live")
        self.assertEqual(TestRecording.objects.filter(series=series, kind="live_session").count(), 1)

    def test_only_the_creator_can_go_live(self):
        series = self._live_series()
        with _live_patches():
            res = _client(self.other).post(reverse("testseries-live-start", kwargs={"pk": series.id}))
        self.assertIn(res.status_code, (403, 404))

    def test_a_non_live_series_cannot_go_live(self):
        series = _series(self.teacher)
        with _live_patches():
            res = _client(self.teacher).post(reverse("testseries-live-start", kwargs={"pk": series.id}))
        self.assertEqual(res.status_code, 400)

    def test_student_gets_a_viewer_token_for_the_live_room(self):
        series = self._live_series()
        attempt_id = self.start(series).json()["id"]
        with _live_patches() as patched:
            res = _client(self.student).post(reverse("testseries-attempt-live-token", kwargs={"pk": attempt_id}))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["live"]["token"], "jwt-token")
        self.assertEqual(patched["issue_token"].call_args.kwargs["role"], "viewer")  # subscribe-only

    def test_proctored_attempt_starts_one_recording_only(self):
        series = _series(self.teacher, proctoring="camera")
        attempt_id = self.start(series).json()["id"]
        url = reverse("testseries-attempt-live-token", kwargs={"pk": attempt_id})
        with _live_patches() as patched:
            first = _client(self.student).post(url)
            second = _client(self.student).post(url)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(patched["start_recording"].call_count, 1)
        self.assertEqual(patched["issue_token"].call_args.kwargs["role"], "candidate")  # publish-only
        self.assertEqual(TestRecording.objects.filter(attempt_id=attempt_id, kind="proctor").count(), 1)

    def test_submitting_a_proctored_attempt_closes_its_room(self):
        series = _series(self.teacher, proctoring="camera")
        attempt_id = self.start(series).json()["id"]
        with _live_patches() as patched:
            _client(self.student).post(reverse("testseries-attempt-live-token", kwargs={"pk": attempt_id}))
            self.submit(attempt_id, _answers(series, ["a", "a"]))
            self.assertEqual(patched["stop_recording"].call_count, 1)
            self.assertEqual(patched["end_room"].call_count, 1)

    def test_no_live_video_on_a_plain_test(self):
        series = _series(self.teacher)
        attempt_id = self.start(series).json()["id"]
        with _live_patches():
            res = _client(self.student).post(reverse("testseries-attempt-live-token", kwargs={"pk": attempt_id}))
        self.assertEqual(res.status_code, 400)

    def test_proctor_events_are_counted_and_visible_only_to_the_creator(self):
        series = _series(self.teacher, proctoring="camera")
        attempt_id = self.start(series).json()["id"]
        client = _client(self.student)
        url = reverse("testseries-attempt-proctor-events", kwargs={"pk": attempt_id})
        for kind in ("tab_switch", "face_missing", "tab_switch"):
            self.assertEqual(client.post(url, {"event_type": kind}, format="json").status_code, 201)
        self.assertEqual(client.post(url, {"event_type": "bogus"}, format="json").status_code, 400)

        self.assertEqual(TestAttempt.objects.get(pk=attempt_id).integrity_flags, 3)
        integrity = reverse("testseries-attempt-integrity", kwargs={"pk": attempt_id})
        self.assertEqual(client.get(integrity).status_code, 403)
        report = _client(self.teacher).get(integrity).json()
        self.assertEqual(report["summary"], {"tab_switch": 2, "face_missing": 1})

    def test_students_only_see_ready_live_recordings(self):
        series = self._live_series()
        TestRecording.objects.create(series=series, kind="live_session", room_name="r", egress_id="e1", status="ready", url="https://x/1.mp4")
        TestRecording.objects.create(series=series, kind="live_session", room_name="r", egress_id="e2", status="recording")
        url = reverse("testseries-recordings", kwargs={"pk": series.id})
        self.assertEqual(_client(self.student).get(url).status_code, 403)  # must have attempted
        self.start(series)
        rows = _client(self.student).get(url).json()
        self.assertEqual([r["status"] for r in rows], ["ready"])
        self.assertEqual(len(_client(self.teacher).get(url).json()), 2)

    def test_webhook_marks_the_recording_ready(self):
        series = self._live_series()
        recording = TestRecording.objects.create(series=series, kind="live_session", room_name="r", egress_id="EG1")
        event = SimpleNamespace(
            event="egress_ended",
            egress_info=SimpleNamespace(
                egress_id="EG1", file_results=[SimpleNamespace(location="https://s3/x.mp4", duration=90_000_000_000)]
            ),
        )
        with mock.patch("testseries.views_advanced.live.verify_webhook", return_value=event):
            res = _client().post(reverse("testseries-livekit-webhook"), b"{}", content_type="application/json")
        self.assertEqual(res.status_code, 200)
        recording.refresh_from_db()
        self.assertEqual((recording.status, recording.url, recording.duration_seconds), ("ready", "https://s3/x.mp4", 90))

    def test_webhook_ignores_foreign_egress_ids(self):
        event = SimpleNamespace(event="egress_ended", egress_info=SimpleNamespace(egress_id="NOT_OURS", file_results=[]))
        with mock.patch("testseries.views_advanced.live.verify_webhook", return_value=event):
            res = _client().post(reverse("testseries-livekit-webhook"), b"{}", content_type="application/json")
        self.assertEqual(res.status_code, 200)

    def test_stale_recordings_are_failed(self):
        series = self._live_series()
        old = TestRecording.objects.create(series=series, kind="live_session", room_name="r", egress_id="E_OLD")
        TestRecording.objects.filter(pk=old.pk).update(started_at=timezone.now() - timedelta(hours=10))
        self.assertEqual(tasks.expire_stale_recordings(), 1)
        old.refresh_from_db()
        self.assertEqual(old.status, "failed")
