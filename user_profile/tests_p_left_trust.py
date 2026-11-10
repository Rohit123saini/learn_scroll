"""P-LEFT (trust & safety leftovers) - tests. WRITTEN, NOT RUN. Run:
    python manage.py test user_profile.tests_p_left_trust
Covers: story review request (appeal) after a staff removal, and the reporter outcome notification for
study-room reports, classroom chat reports and live-class reports.
"""
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from copyrights.tests import mk_story, mk_user
from core.models import Notification
from user_profile import appeals
from user_profile import moderation_log as mlog
from user_profile.models import ModerationAction

User = get_user_model()


def _outcome_count(user):
    return Notification.objects.filter(recipient=user, notif_type="report_outcome").count()


class StoryReviewRequestTests(TestCase):
    def setUp(self):
        self.owner = mk_user("storyowner")
        self.story = mk_story(self.owner)
        self.staff = mk_user("storymod", is_staff=True)
        self.second_staff = mk_user("storymod2", is_staff=True)
        # what resolve_reports(action="hide") writes for a story (can_appeal is now True for stories)
        from post.models import Story

        Story.objects.filter(pk=self.story.pk).update(is_deleted=True)
        self.row = mlog.log_action(
            ModerationAction.Action.HIDE, target_type="story", target_id=self.story.pk, actor=self.staff,
            owner=self.owner, reason="spam", count=2, note="2 report(s) actioned", owner_visible=True, can_appeal=True)

    def test_owner_can_ask_for_a_review_of_a_removed_story(self):
        self.assertTrue(appeals.can_appeal(self.owner, self.row))
        ap = appeals.file_appeal(self.owner, self.row.pk, "This was my own class notes, not spam at all")
        self.assertEqual(ap.target_type, "story")

    def test_accepting_reverses_the_decision_but_never_undeletes_the_story(self):
        from post.models import Story

        ap = appeals.file_appeal(self.owner, self.row.pk, "This was my own class notes, not spam at all")
        appeals.decide_appeal(ap, self.second_staff, True, "mistake, sorry")
        self.assertTrue(Story.objects.get(pk=self.story.pk).is_deleted)  # stays down
        n = Notification.objects.filter(recipient=self.owner, title="We reviewed your story again")
        self.assertEqual(n.count(), 1)
        self.assertIn("post it again", n.first().message)
        self.assertTrue(ModerationAction.objects.filter(
            target_type="story", action="restore", note__icontains="cannot be re-published").exists())

    def test_rejecting_keeps_the_normal_message(self):
        ap = appeals.file_appeal(self.owner, self.row.pk, "This was my own class notes, not spam at all")
        appeals.decide_appeal(ap, self.second_staff, False, "it was spam")
        self.assertTrue(Notification.objects.filter(recipient=self.owner, title="We reviewed your appeal").exists())


class StudyRoomOutcomeTests(TestCase):
    def test_reporters_hear_back_when_staff_dismiss(self):
        from message.models import Conversation, ConversationType, StudyRoomReport

        reporter, reported = mk_user("srrep"), mk_user("srbad")
        staff = mk_user("srstaff", is_staff=True)
        conv = Conversation.objects.create(type=ConversationType.GROUP)
        rep = StudyRoomReport.objects.create(conversation=conv, reporter=reporter, reported_user=reported, reason="spam")
        client = APIClient()
        client.force_authenticate(staff)
        r = client.post(reverse("staff-study-room-report-action", args=[rep.id]), {"action": "dismiss"}, format="json")
        self.assertEqual(r.status_code, 200, getattr(r, "data", None))
        self.assertEqual(_outcome_count(reporter), 1)

    def test_a_plain_review_does_not_notify_yet(self):
        from message.models import Conversation, ConversationType, StudyRoomReport

        reporter, reported = mk_user("srrep2"), mk_user("srbad2")
        staff = mk_user("srstaff2", is_staff=True)
        conv = Conversation.objects.create(type=ConversationType.GROUP)
        rep = StudyRoomReport.objects.create(conversation=conv, reporter=reporter, reported_user=reported, reason="spam")
        client = APIClient()
        client.force_authenticate(staff)
        client.post(reverse("staff-study-room-report-action", args=[rep.id]), {"action": "review"}, format="json")
        self.assertEqual(_outcome_count(reporter), 0)


class LiveReportOutcomeTests(TestCase):
    def test_live_reporter_is_told(self):
        from tuitionclass.live_moderation import _tell_live_reporter

        reporter = mk_user("livereporter")
        _tell_live_reporter(SimpleNamespace(content_report_id=None, actor_id=reporter.pk, session_id=7), "dismissed")
        self.assertEqual(_outcome_count(reporter), 1)

    def test_no_actor_means_no_message_and_no_crash(self):
        from tuitionclass.live_moderation import _tell_live_reporter

        _tell_live_reporter(SimpleNamespace(content_report_id=None, actor_id=None, session_id=7), "actioned")


def _class_chat_base():
    from tuitionclass import tests as tc

    return tc.TuitionClassTestBase


class ClassChatOutcomeTests(_class_chat_base()):
    def setUp(self):
        super().setUp()
        from tuitionclass import tests as tc
        from tuitionclass.models import ChatMessage, ClassSession

        tc._charge_and_create_purchase(self.student, self.class_pass, coupon_code="")
        self.session = self.make_session(status_=ClassSession.Status.LIVE)
        self.message = ChatMessage.objects.create(session=self.session, sender=self.teacher, message="check this out")

    def _review(self, status_value):
        from tuitionclass.models import ChatMessageReport

        report = ChatMessageReport.objects.create(message=self.message, reporter=self.student, reason="spam")
        client = APIClient()
        client.force_authenticate(self.teacher)
        resp = client.post(reverse("chatmessagereport-review", args=[report.pk]), {"status": status_value}, format="json")
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", None))
        return report

    def test_reporter_hears_when_report_is_actioned(self):
        self._review("actioned")
        self.assertEqual(_outcome_count(self.student), 1)

    def test_reporter_hears_when_report_is_dismissed(self):
        self._review("dismissed")
        self.assertEqual(_outcome_count(self.student), 1)
