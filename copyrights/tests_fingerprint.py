"""Tests for automatic re-upload detection: perceptual fingerprints (copyrights/fingerprint.py) and the
synchronous exact-copy refusal (services.precheck_upload).

Written WITHOUT being run (the project owner asked for read-only review of this pass) - run
    python manage.py test copyrights.tests_fingerprint
once. Video / audio need ffmpeg / fpcalc, so those are covered through their pure comparison functions;
PDF tests are skipped when pypdf is not installed.
"""
import importlib.util
import io
import os
import random
import tempfile
from types import SimpleNamespace
from unittest import skipUnless

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from post.models import PostMedia

from . import fingerprint, services
from .models import CopyrightClaim, FingerprintBand, MediaFingerprint
from .tests import mk_post, mk_staff, mk_user
from .tests_hardening import TMP, Media


def make_image(seed, size=(320, 240)):
    from PIL import Image

    rnd = random.Random(seed)
    cells = Image.new("L", (16, 12))
    cells.putdata([rnd.randrange(256) for _ in range(16 * 12)])
    return cells.resize(size, Image.Resampling.NEAREST).convert("RGB")


def encode(img, fmt="PNG", **kw):
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


def write_tmp(data, suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


class ImageFingerprintTests(SimpleTestCase):
    def fp(self, img, fmt="PNG", registry=False, **kw):
        path = write_tmp(encode(img, fmt, **kw), "." + fmt.lower())
        try:
            return fingerprint.image_fingerprint(path, registry=registry)
        finally:
            os.unlink(path)

    def dist(self, a, b):
        return min(fingerprint._hamming(int(a.data["h"][0], 16), int(x, 16)) for x in b.data["h"])

    def test_resized_and_recompressed_copy_is_close_and_other_picture_is_far(self):
        from PIL import ImageOps

        orig = make_image(1)
        reg = self.fp(orig, registry=True)
        small_jpeg = self.fp(orig.resize((160, 120)), "JPEG", quality=55)
        self.assertLessEqual(self.dist(small_jpeg, reg), 5)
        mirrored = self.fp(ImageOps.mirror(orig))
        self.assertLessEqual(self.dist(mirrored, reg), 5)                  # flipped re-upload
        self.assertGreater(self.dist(self.fp(make_image(2)), reg), 5)      # different picture

    def test_flat_picture_has_no_fingerprint(self):
        from PIL import Image

        self.assertIsNone(self.fp(Image.new("RGB", (100, 100), (200, 200, 200))))

    def test_query_has_8_bands_registry_has_more(self):
        orig = make_image(3)
        self.assertEqual(len(self.fp(orig).bands), 8)
        self.assertGreater(len(self.fp(orig, registry=True).bands), 8)

    def test_compare_uses_the_configured_distance(self):
        a = fingerprint.Fingerprint("image", "dhash64", {"h": [f"{0:016x}"]}, [])
        near = SimpleNamespace(data={"h": [f"{0b1111:016x}"]})              # 4 bits apart
        self.assertIsNotNone(fingerprint._compare_image(a, near))
        with override_settings(COPYRIGHT_FP_IMAGE_MAX_DISTANCE=3):
            self.assertIsNone(fingerprint._compare_image(a, near))


class FrameAndAudioCompareTests(SimpleTestCase):
    def test_frames_match_when_enough_frames_are_close(self):
        rnd = random.Random(7)
        base = [rnd.getrandbits(64) for _ in range(12)]
        noisy = [h ^ 0b101 for h in base]                                    # 2 bits off each
        q = fingerprint.Fingerprint("video", "vframes64", {"f": [f"{h:016x}" for h in noisy]}, [])
        row = SimpleNamespace(data={"f": [f"{h:016x}" for h in base]})
        self.assertIsNotNone(fingerprint._compare_frames(q, row, "video"))
        other = SimpleNamespace(data={"f": [f"{rnd.getrandbits(64):016x}" for _ in range(12)]})
        self.assertIsNone(fingerprint._compare_frames(q, other, "video"))

    def test_audio_matches_a_shifted_noisy_copy_but_not_another_song(self):
        rnd = random.Random(9)
        song = [rnd.getrandbits(32) for _ in range(400)]
        copy = song[7:] + [rnd.getrandbits(32) for _ in range(7)]            # started 7 items later
        copy = [x ^ (1 << rnd.randrange(32)) if rnd.random() < 0.3 else x for x in copy]
        q = fingerprint.Fingerprint("audio", "chroma", {"a": copy}, [])
        self.assertIsNotNone(fingerprint._compare_audio(q, SimpleNamespace(data={"a": song})))
        other = SimpleNamespace(data={"a": [rnd.getrandbits(32) for _ in range(400)]})
        self.assertIsNone(fingerprint._compare_audio(q, other))

    def test_short_audio_never_matches(self):
        short = [1] * 50
        q = fingerprint.Fingerprint("audio", "chroma", {"a": short}, [])
        self.assertIsNone(fingerprint._compare_audio(q, SimpleNamespace(data={"a": short})))


@skipUnless(importlib.util.find_spec("pypdf") and importlib.util.find_spec("reportlab"), "pypdf / reportlab missing")
class PdfFingerprintTests(SimpleTestCase):
    VOCAB = [f"word{i}x" for i in range(400)]

    def make_pdf(self, seed, words_per_line):
        from reportlab.pdfgen import canvas

        rnd = random.Random(seed)
        words = [rnd.choice(self.VOCAB) for _ in range(500)]
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        c = canvas.Canvas(path)
        y = 800
        for i in range(0, len(words), words_per_line):
            c.drawString(40, y, " ".join(words[i:i + words_per_line]))
            y -= 14
            if y < 40:
                c.showPage()
                y = 800
        c.save()
        return path

    def test_same_text_laid_out_differently_matches_other_text_does_not(self):
        pa, pb, pc = self.make_pdf(1, 12), self.make_pdf(1, 7), self.make_pdf(2, 12)
        try:
            fa, fb, fc = (fingerprint.pdf_fingerprint(p) for p in (pa, pb, pc))
        finally:
            for p in (pa, pb, pc):
                os.unlink(p)
        self.assertEqual((fa.algo, len(fa.bands)), ("minhash64", 16))
        self.assertIsNotNone(fingerprint._compare_minhash(fa, SimpleNamespace(data=fb.data)))
        self.assertIsNone(fingerprint._compare_minhash(fa, SimpleNamespace(data=fc.data)))

    def test_kind_is_found_from_the_bytes_not_the_extension(self):
        path = self.make_pdf(1, 12)
        try:
            self.assertEqual(fingerprint.sniff_kind(path, "renamed.txt"), "pdf")
        finally:
            os.unlink(path)


@override_settings(MEDIA_ROOT=TMP, COPYRIGHT_MEDIA_ASYNC=False)
class FingerprintFlowTests(Media):
    def setUp(self):
        super().setUp()
        self.l2, self.l3 = mk_staff("fp2", 2), mk_staff("fp3", 3)
        self.orig = make_image(11)

    def uphold(self, claim):
        with self.captureOnCommitCallbacks(execute=True):
            services.uphold_claim(claim, self.l2, "ok")

    def remove_original(self):
        p, _pm = self.post_with_file(data=encode(self.orig), name="posts/2026/fp_orig.png")
        claim = self.file(post=p)
        self.uphold(claim)
        return claim

    def upload(self, who, data, name):
        post = mk_post(mk_user(who))
        with self.captureOnCommitCallbacks(execute=True):
            saved = default_storage.save(name, ContentFile(data))
            PostMedia.objects.create(post=post, media_type="image", file=saved, file_name=os.path.basename(name),
                                     file_size_bytes=len(data), mime_type="image/jpeg")
        return post

    def test_removing_a_picture_registers_its_fingerprint(self):
        self.remove_original()
        fp = MediaFingerprint.objects.get(kind="image", active=True)
        self.assertEqual(fp.algo, "dhash64")
        self.assertTrue(FingerprintBand.objects.filter(fingerprint=fp).exists())

    def test_resized_recompressed_and_mirrored_copies_are_held_other_pictures_are_not(self):
        from PIL import ImageOps

        parent = self.remove_original()
        small = self.upload("copy1", encode(self.orig.resize((160, 120)), "JPEG", quality=55), "posts/2026/c1.jpg")
        flipped = self.upload("copy2", encode(ImageOps.mirror(self.orig), "JPEG", quality=80), "posts/2026/c2.jpg")
        other = self.upload("honest2", encode(make_image(99)), "posts/2026/c3.png")
        self.assertEqual(self.status_of(small), "copyright_hold")
        self.assertEqual(self.status_of(flipped), "copyright_hold")
        self.assertEqual(self.status_of(other), "approved")
        auto = CopyrightClaim.objects.get(target_id=str(small.id))
        self.assertEqual((auto.source, auto.status, auto.required_level), ("auto", "under_review", 2))
        self.assertIn("dhash64", auto.infringement_details)
        self.assertIn(str(parent.id), auto.work_description)

    def test_restoring_the_original_stops_the_matching(self):
        claim = self.remove_original()
        with self.captureOnCommitCallbacks(execute=True):
            services.restore_claim(claim, self.l3, "wrongly removed")
        self.assertFalse(MediaFingerprint.objects.filter(active=True).exists())
        again = self.upload("copy3", encode(self.orig.resize((160, 120)), "JPEG", quality=55), "posts/2026/c4.jpg")
        self.assertEqual(self.status_of(again), "approved")

    @override_settings(COPYRIGHT_FP_ENABLED=False)
    def test_can_be_switched_off(self):
        self.remove_original()
        self.assertFalse(MediaFingerprint.objects.exists())

    def test_exact_copy_is_refused_before_saving(self):
        self.remove_original()
        data = encode(self.orig)
        ok, code, msg = services.precheck_upload(mk_user("sneaky"), files=[SimpleUploadedFile("again.png", data)])
        self.assertEqual((ok, code), (False, "copyright_reupload_blocked"))
        self.assertTrue(msg)
        self.assertTrue(services.precheck_upload(mk_user("fine"), files=[
            SimpleUploadedFile("new.png", encode(make_image(5)))])[0])
        with override_settings(COPYRIGHT_REJECT_EXACT_REUPLOAD=False):
            self.assertTrue(services.precheck_upload(mk_user("fine2"), files=[SimpleUploadedFile("a.png", data)])[0])

    def test_precheck_leaves_the_uploaded_file_readable(self):
        self.remove_original()
        f = SimpleUploadedFile("x.png", encode(make_image(6)))
        services.precheck_upload(mk_user("reader"), files=[f])
        self.assertEqual(f.read(4), b"\x89PNG")

    def test_precheck_checks_assembled_files_by_path(self):
        self.remove_original()
        path = write_tmp(encode(self.orig), ".png")
        try:
            self.assertFalse(services.precheck_upload(mk_user("chunker"), paths=[path])[0])
        finally:
            os.unlink(path)
