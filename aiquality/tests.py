"""P-F6 - AI quality loop tests. WRITTEN, NOT RUN:
    python manage.py test aiquality
"""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from copyrights.tests import mk_user

from . import dashboard, prompts, services, telemetry
from .models import AiCallLog, AiFeedback


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


class TelemetryTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_observe_logs_a_call_with_version_and_estimated_tokens(self):
        u = mk_user("aiq_t1")
        with telemetry.ai_context("doubt", "answer", u) as ctx:
            out = telemetry.observe(lambda: "hello world answer", prompt="What is 2+2?")
        self.assertEqual(out, "hello world answer")
        row = AiCallLog.objects.get()
        self.assertEqual((row.surface, row.feature, row.prompt_version, row.user_id), ("doubt", "answer", "v1", u.id))
        self.assertTrue(row.ok)
        self.assertTrue(row.tokens_estimated)
        self.assertEqual(len(row.prompt_hash), 12)
        self.assertEqual(ctx.meta()["call_id"], str(row.id))

    def test_real_usage_numbers_and_cost(self):
        class Usage:
            prompt_token_count = 1000
            candidates_token_count = 200
            thoughts_token_count = 100

        class Res:
            text = "ok"
            usage_metadata = Usage()

        with override_settings(AI_PRICE_INPUT_PER_M_USD=1.0, AI_PRICE_OUTPUT_PER_M_USD=2.0):
            with telemetry.ai_context("study_buddy", "quiz"):
                telemetry.observe(lambda: Res(), prompt="p")
        row = AiCallLog.objects.get()
        self.assertEqual((row.tokens_in, row.tokens_out, row.tokens_estimated), (1000, 300, False))
        self.assertEqual(row.cost_micro_usd, 1000 * 1 + 300 * 2)

    def test_failure_is_logged_and_reraised(self):
        def boom():
            raise ValueError("x")

        with telemetry.ai_context("copilot", "answer"):
            with self.assertRaises(ValueError):
                telemetry.observe(boom, prompt="p")
        row = AiCallLog.objects.get()
        self.assertFalse(row.ok)
        self.assertEqual(row.error_kind, "ValueError")
        self.assertEqual(row.cost_micro_usd, 0)

    def test_logging_error_never_breaks_the_answer(self):
        with mock.patch("aiquality.models.AiCallLog.objects.create", side_effect=RuntimeError("db down")):
            with telemetry.ai_context("doubt", "answer"):
                self.assertEqual(telemetry.observe(lambda: "fine", prompt="p"), "fine")

    def test_prompt_version_override_and_hash_ignores_whitespace(self):
        with override_settings(AI_PROMPT_VERSIONS={"doubt.answer": "v2"}):
            self.assertEqual(prompts.version_for("doubt", "answer"), "v2")
            self.assertEqual(prompts.version_for("doubt", "other"), "v1")
        self.assertEqual(telemetry.prompt_fingerprint("a  b\n c"), telemetry.prompt_fingerprint("a b c"))
        with override_settings(AI_PROMPT_VERSIONS={"doubt.answer": "bad version!"}):
            self.assertEqual(prompts.version_for("doubt", "answer"), "v1")

    def test_cache_hit_counter(self):
        with telemetry.ai_context("doubt", "answer") as ctx:
            telemetry.note_cache_hit()
        self.assertTrue(ctx.meta()["cached"])
        day = __import__("django.utils.timezone", fromlist=["x"]).localtime().strftime("%Y%m%d")
        self.assertEqual(telemetry.cache_hits("doubt", "answer", day), 1)

    def test_call_in_context_returns_ctx(self):
        res, ctx = telemetry.call_in_context("doubt", "answer", None, lambda x, y=0: x + y, 2, y=3)
        self.assertEqual(res, 5)
        self.assertEqual(ctx.surface, "doubt")


class FeedbackApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = mk_user("aiq_fb1")
        self.c = _client(self.u)
        self.url = reverse("ai-feedback")

    def _post(self, **kw):
        body = {"surface": "doubt", "feature": "answer", "ref_id": "post:1", "rating": "down"}
        body.update(kw)
        return self.c.post(self.url, body, format="json")

    def test_thumbs_down_saved_and_changed_not_duplicated(self):
        r = self._post(reason="unclear", comment="  hard   to read ")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["feedback"]["rating"], -1)
        r = self._post(rating="up")
        self.assertEqual(r.data["feedback"]["rating"], 1)
        self.assertEqual(AiFeedback.objects.count(), 1)
        fb = AiFeedback.objects.get()
        self.assertEqual((fb.rating, fb.reason, fb.comment), (1, "", ""))  # a 👍 carries no complaint

    def test_clear_thumb_deletes_row(self):
        self._post()
        r = self._post(rating="none")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["feedback"])
        self.assertEqual(AiFeedback.objects.count(), 0)

    def test_report_wrong_implies_thumbs_down_and_survives_clear(self):
        r = self._post(rating="none", wrong=True, comment="answer is 5 not 4")
        self.assertEqual(r.data["feedback"]["rating"], -1)
        self.assertTrue(r.data["feedback"]["reported_wrong"])
        self.assertEqual(AiFeedback.objects.get().reason, "wrong_answer")
        r = self._post(rating="none")  # clearing the thumb does not withdraw a report
        self.assertTrue(r.data["feedback"]["reported_wrong"])
        self.assertEqual(AiFeedback.objects.count(), 1)

    def test_thumbs_up_after_report_clears_the_report(self):
        self._post(wrong=True)
        self._post(rating="up")
        self.assertFalse(AiFeedback.objects.get().reported_wrong)

    def test_call_id_gives_server_side_prompt_version(self):
        call = AiCallLog.objects.create(surface="doubt", feature="answer", prompt_version="v7", model="m1")
        self._post(ref_id="", call_id=str(call.id), ai_meta={"prompt_version": "v1"})
        fb = AiFeedback.objects.get()
        self.assertEqual((fb.prompt_version, fb.model, fb.call_id), ("v7", "m1", call.id))

    def test_call_id_of_other_feature_is_ignored(self):
        call = AiCallLog.objects.create(surface="copilot", feature="answer", prompt_version="v7")
        self._post(ref_id="", call_id=str(call.id), excerpt="abc")
        self.assertIsNone(AiFeedback.objects.get().call_id)

    def test_validation(self):
        self.assertEqual(self._post(surface="smart_reply", feature="suggestions").status_code, 404)
        self.assertEqual(self._post(feature="nope").status_code, 404)
        self.assertEqual(self._post(rating="maybe").data["code"], "invalid_rating")
        self.assertEqual(self._post(reason="spam").data["code"], "invalid_reason")
        self.assertEqual(self._post(ref_id="a b/c").data["code"], "invalid_ref")

    def test_requires_login(self):
        self.assertIn(APIClient().post(self.url, {}, format="json").status_code, (401, 403))

    def test_mine_restores_state(self):
        self._post(rating="up")
        r = self.c.get(reverse("ai-feedback-mine"), {"surface": "doubt", "feature": "answer", "ref_id": "post:1"})
        self.assertEqual(r.data["feedback"]["rating"], 1)
        r = self.c.get(reverse("ai-feedback-mine"), {"surface": "doubt", "feature": "answer", "ref_id": "other"})
        self.assertIsNone(r.data["feedback"])

    @override_settings(FEATURE_FLAGS_OFF="ai_features")
    def test_feedback_works_while_ai_kill_switch_is_on(self):
        from common import feature_flags as ff

        ff.invalidate_cache()
        self.assertEqual(self._post().status_code, 200)


class AutoRemoveCachedResultTests(TestCase):
    def test_three_different_students_reporting_drops_the_cached_study_row(self):
        from post.models import Post, PostAiStudy

        owner = mk_user("aiq_own")
        post = Post.objects.create(user=owner, content="Newton's laws " * 5, visibility="public", moderation_status="approved", post_type="text", category="tech")
        row = PostAiStudy.objects.create(post=post, mode="quiz", language="en", source_hash="h", payload={"questions": []})
        for i in range(2):
            services.submit(mk_user(f"aiq_r{i}"), {"surface": "post_study", "feature": "quiz", "ref_id": str(row.id), "wrong": True})
            self.assertTrue(PostAiStudy.objects.filter(id=row.id).exists())
        _fb, removed = services.submit(mk_user("aiq_r9"), {"surface": "post_study", "feature": "quiz", "ref_id": str(row.id), "wrong": True})
        self.assertTrue(removed)
        self.assertFalse(PostAiStudy.objects.filter(id=row.id).exists())
        self.assertTrue(AiFeedback.objects.filter(staff_note__startswith="auto:").exists())

    @override_settings(AI_QUALITY_AUTO_REMOVE_REPORTS=0)
    def test_switch_off(self):
        from post.models import Post, PostAiStudy

        post = Post.objects.create(user=mk_user("aiq_own2"), content="x" * 40, visibility="public", moderation_status="approved", post_type="text", category="tech")
        row = PostAiStudy.objects.create(post=post, mode="quiz", language="en", source_hash="h", payload={})
        for i in range(4):
            services.submit(mk_user(f"aiq_s{i}"), {"surface": "post_study", "feature": "quiz", "ref_id": str(row.id), "wrong": True})
        self.assertTrue(PostAiStudy.objects.filter(id=row.id).exists())


class StaffApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = mk_user("aiq_staff", is_staff=True)
        self.user = mk_user("aiq_user")
        self.sc = _client(self.staff)
        for i in range(3):
            AiFeedback.objects.create(user=mk_user(f"aiq_x{i}"), surface="doubt", feature="answer", ref_id=f"r{i}",
                                      prompt_version="v1", rating=-1, dedupe_key=f"k{i}", reported_wrong=(i == 0))
        AiFeedback.objects.create(user=self.user, surface="doubt", feature="answer", ref_id="up", prompt_version="v1",
                                  rating=1, dedupe_key="kup")

    def test_non_staff_is_forbidden_everywhere(self):
        c = _client(self.user)
        for name in ("ai-quality-staff-feedback", "ai-quality-staff-summary", "ai-quality-staff-cost"):
            self.assertEqual(c.get(reverse(name)).status_code, 403, name)
        fb = AiFeedback.objects.first()
        self.assertEqual(c.patch(reverse("ai-quality-staff-feedback-detail", args=[fb.id]), {"status": "fixed"}, format="json").status_code, 403)

    def test_default_queue_is_new_thumbs_down_with_reports_first(self):
        r = self.sc.get(reverse("ai-quality-staff-feedback"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["results"]), 3)  # the 👍 is not in the default queue
        self.assertTrue(r.data["results"][0]["reported_wrong"])
        r = self.sc.get(reverse("ai-quality-staff-feedback"), {"only": "up", "status": "all"})
        self.assertEqual(len(r.data["results"]), 1)

    def test_paging(self):
        r = self.sc.get(reverse("ai-quality-staff-feedback"), {"limit": 2})
        self.assertEqual((len(r.data["results"]), r.data["has_more"], r.data["next_offset"]), (2, True, 2))

    def test_review_sets_status_note_and_reviewer(self):
        fb = AiFeedback.objects.filter(reported_wrong=True).first()
        r = self.sc.patch(reverse("ai-quality-staff-feedback-detail", args=[fb.id]),
                          {"status": "confirmed_wrong", "staff_note": "prompt missed units"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        fb.refresh_from_db()
        self.assertEqual((fb.status, fb.staff_note, fb.reviewed_by_id), ("confirmed_wrong", "prompt missed units", self.staff.id))
        self.assertIsNotNone(fb.reviewed_at)
        self.assertEqual(self.sc.get(reverse("ai-quality-staff-feedback")).data["results"].__len__(), 2)
        bad = self.sc.patch(reverse("ai-quality-staff-feedback-detail", args=[fb.id]), {"status": "zzz"}, format="json")
        self.assertEqual(bad.data["code"], "invalid_status")

    def test_new_complaint_reopens_a_reviewed_row(self):
        fb = AiFeedback.objects.get(ref_id="r1")
        services.review(fb, self.staff, status="dismissed")
        services.submit(fb.user, {"surface": "doubt", "feature": "answer", "ref_id": "r1", "rating": "down", "comment": "still wrong"})
        fb.refresh_from_db()
        self.assertEqual(fb.status, "new")

    @override_settings(AI_QUALITY_MIN_VOTES=3, AI_QUALITY_ALERT_DOWN_RATE=0.5)
    def test_summary_flags_bad_prompt_version(self):
        AiCallLog.objects.create(surface="doubt", feature="answer", prompt_version="v1")
        r = self.sc.get(reverse("ai-quality-staff-summary"))
        row = r.data["rows"][0]
        self.assertEqual((row["votes"], row["up"], row["down"], row["reported_wrong"]), (4, 1, 3, 1))
        self.assertEqual(row["down_rate"], 0.75)
        self.assertTrue(row["needs_attention"])
        self.assertEqual(row["model_calls"], 1)
        self.assertEqual(r.data["needs_attention"], 1)

    def test_cost_dashboard(self):
        AiCallLog.objects.create(surface="doubt", feature="answer", user=self.user, cost_micro_usd=2_000_000, tokens_in=10, tokens_out=5)
        AiCallLog.objects.create(surface="doubt", feature="answer", user=self.user, ok=False)
        with override_settings(AI_DAILY_BUDGET_USD=2.5):
            r = self.sc.get(reverse("ai-quality-staff-cost"))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["today"]["calls"], 2)
        self.assertEqual(r.data["today"]["failed"], 1)
        self.assertEqual(r.data["today"]["cost_usd"], 2.0)
        self.assertEqual(r.data["today"]["alert"], "warn")
        self.assertEqual(r.data["features"][0]["feature"], "answer")
        self.assertEqual(r.data["top_users"][0]["user_id"], self.user.id)

    def test_alert_levels(self):
        self.assertEqual(dashboard.alert_level(5, 0), "none")
        self.assertEqual(dashboard.alert_level(7.9, 10), "none")
        self.assertEqual(dashboard.alert_level(8, 10), "warn")
        self.assertEqual(dashboard.alert_level(10, 10), "over")


class TasksTests(TestCase):
    def test_prune_keeps_unreviewed_feedback(self):
        from datetime import timedelta

        from django.utils import timezone

        from . import tasks

        u = mk_user("aiq_prune")
        old = timezone.now() - timedelta(days=400)
        c = AiCallLog.objects.create(surface="doubt", feature="answer")
        a = AiFeedback.objects.create(user=u, surface="doubt", feature="answer", dedupe_key="a", status="new", rating=-1)
        b = AiFeedback.objects.create(user=u, surface="doubt", feature="answer", dedupe_key="b", status="fixed", rating=-1)
        AiCallLog.objects.filter(id=c.id).update(created_at=old)
        AiFeedback.objects.filter(id__in=[a.id, b.id]).update(created_at=old)
        out = tasks.prune_logs()
        self.assertEqual(out["calls_deleted"], 1)
        self.assertTrue(AiFeedback.objects.filter(id=a.id).exists())
        self.assertFalse(AiFeedback.objects.filter(id=b.id).exists())

    @override_settings(AI_DAILY_BUDGET_USD=1)
    def test_budget_alert_logged_once_a_day(self):
        from . import tasks

        cache.clear()
        AiCallLog.objects.create(surface="doubt", feature="answer", cost_micro_usd=1_500_000)
        with self.assertLogs("aiquality.tasks", level="CRITICAL") as cm:
            self.assertEqual(tasks.check_budget()["level"], "over")
        self.assertEqual(len(cm.records), 1)
        tasks.check_budget()  # second run the same day: silent (no new critical record)


class HooksTests(TestCase):
    """The existing AI endpoints hand back `ai_meta` and write a call-log row."""

    def setUp(self):
        cache.clear()
        self.u = mk_user("aiq_hook")
        self.c = _client(self.u)

    def test_ask_doubt_returns_ai_meta(self):
        with mock.patch("message.views_ai.AI_ENABLED", True), \
                mock.patch("message.views_ai.generate_doubt_answer", return_value="Because F=ma"):
            r = self.c.post("/message/ai/ask-doubt/", {"question": "Why?"}, format="json")
        self.assertEqual(r.status_code, 200, getattr(r, "data", None))
        self.assertEqual(r.data["answer"], "Because F=ma")
        self.assertEqual((r.data["ai_meta"]["surface"], r.data["ai_meta"]["feature"]), ("doubt", "answer"))

    def test_call_gemini_wrapper_logs_a_row(self):
        from message import ai_service

        class Res:
            text = "hi"

        with mock.patch.object(ai_service, "_call_gemini_raw", return_value=Res()):
            with telemetry.ai_context("doubt", "answer", self.u):
                ai_service._call_gemini("Explain gravity")
        self.assertEqual(AiCallLog.objects.filter(surface="doubt", ok=True).count(), 1)


class CacheKeyVersionTests(TestCase):
    """A prompt-version bump must miss the 24h answer caches (otherwise the old prompt's answer is served)."""

    def test_message_cache_key_changes_with_prompt_version(self):
        from message import ai_service

        base = ai_service._get_cache_key("doubt", "same content")
        self.assertEqual(base, ai_service._get_cache_key("doubt", "same content"))
        with override_settings(AI_PROMPT_VERSIONS={"doubt.answer": "v2"}):
            bumped = ai_service._get_cache_key("doubt", "same content")
            other = ai_service._get_cache_key("summary", "same content")
        self.assertNotEqual(base, bumped)
        self.assertIn(":v2:", bumped)
        self.assertIn(":v1:", other)  # another feature keeps its own version

    def test_study_buddy_modes_map_to_their_feature(self):
        self.assertEqual(prompts.cache_version("studybuddy_quiz_hi"), "v1")
        with override_settings(AI_PROMPT_VERSIONS={"study_buddy.quiz": "v3"}):
            self.assertEqual(prompts.cache_version("studybuddy_quiz_hi"), "v3")
            self.assertEqual(prompts.cache_version("studybuddy_flashcards_en"), "v1")
            self.assertEqual(prompts.cache_version("unknown_mode"), "v1")

    def test_composer_assist_key_changes_with_prompt_version(self):
        from post import ai_assist

        a = ai_assist.Prepared("caption", {"text": "x"}).key
        with override_settings(AI_PROMPT_VERSIONS={"post_assist.caption": "v2"}):
            b = ai_assist.Prepared("caption", {"text": "x"}).key
            c = ai_assist.Prepared("tags", {"text": "x"}).key
        self.assertNotEqual(a, b)
        self.assertTrue(c.split(":")[3] == "v1")


class RevisionDeckMetaTests(TestCase):
    def test_deck_json_carries_ref_id_and_ai_meta(self):
        import types
        from datetime import datetime

        from message import views_ai

        deck = types.SimpleNamespace(id="3f2b1c0e-0000-4000-8000-000000000001", flashcards=[{"q": 1}], quiz=[],
                                     created_at=datetime(2026, 11, 1, 10, 0, 0))
        meta = views_ai._cached_deck_meta(mk_user("aiq_deck"))
        body = views_ai._deck_json(deck, meta)
        self.assertEqual(body["ref_id"], body["id"])
        self.assertEqual((body["ai_meta"]["surface"], body["ai_meta"]["feature"]), ("revision_deck", "deck"))
        self.assertTrue(body["ai_meta"]["cached"])  # served without a model call
        self.assertIsNone(body["ai_meta"]["call_id"])
