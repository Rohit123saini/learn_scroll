"""Help & feedback. Run: python manage.py test support"""
import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from user_profile.models import AutoModerationFlag

from . import services
from .models import BugReport, FeatureRequest, FeatureVote, SupportMessage, SupportTicket

User = get_user_model()


def png_bytes(size=(20, 20)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


class Base(APITestCase):
    def setUp(self):
        cache.clear()  # throttle counters live in the cache
        self.me = User.objects.create_user(username="sup_me", password="x", first_name="Mia")
        self.other = User.objects.create_user(username="sup_other", password="x")
        self.staff = User.objects.create_user(username="sup_staff", password="x", is_staff=True)
        self.client.force_authenticate(self.me)


# ------------------------------------------------------------------ tickets
class TicketTests(Base):
    URL = "/support/tickets/"

    def _create(self, **kw):
        body = {"subject": "Coins missing", "category": "payment", "message": "I paid but coins did not arrive."}
        body.update(kw)
        return self.client.post(self.URL, body, format="json")

    def test_auth_required(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.URL).status_code, (401, 403))

    def test_create_makes_ticket_with_first_message(self):
        r = self._create()
        self.assertEqual(r.status_code, 201, r.data)
        t = SupportTicket.objects.get()
        self.assertEqual((t.user, t.status, t.category), (self.me, "open", "payment"))
        self.assertEqual(t.messages.count(), 1)
        self.assertFalse(t.messages.get().is_staff)
        self.assertEqual(len(r.data["messages"]), 1)

    def test_validation(self):
        self.assertEqual(self._create(subject="hi").status_code, 400)
        self.assertEqual(self._create(message="short").status_code, 400)
        self.assertEqual(self._create(category="nonsense").status_code, 400)
        self.assertEqual(SupportTicket.objects.count(), 0)

    def test_open_ticket_cap(self):
        for i in range(10):
            SupportTicket.objects.create(user=self.me, subject=f"t{i}")
        self.assertEqual(self._create().status_code, 429)

    def test_list_shows_only_mine(self):
        SupportTicket.objects.create(user=self.me, subject="mine")
        SupportTicket.objects.create(user=self.other, subject="theirs")
        r = self.client.get(self.URL)
        self.assertEqual([t["subject"] for t in r.data["results"]], ["mine"])

    def test_cannot_read_or_write_someone_elses_ticket(self):
        t = SupportTicket.objects.create(user=self.other, subject="theirs")
        self.assertEqual(self.client.get(f"{self.URL}{t.id}/").status_code, 404)
        self.assertEqual(self.client.post(f"{self.URL}{t.id}/messages/", {"body": "hi"}, format="json").status_code, 404)
        self.assertEqual(self.client.post(f"{self.URL}{t.id}/close/").status_code, 404)

    def test_reply_flow_and_reopen(self):
        t = SupportTicket.objects.get(pk=self._create().data["id"])
        services.add_staff_reply(t, self.staff, "We fixed it.")
        t.refresh_from_db()
        self.assertEqual((t.status, t.has_unread_reply), ("answered", True))

        r = self.client.get(f"{self.URL}{t.id}/")           # opening clears the unread flag
        self.assertEqual(r.status_code, 200)
        self.assertEqual([m["is_staff"] for m in r.data["messages"]], [False, True])
        self.assertEqual(r.data["messages"][1]["sender_label"], "LearnScroll Support")
        t.refresh_from_db()
        self.assertFalse(t.has_unread_reply)

        r = self.client.post(f"{self.URL}{t.id}/messages/", {"body": "Thanks, still not working"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        t.refresh_from_db()
        self.assertEqual(t.status, "open")                  # user reply reopens

    def test_closed_ticket_refuses_replies(self):
        t = SupportTicket.objects.get(pk=self._create().data["id"])
        self.assertEqual(self.client.post(f"{self.URL}{t.id}/close/").data["status"], "closed")
        r = self.client.post(f"{self.URL}{t.id}/messages/", {"body": "hello again"}, format="json")
        self.assertEqual(r.status_code, 409)

    def test_message_cap(self):
        t = SupportTicket.objects.create(user=self.me, subject="long")
        with mock.patch.object(services, "MAX_MESSAGES_PER_TICKET", 2):
            SupportMessage.objects.bulk_create([SupportMessage(ticket=t, sender=self.me, body="x") for _ in range(2)])
            r = self.client.post(f"{self.URL}{t.id}/messages/", {"body": "one more"}, format="json")
        self.assertEqual(r.status_code, 409)

    def test_empty_message_rejected(self):
        t = SupportTicket.objects.create(user=self.me, subject="s")
        self.assertEqual(self.client.post(f"{self.URL}{t.id}/messages/", {"body": "   "}, format="json").status_code, 400)

    def test_staff_reply_notifies_user_and_never_breaks_on_notify_failure(self):
        t = SupportTicket.objects.create(user=self.me, subject="s")
        with mock.patch("core.services.create_notification") as notify:
            services.add_staff_reply(t, self.staff, "Hello there")
        args = notify.call_args
        self.assertEqual(args.args[0], self.me.id)
        self.assertEqual(args.args[1], "support_reply")
        self.assertEqual(args.kwargs["data"], {"ticket_id": str(t.id)})

        with mock.patch("core.services.create_notification", side_effect=RuntimeError("boom")):
            services.add_staff_reply(t, self.staff, "Second reply")     # must not raise
        self.assertEqual(t.messages.filter(is_staff=True).count(), 2)

    def test_resolve_flag(self):
        t = SupportTicket.objects.create(user=self.me, subject="s")
        services.add_staff_reply(t, self.staff, "Done.", resolve=True)
        t.refresh_from_db()
        self.assertEqual(t.status, "resolved")


# ---------------------------------------------------------------- bug reports
class BugReportTests(Base):
    URL = "/support/bug-reports/"

    def _post(self, **extra):
        data = {"title": "Reels crash", "description": "App closes when I open reels.",
                "screen": "reels", "app_version": "1.4.2", "platform": "android", "device_info": "Pixel 7"}
        data.update(extra)
        return self.client.post(self.URL, data, format="multipart")

    def test_without_screenshot(self):
        r = self._post()
        self.assertEqual(r.status_code, 201, r.data)
        b = BugReport.objects.get()
        self.assertEqual((b.user, b.status, b.platform), (self.me, "new", "android"))
        self.assertIsNone(r.data["screenshot"])

    @override_settings(MEDIA_ROOT="/tmp/support_test_media")
    def test_with_png_screenshot(self):
        shot = SimpleUploadedFile("shot.png", png_bytes(), content_type="image/png")
        r = self._post(screenshot=shot)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(BugReport.objects.get().screenshot.name.startswith("support/bugs/"))
        self.assertTrue(r.data["screenshot"].startswith("http"))

    def test_non_image_and_bad_extension_rejected(self):
        bad = SimpleUploadedFile("shot.png", b"definitely not an image", content_type="image/png")
        self.assertEqual(self._post(screenshot=bad).status_code, 400)
        exe = SimpleUploadedFile("shot.exe", png_bytes(), content_type="image/png")
        self.assertEqual(self._post(screenshot=exe).status_code, 400)
        self.assertEqual(BugReport.objects.count(), 0)

    @override_settings(PROFILE_PHOTO_MAX_BYTES=100)
    def test_oversized_screenshot_rejected(self):
        big = SimpleUploadedFile("shot.png", png_bytes((300, 300)), content_type="image/png")
        self.assertEqual(self._post(screenshot=big).status_code, 400)

    def test_validation_and_list_is_mine_only(self):
        self.assertEqual(self._post(title="x").status_code, 400)
        self.assertEqual(self._post(description="short").status_code, 400)
        BugReport.objects.create(user=self.other, title="theirs", description="x" * 20)
        self._post()
        r = self.client.get(self.URL)
        self.assertEqual([b["title"] for b in r.data["results"]], ["Reels crash"])


# ----------------------------------------------------------- feature requests
class FeatureBoardTests(Base):
    URL = "/support/feature-requests/"

    def _create(self, title="Dark mode for the whiteboard", **kw):
        return self.client.post(self.URL, {"title": title, "description": "please", **kw}, format="json")

    def test_create_adds_authors_vote(self):
        r = self._create()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["votes_count"], r.data["has_voted"], r.data["is_mine"]), (1, True, True))
        self.assertEqual(r.data["author_name"], "Mia")

    def test_validation_and_duplicate_guard(self):
        self.assertEqual(self._create(title="hey").status_code, 400)
        self._create()
        self.assertEqual(self._create(title="DARK MODE FOR THE WHITEBOARD").status_code, 409)

    def test_vote_toggle_counts_stay_exact(self):
        fr = FeatureRequest.objects.create(author=self.other, title="Offline downloads for tests")
        url = f"{self.URL}{fr.id}/vote/"
        r = self.client.post(url)
        self.assertEqual((r.data["voted"], r.data["votes_count"]), (True, 1))
        r = self.client.post(url)
        self.assertEqual((r.data["voted"], r.data["votes_count"]), (False, 0))
        self.assertEqual(FeatureVote.objects.count(), 0)

    def test_two_users_two_votes_and_one_vote_each(self):
        fr = FeatureRequest.objects.create(author=self.other, title="Study groups with video")
        self.client.post(f"{self.URL}{fr.id}/vote/")
        self.client.force_authenticate(self.other)
        self.client.post(f"{self.URL}{fr.id}/vote/")
        fr.refresh_from_db()
        self.assertEqual(fr.votes_count, 2)
        with self.assertRaises(Exception):                       # DB-level unique constraint
            from django.db import transaction
            with transaction.atomic():
                FeatureVote.objects.create(request=fr, user=self.other)

    def test_count_recomputed_when_votes_are_deleted_outside_the_api(self):
        fr = FeatureRequest.objects.create(author=self.other, title="Something people want")
        FeatureVote.objects.create(request=fr, user=self.me)
        FeatureVote.objects.create(request=fr, user=self.other)
        fr.refresh_from_db()
        self.assertEqual(fr.votes_count, 2)
        FeatureVote.objects.filter(user=self.me).delete()        # e.g. admin / account deletion
        fr.refresh_from_db()
        self.assertEqual(fr.votes_count, 1)

    def test_vote_closed_for_shipped_and_declined(self):
        for st in ("shipped", "declined"):
            fr = FeatureRequest.objects.create(author=self.other, title=f"Feature {st}", status=st)
            self.assertEqual(self.client.post(f"{self.URL}{fr.id}/vote/").status_code, 409)

    def test_hidden_not_listed_and_not_votable(self):
        fr = FeatureRequest.objects.create(author=self.other, title="Hidden request", is_hidden=True)
        self.assertEqual(self.client.get(self.URL).data["results"], [])
        self.assertEqual(self.client.post(f"{self.URL}{fr.id}/vote/").status_code, 404)

    def test_sort_search_status_and_has_voted(self):
        a = FeatureRequest.objects.create(author=self.other, title="Alpha feature", votes_count=0)
        b = FeatureRequest.objects.create(author=self.other, title="Beta feature")
        FeatureVote.objects.create(request=b, user=self.other)
        FeatureVote.objects.create(request=b, user=self.me)
        top = [x["title"] for x in self.client.get(self.URL).data["results"]]
        self.assertEqual(top, ["Beta feature", "Alpha feature"])
        self.assertTrue(self.client.get(self.URL).data["results"][0]["has_voted"])
        self.assertEqual([x["title"] for x in self.client.get(self.URL, {"q": "alph"}).data["results"]], ["Alpha feature"])
        FeatureRequest.objects.filter(pk=a.pk).update(status="planned")
        self.assertEqual([x["title"] for x in self.client.get(self.URL, {"status": "planned"}).data["results"]],
                         ["Alpha feature"])
        self.assertEqual(len(self.client.get(self.URL, {"sort": "new"}).data["results"]), 2)

    def test_abusive_title_is_flagged_for_review_not_blocked(self):
        r = self._create(title="This app is shit honestly")
        self.assertEqual(r.status_code, 201)
        f = AutoModerationFlag.objects.get(target_type="feature")
        self.assertEqual((f.reason, f.target_id), ("profanity", r.data["id"]))
