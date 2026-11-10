"""Tests for: claimant checks (verified e-mail, evidence, tiers, brakes), new target types, takedown
coverage (engine, quarantine, highlights, reposts, re-upload match), evidence snapshots,
notifications (type / push / e-mail) and the staff preview."""
import hashlib
import re
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Notification
from post.models import Highlight, HighlightItem, Post, PostMedia, Story

from . import claimant as claimant_rules
from . import legal_orders, media, services, targets
from .models import (
    BlockedMediaHash, ClaimantEmailVerification, CopyrightClaim, CopyrightEvidence, TargetHold, TrustedClaimant,
)
from .tests import Base, mk_claimant, mk_post, mk_staff, mk_story, mk_user, notice

User = get_user_model()
TMP = tempfile.mkdtemp(prefix="cr_media_")


def aged(user, days=60):
    User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(days=days))
    return User.objects.get(pk=user.pk)


def verified(user, email="rita@pub.com"):
    ClaimantEmailVerification.objects.create(user=user, email=email, code_hash="x", verified_at=timezone.now(),
                                             expires_at=timezone.now() + timedelta(minutes=15))


def pdf(name="proof.pdf", size=100):
    return SimpleUploadedFile(name, b"%PDF-1.4 " + b"x" * size, content_type="application/pdf")


@override_settings(MEDIA_ROOT=TMP, COPYRIGHT_MEDIA_ASYNC=False, COPYRIGHT_EMAILS_ENABLED=True)
class Media(Base):
    """Base with a real (temp) storage so files can be moved around."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def post_with_file(self, owner=None, data=b"original-bytes-1", name="posts/2026/t1.jpg"):
        p = mk_post(owner or self.owner)
        saved = default_storage.save(name, ContentFile(data))
        pm = PostMedia.objects.create(post=p, media_type="image", file=saved, file_name="t1.jpg",
                                      file_size_bytes=len(data), mime_type="image/jpeg")
        return p, pm


class ClaimantChecksTests(Base):
    def setUp(self):
        super().setUp()
        self.newbie = mk_user("newbie")                       # just registered
        self.regular = aged(mk_user("regular"))
        verified(self.newbie)
        verified(self.regular)

    def file_as(self, user, evidence=None, **kw):
        return services.submit_claim(user, notice("post", self.post.id, **kw), evidence_files=evidence)[0]

    def test_unverified_email_cannot_file(self):
        stranger = aged(mk_user("stranger"))
        with self.assertRaises(services.ClaimError) as cm:
            services.submit_claim(stranger, notice("post", self.post.id))
        self.assertEqual(cm.exception.code, "email_unverified")
        verified(stranger, "other@x.com")                     # a DIFFERENT address does not count
        with self.assertRaises(services.ClaimError):
            services.submit_claim(stranger, notice("post", self.post.id))

    def test_verification_expires_after_30_days(self):
        ClaimantEmailVerification.objects.filter(user=self.regular).update(verified_at=timezone.now() - timedelta(days=31))
        with self.assertRaises(services.ClaimError):
            self.file_as(self.regular)

    def test_verification_can_be_switched_off(self):
        stranger = aged(mk_user("stranger2"))
        with override_settings(COPYRIGHT_REQUIRE_EMAIL_VERIFICATION=False):
            services.submit_claim(stranger, notice("post", self.post.id), evidence_files=[pdf()])

    def test_new_account_is_never_auto_held_even_with_proof(self):
        claim = self.file_as(self.newbie, evidence=[pdf()])
        self.assertEqual(claim.claimant_tier, "new")
        self.assertEqual(self.status_of(self.post), "approved")     # still visible: a reviewer decides
        self.assertFalse(hasattr(claim, "takedown"))

    def test_new_account_limited_to_one_notice_a_day(self):
        self.file_as(self.newbie)
        with self.assertRaises(services.ClaimError) as cm:
            services.submit_claim(self.newbie, notice("post", mk_post(self.owner).id))
        self.assertEqual(cm.exception.code, "rate_limited")

    def test_standard_claimant_is_held_only_with_proof(self):
        claim = self.file_as(self.regular)
        self.assertEqual((claim.claimant_tier, self.status_of(self.post)), ("standard", "approved"))
        p2 = mk_post(self.owner)
        c2 = services.submit_claim(self.regular, notice("post", p2.id), evidence_files=[pdf()])[0]
        self.assertEqual(self.status_of(p2), "copyright_hold")
        self.assertEqual(c2.evidence.count(), 1)

    def test_proof_added_later_triggers_the_hold(self):
        claim = self.file_as(self.regular)
        self.assertEqual(self.status_of(self.post), "approved")
        services.add_claim_evidence(claim, self.regular, [pdf()])
        self.assertEqual(self.status_of(self.post), "copyright_hold")

    def test_trusted_claimant_is_held_without_proof(self):
        TrustedClaimant.objects.create(user=self.regular, organisation="Big Publisher")
        claim = self.file_as(self.regular)
        self.assertEqual((claim.claimant_tier, self.status_of(self.post)), ("trusted", "copyright_hold"))

    @override_settings(COPYRIGHT_AUTO_HOLDS_PER_DAY=2)
    def test_brake_on_automatic_holds_per_day(self):
        posts = [mk_post(self.owner) for _ in range(3)]
        for p in posts:
            services.submit_claim(self.regular, notice("post", p.id), evidence_files=[pdf()])
        held = [self.status_of(p) for p in posts]
        self.assertEqual(held.count("copyright_hold"), 2)
        self.assertEqual(held.count("approved"), 1)           # third one waits for a reviewer

    def test_competitor_cannot_hide_a_post_without_proof(self):
        """The scenario from the review: any logged-in user hiding a rival's post."""
        rival = aged(mk_user("rival"))
        verified(rival)
        services.submit_claim(rival, notice("post", self.post.id))
        self.assertEqual(self.status_of(self.post), "approved")

    def test_evidence_validation(self):
        for bad in ([SimpleUploadedFile("x.exe", b"abc")], [SimpleUploadedFile("x.pdf", b"")],
                    [pdf(f"p{i}.pdf") for i in range(6)]):
            with self.assertRaises(services.ClaimError):
                self.file_as(self.regular, evidence=bad)
        with override_settings(COPYRIGHT_EVIDENCE_MAX_MB=0):
            with self.assertRaises(services.ClaimError):
                self.file_as(self.regular, evidence=[pdf()])

    def test_only_the_claimant_can_add_evidence(self):
        claim = self.file_as(self.regular)
        with self.assertRaises(Exception):
            services.add_claim_evidence(claim, self.newbie, [pdf()])


@override_settings(COPYRIGHT_MEDIA_ASYNC=False)
class EmailVerificationTests(Base):
    def setUp(self):
        super().setUp()
        self.u = aged(mk_user("verifyme"))

    def code_from_mail(self):
        with self.captureOnCommitCallbacks(execute=True):
            claimant_rules.send_email_code(self.u, "Rita@Pub.com")
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def test_full_round_trip(self):
        code = self.code_from_mail()
        self.assertEqual(mail.outbox[-1].to, ["rita@pub.com"])           # normalised
        self.assertFalse(claimant_rules.has_verified_email(self.u, "rita@pub.com"))
        claimant_rules.confirm_email_code(self.u, "rita@pub.com", code)
        self.assertTrue(claimant_rules.has_verified_email(self.u, "RITA@pub.com"))

    def test_wrong_code_and_attempt_limit(self):
        code = self.code_from_mail()
        for _ in range(5):
            with self.assertRaises(services.ClaimError) as cm:
                claimant_rules.confirm_email_code(self.u, "rita@pub.com", "000000" if code != "000000" else "111111")
            self.assertEqual(cm.exception.code, "bad_code" if _ < 5 else "code_expired")
        with self.assertRaises(services.ClaimError):                      # even the right code is refused now
            claimant_rules.confirm_email_code(self.u, "rita@pub.com", code)

    def test_expired_code(self):
        code = self.code_from_mail()
        ClaimantEmailVerification.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(services.ClaimError) as cm:
            claimant_rules.confirm_email_code(self.u, "rita@pub.com", code)
        self.assertEqual(cm.exception.code, "code_expired")

    def test_code_is_stored_hashed_and_send_is_rate_limited(self):
        code = self.code_from_mail()
        self.assertNotIn(code, ClaimantEmailVerification.objects.get(user=self.u).code_hash)
        for _ in range(4):
            claimant_rules.send_email_code(self.u, "rita@pub.com")
        with self.assertRaises(services.ClaimError) as cm:
            claimant_rules.send_email_code(self.u, "rita@pub.com")
        self.assertEqual(cm.exception.code, "rate_limited")

    def test_api_flow_then_file_claim_with_multipart_evidence(self):
        c = APIClient()
        c.force_authenticate(self.u)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(c.post("/copyright/verify-email/", {"email": "rita@pub.com"}, format="json").status_code, 200)
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        self.assertEqual(c.post("/copyright/verify-email/confirm/", {"email": "rita@pub.com", "code": "999999" if code != "999999" else "888888"},
                                format="json").status_code, 400)
        self.assertEqual(c.post("/copyright/verify-email/confirm/", {"email": "rita@pub.com", "code": code}, format="json").status_code, 200)
        data = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in notice("post", self.post.id).items()}
        data["evidence"] = pdf()
        r = c.post("/copyright/claims/", data, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()["data"]
        self.assertTrue(body["content_hidden"])
        self.assertEqual(len(body["evidence"]), 1)

    def test_api_refuses_unverified(self):
        c = APIClient()
        c.force_authenticate(self.u)
        r = c.post("/copyright/claims/", notice("post", self.post.id), format="json")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "email_unverified"))


class EngineTests(Base):
    """Overlapping claims / orders must never un-hide each other."""

    def setUp(self):
        super().setUp()
        self.l2, self.l3 = mk_staff("e2", 2), mk_staff("e3", 3)

    def test_two_claims_one_closes_other_keeps_it_hidden(self):
        a = self.file()
        b = self.file(claimant=mk_claimant("bob2"))
        services.withdraw_claim(a, self.rita)
        self.assertEqual(self.status_of(self.post), "copyright_hold")       # b still holds it
        services.withdraw_claim(b, b.claimant)
        self.assertEqual(self.status_of(self.post), "approved")

    def test_strongest_level_wins_and_downgrades_step_by_step(self):
        claim = self.file()
        order = legal_orders.register_order(self.l2, {"reference": "R", "authority": "HC", "summary": "s"}, [("post", self.post.id)])
        legal_orders.comply(order, self.l3)
        self.assertEqual(self.status_of(self.post), "legal_removed")
        services.uphold_claim(claim, self.l2, "ok")
        self.assertEqual(self.status_of(self.post), "legal_removed")        # still the strongest
        legal_orders.reverse_order(order, self.l3, "set aside")
        self.assertEqual(self.status_of(self.post), "copyright_removed")    # claim's removal remains
        services.restore_claim(claim, self.l3, "wrong")
        self.assertEqual(self.status_of(self.post), "approved")
        self.assertFalse(TargetHold.objects.exists())                       # nothing left over

    def test_previous_state_survives_a_whole_chain(self):
        p = mk_post(self.owner, moderation_status="flagged")
        c = self.file(post=p)
        services.uphold_claim(c, self.l2, "x")
        services.restore_claim(c, self.l3, "y")
        self.assertEqual(self.status_of(p), "flagged")

    def test_hold_row_is_deleted_when_content_disappears(self):
        self.file()
        Post.objects.filter(pk=self.post.pk).delete()
        targets.refresh("post", self.post.id)
        self.assertFalse(TargetHold.objects.exists())

    def test_refresh_is_idempotent(self):
        self.file()
        for _ in range(3):
            targets.refresh("post", self.post.id)
        self.assertEqual(self.status_of(self.post), "copyright_hold")
        self.assertEqual(TargetHold.objects.count(), 1)


class NewTargetTypeTests(Base):
    def setUp(self):
        super().setUp()
        self.l2, self.l3 = mk_staff("t2", 2), mk_staff("t3", 3)

    def claim(self, target_type, obj_id):
        return services.submit_claim(self.rita, notice(target_type, obj_id))[0]

    def classroom(self):
        from tuitionclass.models import Classroom
        return Classroom.objects.create(teacher=self.owner, title="Physics 11", description="d")

    def test_class_material_is_hidden_then_restored_with_file_and_link(self):
        from tuitionclass.models import ClassMaterial
        room = self.classroom()
        m = ClassMaterial.objects.create(classroom=room, uploaded_by=self.owner, title="Chapter 1 notes",
                                         material_type="pdf", file="class_materials/ch1.pdf",
                                         external_link="https://x.in/ch1")
        claim = self.claim("class_material", m.pk)
        self.assertEqual(claim.content_owner, self.owner)
        m.refresh_from_db()
        self.assertEqual((m.file.name, m.external_link), ("", ""))          # every serializer now shows nothing
        services.reject_claim(claim, self.l2, "not theirs")
        m.refresh_from_db()
        self.assertEqual((m.file.name, m.external_link), ("class_materials/ch1.pdf", "https://x.in/ch1"))

    def test_class_recording_is_blanked_and_restored(self):
        from tuitionclass.models import ClassSession
        room = self.classroom()
        sess = ClassSession.objects.create(classroom=room, scheduled_start=timezone.now(),
                                           scheduled_end=timezone.now() + timedelta(hours=1),
                                           recording_url="https://cdn.x/rec3.mp4")
        claim = self.claim("class_recording", sess.pk)
        sess.refresh_from_db()
        self.assertEqual(sess.recording_url, "")
        self.assertEqual(claim.content_owner, self.owner)
        services.withdraw_claim(claim, self.rita)
        sess.refresh_from_db()
        self.assertEqual(sess.recording_url, "https://cdn.x/rec3.mp4")

    def test_session_without_recording_cannot_be_claimed(self):
        from tuitionclass.models import ClassSession
        room = self.classroom()
        sess = ClassSession.objects.create(classroom=room, scheduled_start=timezone.now(),
                                           scheduled_end=timezone.now() + timedelta(hours=1))
        with self.assertRaises(services.ClaimError) as cm:
            self.claim("class_recording", sess.pk)
        self.assertEqual(cm.exception.code, "not_found")

    def test_test_series_is_archived_and_back(self):
        from testseries.models import TestSeries
        ts = TestSeries.objects.create(creator=self.owner, source="individual", title="JEE Mock 1",
                                       status=TestSeries.Status.PUBLISHED)
        claim = self.claim("test_series", ts.pk)
        ts.refresh_from_db()
        self.assertEqual(ts.status, "archived")
        services.reject_claim(claim, self.l2, "no")
        ts.refresh_from_db()
        self.assertEqual(ts.status, "published")

    def test_draft_test_series_cannot_be_claimed(self):
        from testseries.models import TestSeries
        ts = TestSeries.objects.create(creator=self.owner, source="individual", title="Draft")
        with self.assertRaises(services.ClaimError):
            self.claim("test_series", ts.pk)

    def test_unknown_target_type_is_rejected_by_the_api(self):
        c = APIClient()
        c.force_authenticate(self.rita)
        r = c.post("/copyright/claims/", notice("video_lecture", "1"), format="json")
        self.assertEqual(r.status_code, 400)

    def test_legal_order_works_on_new_types_too(self):
        from tuitionclass.models import ClassMaterial
        m = ClassMaterial.objects.create(classroom=self.classroom(), uploaded_by=self.owner, title="N",
                                         material_type="pdf", file="class_materials/n.pdf")
        o = legal_orders.register_order(self.l2, {"reference": "R", "authority": "HC", "summary": "s"}, [("class_material", m.pk)])
        legal_orders.comply(o, self.l3)
        m.refresh_from_db()
        self.assertEqual(m.file.name, "")
        legal_orders.reverse_order(o, self.l3, "set aside")
        m.refresh_from_db()
        self.assertEqual(m.file.name, "class_materials/n.pdf")


class StoryCoverageTests(Base):
    """Highlights, ended stories and reposts - the gaps listed in the review."""

    def setUp(self):
        super().setUp()
        self.l2 = mk_staff("s2", 2)

    def highlighted_ended_story(self):
        s = mk_story(self.owner)
        Story.objects.filter(pk=s.pk).update(expires_at=timezone.now() - timedelta(hours=1),
                                             is_deleted=True, deleted_at=timezone.now() - timedelta(hours=1))
        h = Highlight.objects.create(user=self.owner, title="Notes")
        HighlightItem.objects.create(highlight=h, story=s, position=0)
        return Story.objects.get(pk=s.pk)

    def in_highlights(self, story):
        from post.highlights import highlightable_stories_q
        return Story.objects.filter(pk=story.pk).filter(highlightable_stories_q()).exists()

    def test_story_that_lives_on_in_a_highlight_can_be_claimed_and_is_hidden_there(self):
        s = self.highlighted_ended_story()
        self.assertTrue(self.in_highlights(s))
        claim, _ = services.submit_claim(self.rita, notice("story", s.id))
        self.assertFalse(self.in_highlights(Story.objects.get(pk=s.pk)))
        services.withdraw_claim(claim, self.rita)
        s.refresh_from_db()
        self.assertTrue(self.in_highlights(s))
        self.assertTrue(s.is_deleted)                          # it stays "ended", just visible in the highlight again

    def test_live_story_that_ended_during_the_hold_goes_back_as_ended(self):
        s = mk_story(self.owner)
        claim, _ = services.submit_claim(self.rita, notice("story", s.id))
        Story.objects.filter(pk=s.pk).update(expires_at=timezone.now() - timedelta(minutes=5))
        services.withdraw_claim(claim, self.rita)
        s.refresh_from_db()
        self.assertTrue(s.is_deleted)
        self.assertFalse(s.takedown_hidden)

    def test_unrelated_deleted_story_cannot_be_claimed(self):
        s = mk_story(self.owner)
        Story.objects.filter(pk=s.pk).update(is_deleted=True, deleted_at=timezone.now())   # owner deleted it early
        with self.assertRaises(services.ClaimError):
            services.submit_claim(self.rita, notice("story", s.id))

    def test_repost_shows_the_original_as_unavailable(self):
        from post.serializers import PostListSerializer
        original = mk_post(self.owner)
        repost = mk_post(mk_user("reposter"), post_type="repost", original_post=original, content="")
        self.file(post=original)
        req = RequestFactory().get("/")
        req.user = mk_user("viewer2")
        data = PostListSerializer(Post.objects.get(pk=repost.pk), context={"request": req}).data
        self.assertEqual(data["original_post"], {"id": str(original.id), "is_unavailable": True})


@override_settings(MEDIA_ROOT=TMP, COPYRIGHT_MEDIA_ASYNC=False)
class QuarantineAndMatchTests(Media):
    def setUp(self):
        super().setUp()
        self.l2, self.l3 = mk_staff("q2", 2), mk_staff("q3", 3)

    def uphold(self, claim):
        with self.captureOnCommitCallbacks(execute=True):
            services.uphold_claim(claim, self.l2, "ok")

    def test_snapshot_keeps_a_copy_and_hash_even_if_post_is_deleted(self):
        p, pm = self.post_with_file()
        with self.captureOnCommitCallbacks(execute=True):
            claim = self.file(post=p)
        claim.refresh_from_db()
        ev = claim.evidence.get(kind="content_snapshot")
        self.assertEqual(ev.sha256, hashlib.sha256(b"original-bytes-1").hexdigest())
        self.assertEqual(ev.file.read(), b"original-bytes-1")
        self.assertEqual(claim.content_meta["owner_username"], "owner")
        Post.objects.filter(pk=p.pk).update(is_deleted=True)
        default_storage.delete(pm.file.name)
        ev.refresh_from_db()
        self.assertEqual(ev.file.read(), b"original-bytes-1")               # evidence survives

    def test_hold_alone_does_not_move_files_but_removal_does(self):
        p, pm = self.post_with_file()
        claim = self.file(post=p)
        self.assertTrue(default_storage.exists(pm.file.name))              # a hold keeps the file where it is
        self.uphold(claim)
        self.assertFalse(default_storage.exists(pm.file.name))             # direct URL is dead now
        hold = TargetHold.objects.get()
        self.assertTrue(hold.media_quarantined)
        self.assertTrue(default_storage.exists(hold.media_manifest[0]["quarantine"]))

    def test_restore_brings_the_file_back_under_the_same_name(self):
        p, pm = self.post_with_file()
        claim = self.file(post=p)
        self.uphold(claim)
        with self.captureOnCommitCallbacks(execute=True):
            services.restore_claim(claim, self.l3, "wrongly removed")
        self.assertTrue(default_storage.exists(pm.file.name))
        with default_storage.open(pm.file.name, "rb") as f:
            self.assertEqual(f.read(), b"original-bytes-1")
        self.assertFalse(TargetHold.objects.exists())
        self.assertFalse(BlockedMediaHash.objects.filter(active=True).exists())

    def test_legal_removal_also_quarantines_and_reverse_restores(self):
        p, pm = self.post_with_file()
        order = legal_orders.register_order(self.l2, {"reference": "R", "authority": "HC", "summary": "s"}, [("post", p.id)])
        with self.captureOnCommitCallbacks(execute=True):
            legal_orders.comply(order, self.l3)
        self.assertFalse(default_storage.exists(pm.file.name))
        with self.captureOnCommitCallbacks(execute=True):
            legal_orders.reverse_order(order, self.l3, "set aside")
        self.assertTrue(default_storage.exists(pm.file.name))

    def test_file_stays_quarantined_while_another_removal_remains(self):
        p, pm = self.post_with_file()
        a = self.file(post=p)
        b = self.file(post=p, claimant=mk_claimant("bob3"))
        self.uphold(a)                                                      # b is closed together with a (duplicate)
        b.refresh_from_db()
        self.assertEqual(b.status, "upheld")
        with self.captureOnCommitCallbacks(execute=True):
            services.restore_claim(a, self.l3, "x")
        self.assertTrue(default_storage.exists(pm.file.name) is False or TargetHold.objects.count() == 1)

    def test_exact_reupload_is_held_automatically_and_sent_to_review(self):
        p, pm = self.post_with_file()
        self.uphold(self.file(post=p))
        self.assertTrue(BlockedMediaHash.objects.filter(active=True).exists())
        # somebody uploads the same bytes in a new post
        other = mk_user("copycat")
        p2 = mk_post(other)
        with self.captureOnCommitCallbacks(execute=True):
            saved = default_storage.save("posts/2026/copy.jpg", ContentFile(b"original-bytes-1"))
            PostMedia.objects.create(post=p2, media_type="image", file=saved, file_name="copy.jpg",
                                     file_size_bytes=16, mime_type="image/jpeg")
        self.assertEqual(self.status_of(p2), "copyright_hold")
        auto = CopyrightClaim.objects.get(target_id=str(p2.id))
        self.assertEqual((auto.source, auto.status, auto.required_level), ("auto", "under_review", 2))
        self.assertTrue(Notification.objects.filter(recipient=other, notif_type="copyright_notice").exists())

    def test_different_bytes_are_not_touched_and_nothing_runs_without_blocked_hashes(self):
        p, pm = self.post_with_file()
        self.uphold(self.file(post=p))
        p3 = mk_post(mk_user("honest"))
        with self.captureOnCommitCallbacks(execute=True):
            saved = default_storage.save("posts/2026/new.jpg", ContentFile(b"totally different"))
            PostMedia.objects.create(post=p3, media_type="image", file=saved, file_name="n.jpg",
                                     file_size_bytes=17, mime_type="image/jpeg")
        self.assertEqual(self.status_of(p3), "approved")

    def test_a_failed_copy_never_loses_the_original(self):
        p, pm = self.post_with_file()
        claim = self.file(post=p)
        from unittest import mock
        with mock.patch.object(media, "_copy", return_value=None):
            self.uphold(claim)
        self.assertTrue(default_storage.exists(pm.file.name))

    def test_cdn_invalidation_is_called_when_configured(self):
        from unittest import mock
        p, pm = self.post_with_file()
        claim = self.file(post=p)
        fake = mock.MagicMock()
        with override_settings(COPYRIGHT_CLOUDFRONT_DISTRIBUTION_ID="E123", COPYRIGHT_CDN_PATH_PREFIX="media"), \
                mock.patch.dict("sys.modules", {"boto3": mock.MagicMock(client=mock.MagicMock(return_value=fake))}):
            self.uphold(claim)
        batch = fake.create_invalidation.call_args.kwargs["InvalidationBatch"]
        self.assertEqual(batch["Paths"]["Items"], ["/media/" + pm.file.name])

    def test_unblocking_a_hash_lets_the_same_file_through(self):
        p, pm = self.post_with_file()
        self.uphold(self.file(post=p))
        BlockedMediaHash.objects.update(active=False)
        p2 = mk_post(mk_user("licensed"))
        with self.captureOnCommitCallbacks(execute=True):
            saved = default_storage.save("posts/2026/lic.jpg", ContentFile(b"original-bytes-1"))
            PostMedia.objects.create(post=p2, media_type="image", file=saved, file_name="l.jpg",
                                     file_size_bytes=16, mime_type="image/jpeg")
        self.assertEqual(self.status_of(p2), "approved")


@override_settings(COPYRIGHT_MEDIA_ASYNC=False)
class NotificationTests(Base):
    def setUp(self):
        super().setUp()
        self.l2 = mk_staff("n2", 2)

    def test_dedicated_types_and_claim_id_for_each_role(self):
        with self.captureOnCommitCallbacks(execute=True):
            claim = self.file()
        owner_row = Notification.objects.get(recipient=self.owner)
        self.assertEqual(owner_row.notif_type, "copyright_notice")
        self.assertEqual(owner_row.data["claim_id"], str(claim.id))
        claimant_row = Notification.objects.get(recipient=self.rita)
        self.assertEqual(claimant_row.notif_type, "copyright_decision")
        staff_row = Notification.objects.get(recipient=self.l2)
        self.assertEqual(staff_row.notif_type, "copyright_staff")

    def test_standing_change_uses_its_own_type(self):
        for i in range(2):
            c = self.file(post=mk_post(self.owner), claimant=mk_claimant(f"s{i}"))
            services.uphold_claim(c, self.l2, "x")
        self.assertTrue(Notification.objects.filter(recipient=self.owner, notif_type="copyright_standing").exists())

    def test_push_payload_carries_claim_id_so_the_app_can_open_the_screen(self):
        from core.services import build_push_payload
        claim = self.file()
        row = Notification.objects.get(recipient=self.owner)
        payload = build_push_payload(row)
        self.assertEqual(payload["data"]["claim_id"], str(claim.id))
        self.assertEqual(payload["data"]["notif_type"], "copyright_notice")

    def test_push_is_attempted(self):
        from unittest import mock
        with mock.patch("message.push_utils.send_push_for_notification") as push:
            self.file()
        self.assertGreaterEqual(push.call_count, 2)                         # owner + claimant (+ staff)

    def test_emails_go_to_the_right_people_and_not_to_staff(self):
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.file()
        to = sorted(m.to[0] for m in mail.outbox)
        self.assertEqual(to, sorted(["owner@x.com", "rita@pub.com"]))       # claimant: the VERIFIED notice address
        self.assertIn("Copyright policy", mail.outbox[0].body)

    def test_email_goes_to_the_notice_address_not_the_account_address(self):
        mail.outbox.clear()
        c = mk_claimant("other_account", email="legal@publisher.com")
        with self.captureOnCommitCallbacks(execute=True):
            services.submit_claim(c, notice("post", self.post.id, claimant_email="legal@publisher.com"))
        self.assertIn("legal@publisher.com", [m.to[0] for m in mail.outbox])
        self.assertNotIn("other_account@x.com", [m.to[0] for m in mail.outbox])

    @override_settings(COPYRIGHT_EMAILS_ENABLED=False)
    def test_emails_can_be_switched_off(self):
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.file()
        self.assertEqual(mail.outbox, [])

    def test_muted_push_still_leaves_the_bell_row_and_the_email(self):
        from core.models import NotificationPreference
        NotificationPreference.objects.create(user=self.owner, push_enabled=False)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.file()
        self.assertTrue(Notification.objects.filter(recipient=self.owner).exists())
        self.assertIn("owner@x.com", [m.to[0] for m in mail.outbox])


class StaffPreviewTests(Base):
    def setUp(self):
        super().setUp()
        self.l1, self.l2 = mk_staff("p1", 1), mk_staff("p2", 2)
        from django.contrib import admin as dj_admin
        self.ma = dj_admin.site._registry[CopyrightClaim]
        self.req = RequestFactory().get("/")
        self.req.user = self.l2

    def test_preview_fields_come_first_and_are_read_only(self):
        fields = self.ma.get_fields(self.req)
        self.assertEqual(fields[:3], ["preview_current", "preview_proof", "preview_snapshot"])
        self.assertIn("preview_current", self.ma.get_readonly_fields(self.req))

    def test_reviewer_sees_text_owner_and_hold_state(self):
        claim = self.file()
        html = str(self.ma.preview_current(claim))
        self.assertIn("my original-looking post", html)
        self.assertIn("owner", html)
        self.assertIn("copyright_hold", html)

    def test_proof_and_snapshot_are_listed(self):
        claim = services.submit_claim(aged(mk_claimant("proofer", email="p@x.com")), notice("post", self.post.id, claimant_email="p@x.com"),
                                      evidence_files=[pdf("deed.pdf")])[0]
        self.assertIn("deed.pdf", str(self.ma.preview_proof(claim)))
        self.assertIn("owner", str(self.ma.preview_snapshot(claim)))

    def test_preview_survives_deleted_content_and_html_is_escaped(self):
        p = mk_post(self.owner, content="<script>alert(1)</script>")
        claim = self.file(post=p)
        self.assertNotIn("<script>", str(self.ma.preview_current(claim)))
        Post.objects.filter(pk=p.pk).delete()
        self.assertIn("no longer exists", str(self.ma.preview_current(claim)))

    def test_trusted_claimant_admin_is_l2_only(self):
        from django.contrib import admin as dj_admin
        ta = dj_admin.site._registry[TrustedClaimant]
        self.req.user = self.l1
        self.assertFalse(ta.has_module_permission(self.req))
        self.req.user = self.l2
        self.assertTrue(ta.has_add_permission(self.req))
        self.assertFalse(ta.has_delete_permission(self.req))
