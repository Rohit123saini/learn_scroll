"""P-F15 (trust & safety completion) - tests. WRITTEN, NOT RUN. Run:
    python manage.py test user_profile.tests_p_f15_trust_safety
Covers: report SLA (deadline, tighten on hold, one-time breach sweep, staff queue order + permission), comment hold on
profanity / adult text (+ appeal restores and fixes comments_count), DM adult-text flag, the optional image/video
provider hook (hold / flag / low score / no provider / failure), reporter outcome helper for non-ContentReport systems.
"""

from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from copyrights.tests import mk_post, mk_user
from core.models import Notification
from post.models import Post, PostComment
from user_profile import appeals, report_sla
from user_profile.models import AutoModerationFlag, ContentReport, ModerationAction
from user_profile.report_automation import notify_outcome
from user_profile.services import file_report

User = get_user_model()


def old_users(prefix, n):
    us = [mk_user(f"{prefix}{i}") for i in range(n)]
    User.objects.filter(pk__in=[u.pk for u in us]).update(date_joined=timezone.now() - timedelta(days=90))
    return us


@override_settings(REPORT_SLA_HOURS_SEVERE=24, REPORT_SLA_HOURS_NORMAL=72, CHILD_SAFETY_SLA_HOURS=24)
class ReportSlaTests(TestCase):
    def setUp(self):
        self.author = mk_user("author")
        self.post = mk_post(self.author)
        self.reps = old_users("r", 8)

    def test_deadline_depends_on_reason(self):
        a, _ = file_report(self.reps[0], "post", self.post.id, "spam")
        b, _ = file_report(self.reps[1], "post", self.post.id, "nudity")
        now = timezone.now()
        self.assertAlmostEqual((a.sla_due_at - now).total_seconds() / 3600, 72, delta=0.1)
        self.assertAlmostEqual((b.sla_due_at - now).total_seconds() / 3600, 24, delta=0.1)

    def test_auto_hold_pulls_every_open_report_of_the_target_to_the_severe_deadline(self):
        first, _ = file_report(self.reps[0], "post", self.post.id, "spam")
        self.assertGreater(first.sla_due_at, timezone.now() + timedelta(hours=60))
        for r in self.reps[1:4]:
            file_report(r, "post", self.post.id, "nudity")  # 3 severe -> auto-hold
        first.refresh_from_db()
        self.assertLessEqual(first.sla_due_at, timezone.now() + timedelta(hours=24, minutes=1))

    def test_sweep_marks_breach_once_and_alerts_staff_once(self):
        staff = mk_user("mod", is_staff=True)
        rep, _ = file_report(self.reps[0], "post", self.post.id, "spam")
        ContentReport.objects.filter(pk=rep.pk).update(sla_due_at=timezone.now() - timedelta(hours=1))
        before = Notification.objects.filter(recipient=staff).count()
        self.assertEqual(report_sla.sweep(), 1)
        self.assertEqual(report_sla.sweep(), 0)  # stamped once
        rep.refresh_from_db()
        self.assertIsNotNone(rep.sla_breached_at)
        self.assertEqual(Notification.objects.filter(recipient=staff).count(), before + 1)

    def test_a_decided_report_is_never_breached(self):
        rep, _ = file_report(self.reps[0], "post", self.post.id, "spam")
        ContentReport.objects.filter(pk=rep.pk).update(sla_due_at=timezone.now() - timedelta(hours=1), status="dismissed")
        self.assertEqual(report_sla.sweep(), 0)

    def test_queue_puts_overdue_and_severe_first_and_hides_child_safety_from_plain_staff(self):
        other = mk_post(self.author, content="another post")
        file_report(self.reps[0], "post", self.post.id, "spam")                 # 72 h
        file_report(self.reps[1], "post", other.id, "nudity")                   # 24 h
        rows = report_sla.queue()
        self.assertEqual(rows[0]["target_id"], str(other.id))
        ContentReport.objects.filter(target_id=str(self.post.id)).update(sla_due_at=timezone.now() - timedelta(minutes=5))
        rows = report_sla.queue()
        self.assertEqual(rows[0]["target_id"], str(self.post.id))
        self.assertTrue(rows[0]["overdue"])
        self.assertNotIn("reporter", rows[0])
        file_report(self.reps[2], "post", mk_post(self.author, content="third").id, "child_safety")
        self.assertFalse(any("child_safety" in r["by_reason"] for r in report_sla.queue()))
        self.assertTrue(any("child_safety" in r["by_reason"] for r in report_sla.queue(include_child_safety=True)))

    def test_staff_api_is_staff_only(self):
        c = APIClient()
        c.force_authenticate(self.reps[0])
        self.assertEqual(c.get("/profile/staff/report-queue/").status_code, 403)
        staff = mk_user("mod", is_staff=True)
        c.force_authenticate(staff)
        file_report(self.reps[0], "post", self.post.id, "nudity")
        self.assertEqual(c.get("/profile/staff/report-queue/").json()["count"], 1)
        s = c.get("/profile/staff/report-queue/summary/").json()["data"]
        self.assertEqual((s["open"], s["overdue"]), (1, 0))


@override_settings(COMMENT_TEXT_ACTION="hold")
class CommentHoldTests(TestCase):
    def setUp(self):
        self.owner = mk_user("owner")
        self.writer = mk_user("writer")
        self.post = mk_post(self.owner)
        self.client = APIClient()
        self.client.force_authenticate(self.writer)

    def _comment(self, text):
        return self.client.post("/post/comment/create/", {"post_id": str(self.post.id), "content": text}, format="json")

    def test_profanity_comment_is_created_hidden_with_notice_and_not_counted(self):
        resp = self._comment("you are a bastard")
        self.assertEqual(resp.status_code, 201)
        c = PostComment.objects.get(user=self.writer)
        self.assertTrue(c.is_hidden)
        self.assertEqual(Post.objects.get(pk=self.post.pk).comments_count, 0)
        row = ModerationAction.objects.get(action="auto_hold", target_type="comment")
        self.assertEqual((row.target_owner_id, row.can_appeal, row.owner_visible), (self.writer.id, True, True))
        self.assertTrue(AutoModerationFlag.objects.filter(target_type="comment", target_id=str(c.pk)).exists())

    def test_nsfw_comment_is_held_too(self):
        self._comment("send nudes please")
        self.assertTrue(PostComment.objects.get(user=self.writer).is_hidden)

    def test_clean_comment_is_untouched_and_counted(self):
        self._comment("great explanation, thanks!")
        self.assertFalse(PostComment.objects.get(user=self.writer).is_hidden)
        self.assertEqual(Post.objects.get(pk=self.post.pk).comments_count, 1)

    def test_accepted_appeal_unhides_and_counts_it(self):
        self._comment("you are a bastard")
        c = PostComment.objects.get(user=self.writer)
        row = ModerationAction.objects.get(action="auto_hold", target_type="comment")
        ap = appeals.file_appeal(self.writer, row.pk, "I was quoting a line from a book we study")
        staff = mk_user("mod", is_staff=True)
        appeals.decide_appeal(ap, staff, True, "context is fine")
        c.refresh_from_db()
        self.assertFalse(c.is_hidden)
        self.assertEqual(Post.objects.get(pk=self.post.pk).comments_count, 1)

    @override_settings(COMMENT_TEXT_ACTION="flag")
    def test_flag_mode_keeps_the_old_behaviour(self):
        self._comment("you are a bastard")
        self.assertFalse(PostComment.objects.get(user=self.writer).is_hidden)
        self.assertTrue(AutoModerationFlag.objects.filter(target_type="comment", reason="profanity").exists())

    @override_settings(COMMENT_TEXT_ACTION="off")
    def test_off_mode_does_nothing(self):
        self._comment("you are a bastard")
        self.assertFalse(PostComment.objects.get(user=self.writer).is_hidden)
        self.assertFalse(AutoModerationFlag.objects.filter(target_type="comment").exists())


class DmAdultTextTests(TestCase):
    def test_adult_text_in_a_dm_is_flagged_not_hidden(self):
        from user_profile.automod import screen_and_flag

        u = mk_user("dmuser")
        flag = screen_and_flag(target_type="message", target_id="m1", user_id=u.pk, text="send nudes", surface="dm")
        self.assertEqual(flag.reason, "nsfw")
        self.assertEqual(flag.severity, "high")

    @override_settings(DM_NSFW_ACTION="off")
    def test_dm_nsfw_screen_can_be_switched_off(self):
        from user_profile.automod import screen_and_flag

        u = mk_user("dmuser2")
        self.assertIsNone(screen_and_flag(target_type="message", target_id="m2", user_id=u.pk, text="send nudes", surface="dm"))


def fake_provider_nsfw(name, file=None, path=None, kind="image"):
    return {"nsfw": True, "score": 0.97, "label": "porn"} if "bad" in name else None


def fake_provider_low(name, file=None, path=None, kind="image"):
    return {"nsfw": True, "score": 0.5, "label": "suggestive"}


def fake_provider_boom(name, file=None, path=None, kind="image"):
    raise RuntimeError("provider down")


class MediaHookTests(TestCase):
    def item(self, name):
        return SimpleNamespace(name=name, content_type="image/jpeg", declared_type="image", file=None, path="")

    @override_settings(MEDIA_NSFW_PROVIDER="")
    def test_no_provider_means_nothing_happens(self):
        from post.publish_gate import check_media

        self.assertEqual(check_media([self.item("bad.jpg")]), [])

    @override_settings(MEDIA_NSFW_PROVIDER="user_profile.tests_p_f15_trust_safety.fake_provider_nsfw", MEDIA_NSFW_ACTION="hold")
    def test_strong_hit_holds(self):
        from post.publish_gate import check_media

        flags = check_media([self.item("bad.jpg"), self.item("ok.jpg")])
        self.assertEqual([(f.reason, f.hold) for f in flags], [("nsfw_media", True)])

    @override_settings(MEDIA_NSFW_PROVIDER="user_profile.tests_p_f15_trust_safety.fake_provider_nsfw", MEDIA_NSFW_ACTION="flag")
    def test_flag_mode_never_holds(self):
        from post.publish_gate import check_media

        self.assertFalse(check_media([self.item("bad.jpg")])[0].hold)

    @override_settings(MEDIA_NSFW_PROVIDER="user_profile.tests_p_f15_trust_safety.fake_provider_low", MEDIA_NSFW_MIN_SCORE=0.85)
    def test_low_score_is_only_flagged(self):
        from post.publish_gate import check_media

        f = check_media([self.item("x.jpg")])[0]
        self.assertFalse(f.hold)

    @override_settings(MEDIA_NSFW_PROVIDER="user_profile.tests_p_f15_trust_safety.fake_provider_boom")
    def test_provider_error_fails_open(self):
        from post.publish_gate import check_media

        self.assertEqual(check_media([self.item("x.jpg")]), [])

    @override_settings(MEDIA_NSFW_PROVIDER="user_profile.tests_p_f15_trust_safety.fake_provider_nsfw", MEDIA_NSFW_ACTION="hold")
    def test_a_story_with_adult_media_is_refused_because_it_has_no_hold_state(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from post import publish_gate

        u = mk_user("storyuser")
        f = SimpleUploadedFile("bad.jpg", b"\xff\xd8\xff", content_type="image/jpeg")
        v = publish_gate.evaluate(u, "story", texts=["hello"], files=[f], media_types=["image"])
        self.assertFalse(v.ok)
        self.assertEqual((v.status, v.code), (422, "media_not_allowed"))


class OutcomeHelperTests(TestCase):
    def test_each_reporter_is_told_once(self):
        a, b = mk_user("a"), mk_user("b")
        n = notify_outcome([a, a.pk, b], "dismissed", "study_room", "conv1")
        self.assertEqual(n, 2)
        self.assertEqual(Notification.objects.filter(recipient=a, notif_type="report_outcome").count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=b, notif_type="report_outcome").count(), 1)


class SoundReportOutcomeTests(TestCase):
    def test_reporter_is_told_when_staff_dismiss_the_sound_reports(self):
        from post import sounds

        a, b = mk_user("sa"), mk_user("sb")
        sounds.report_sound(a, "12345", "copyright")
        sounds.report_sound(b, "12345", "copyright")
        self.assertEqual(sounds.dismiss_reports("12345"), 2)
        for u in (a, b):
            self.assertEqual(Notification.objects.filter(recipient=u, notif_type="report_outcome").count(), 1)

    def test_reporter_is_told_when_the_sound_is_blocked(self):
        from post import sounds

        a = mk_user("sc")
        staff = mk_user("smod", is_staff=True)
        sounds.report_sound(a, "777", "copyright")
        sounds.block_sound("777", by=staff, reason="copyright")
        self.assertEqual(Notification.objects.filter(recipient=a, notif_type="report_outcome").count(), 1)
