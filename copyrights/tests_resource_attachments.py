"""P-F17: resource posts (PDF / DOC / lecture video = post.PostAttachment) are covered by the copyright flow.

Before this, only PostMedia was quarantined / fingerprinted / checked on upload, so a removed PDF could be
uploaded again as a resource. Written WITHOUT being run - run
    python manage.py test copyrights.tests_resource_attachments
"""
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings

from post.models import Post, PostAttachment, PostAttachmentVersion

from . import services
from .models import BlockedMediaHash, CopyrightClaim, TargetHold
from .tests import mk_post, mk_staff, mk_user
from .tests_hardening import TMP, Media

PDF_BYTES = b"%PDF-1.4 original-resource-bytes " + b"x" * 200


def add_attachment(post, data=PDF_BYTES, name="resources/2026/book.pdf"):
    saved = default_storage.save(name, ContentFile(data))
    return PostAttachment.objects.create(
        post=post, kind="pdf", file=saved, original_name="book.pdf", mime="application/pdf", size=len(data))


@override_settings(MEDIA_ROOT=TMP, COPYRIGHT_MEDIA_ASYNC=False)
class ResourceAttachmentTests(Media):
    def setUp(self):
        super().setUp()
        self.l2, self.l3 = mk_staff("ra2", 2), mk_staff("ra3", 3)

    def uphold(self, claim):
        with self.captureOnCommitCallbacks(execute=True):
            services.uphold_claim(claim, self.l2, "ok")

    def resource_post(self, owner=None):
        p = mk_post(owner or self.owner)
        return p, add_attachment(p)

    def test_removed_resource_pdf_is_quarantined_and_its_hash_blocked(self):
        p, att = self.resource_post()
        with self.captureOnCommitCallbacks(execute=True):
            claim = self.file(post=p)
        self.uphold(claim)
        self.assertFalse(default_storage.exists(att.file.name))          # direct link is dead
        hold = TargetHold.objects.get()
        self.assertTrue(hold.media_quarantined)
        self.assertTrue(BlockedMediaHash.objects.filter(active=True).exists())

    def test_older_versions_are_quarantined_too_and_restore_brings_everything_back(self):
        p, att = self.resource_post()
        old = default_storage.save("resources/2026/book_v1.pdf", ContentFile(b"%PDF-1.4 older " + b"y" * 120))
        PostAttachmentVersion.objects.create(
            attachment=att, version=1, file=old, original_name="book.pdf", mime="application/pdf", size=140)
        claim = self.file(post=p)
        self.uphold(claim)
        self.assertFalse(default_storage.exists(att.file.name))
        self.assertFalse(default_storage.exists(old))
        with self.captureOnCommitCallbacks(execute=True):
            services.restore_claim(claim, self.l3, "wrongly removed")
        self.assertTrue(default_storage.exists(att.file.name))
        self.assertTrue(default_storage.exists(old))
        self.assertFalse(BlockedMediaHash.objects.filter(active=True).exists())

    def test_same_pdf_uploaded_again_as_a_resource_is_held_for_review(self):
        p, att = self.resource_post()
        self.uphold(self.file(post=p))
        other = mk_user("pdfcopycat")
        p2 = mk_post(other)
        with self.captureOnCommitCallbacks(execute=True):
            add_attachment(p2, name="resources/2026/copy.pdf")
        self.assertEqual(Post.objects.get(pk=p2.pk).moderation_status, "copyright_hold")
        auto = CopyrightClaim.objects.get(target_id=str(p2.id))
        self.assertEqual((auto.source, auto.status, auto.required_level), ("auto", "under_review", 2))

    def test_other_pdf_is_left_alone(self):
        p, att = self.resource_post()
        self.uphold(self.file(post=p))
        p3 = mk_post(mk_user("honestpdf"))
        with self.captureOnCommitCallbacks(execute=True):
            add_attachment(p3, data=b"%PDF-1.4 something else " + b"z" * 300, name="resources/2026/new.pdf")
        self.assertEqual(Post.objects.get(pk=p3.pk).moderation_status, "approved")

    def test_replace_with_removed_content_is_checked_even_though_the_row_is_not_new(self):
        from .signals import queue_check

        p, att = self.resource_post()
        self.uphold(self.file(post=p))
        p2 = mk_post(mk_user("replacer"))
        att2 = add_attachment(p2, data=b"%PDF-1.4 innocent " + b"q" * 150, name="resources/2026/inno.pdf")
        # the author swaps in the removed bytes (post/resource_versions.py saves the same row, created=False)
        att2.file.save("swap.pdf", ContentFile(PDF_BYTES), save=True)
        with self.captureOnCommitCallbacks(execute=True):
            queue_check("post", p2.id, att2.file.name)
        self.assertEqual(Post.objects.get(pk=p2.pk).moderation_status, "copyright_hold")

    def test_preview_lists_the_attachment_for_staff(self):
        from . import targets

        p, att = self.resource_post()
        kinds = [k for k, _v in targets.spec_for("post").preview(p)]
        self.assertIn("file", kinds)
        names = targets.spec_for("post").media_names(p)
        self.assertEqual(names.count(att.file.name), 1)
