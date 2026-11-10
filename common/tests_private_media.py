"""
T0.3 (B3) tests.   python manage.py test common.tests_private_media

Done-when mapping:
  * logged-out curl on a private file           -> 401 (no sig) / 403 (bad or expired sig)
  * authorised user (serializer-minted URL)     -> 200, Range works
  * 1-yr public cache only on public media      -> private responses are `private`, /media/<private> -> 403
"""
import os
import shutil
import tempfile
import time

from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings

from common.private_media import build_signed_path, is_private_path, make_signature, verify_signature

TMP = tempfile.mkdtemp(prefix="pm_test_")


class PrivatePathTests(SimpleTestCase):
    def test_classification(self):
        for p in ("class_materials/a.pdf", "assigments/submissions/x.png", "testseries/questions/q.jpg",
                  "campus/doubts/2026/01/a.png", "certificates/c.pdf", "copyright_evidence/1/a.png",
                  "/media/class_materials/a.pdf"):
            self.assertTrue(is_private_path(p), p)
        for p in ("posts/2026/01/01/a.jpg", "stories/a.mp4", "profile/me.png", "comment_media/a.png",
                  "classroom_covers/c.jpg"):
            self.assertFalse(is_private_path(p), p)

    def test_signature_roundtrip_and_tamper(self):
        now = 1_700_000_000
        exp, sig = make_signature("class_materials/a.pdf", now)
        self.assertEqual(verify_signature("class_materials/a.pdf", exp, sig, now), "ok")
        self.assertEqual(verify_signature("class_materials/b.pdf", exp, sig, now), "invalid")  # bound to path
        self.assertEqual(verify_signature("class_materials/a.pdf", exp + 1, sig, now), "invalid")
        self.assertEqual(verify_signature("class_materials/a.pdf", exp, "0" * 40, now), "invalid")
        self.assertEqual(verify_signature("class_materials/a.pdf", None, None, now), "missing")
        self.assertEqual(verify_signature("class_materials/a.pdf", exp, sig, exp + 1), "expired")

    def test_url_stable_within_bucket_and_expires_later(self):
        a = build_signed_path("class_materials/a.pdf", 1_700_000_000)
        b = build_signed_path("class_materials/a.pdf", 1_700_000_100)
        self.assertEqual(a, b)  # same bucket -> same URL -> client caches still hit
        self.assertNotEqual(a, build_signed_path("class_materials/a.pdf", 1_700_000_000 + 7200))


@override_settings(MEDIA_ROOT=TMP, USE_S3_STORAGE=False, PRIVATE_MEDIA_ACCEL_PREFIX="")
class PrivateMediaServeTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.makedirs(os.path.join(TMP, "class_materials"), exist_ok=True)
        with open(os.path.join(TMP, "class_materials", "notes.pdf"), "wb") as f:
            f.write(b"0123456789" * 100)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)
        super().tearDownClass()

    def test_logged_out_curl_is_rejected(self):
        self.assertEqual(self.client.get("/pmedia/class_materials/notes.pdf").status_code, 401)
        self.assertEqual(self.client.get("/pmedia/class_materials/notes.pdf?exp=1&sig=bad").status_code, 403)

    def test_expired_link_rejected(self):
        exp, sig = make_signature("class_materials/notes.pdf", time.time() - 10 * 3600)
        self.assertEqual(self.client.get(f"/pmedia/class_materials/notes.pdf?exp={exp}&sig={sig}").status_code, 403)

    def test_valid_signed_url_serves_file_privately(self):
        url = build_signed_path("class_materials/notes.pdf")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content)[:10], b"0123456789")
        self.assertIn("private", r["Cache-Control"])
        self.assertNotIn("public", r["Cache-Control"])
        self.assertNotIn("31536000", r["Cache-Control"])

    def test_range_request(self):
        r = self.client.get(build_signed_path("class_materials/notes.pdf"), HTTP_RANGE="bytes=10-19")
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r["Content-Range"], "bytes 10-19/1000")
        self.assertEqual(b"".join(r.streaming_content), b"0123456789")

    def test_signature_cannot_be_reused_for_another_file(self):
        exp, sig = make_signature("class_materials/notes.pdf")
        r = self.client.get(f"/pmedia/class_materials/other.pdf?exp={exp}&sig={sig}")
        self.assertEqual(r.status_code, 403)

    def test_traversal_blocked(self):
        exp, sig = make_signature("class_materials/../../etc/passwd")
        r = self.client.get(f"/pmedia/class_materials/../../etc/passwd?exp={exp}&sig={sig}")
        self.assertIn(r.status_code, (400, 403, 404))

    def test_legacy_media_url_for_private_file_is_forbidden(self):
        # even though the file exists on disk
        self.assertEqual(self.client.get("/media/class_materials/notes.pdf").status_code, 403)
        url = build_signed_path("class_materials/notes.pdf")
        sig_qs = url.split("?", 1)[1]
        self.assertEqual(self.client.get(f"/media/class_materials/notes.pdf?{sig_qs}").status_code, 403)

    def test_storage_url_hook_covers_every_serializer(self):
        """Serializers call FieldFile.url -> storage.url(); private prefix must come back signed."""
        from common.storage_backends import PrivateAwareFileSystemStorage

        st = PrivateAwareFileSystemStorage(location=TMP, base_url="/media/")
        priv = st.url("class_materials/notes.pdf")
        pub = st.url("posts/2026/01/01/a.jpg")
        self.assertTrue(priv.startswith("/pmedia/class_materials/notes.pdf?exp="), priv)
        self.assertIn("&sig=", priv)
        self.assertEqual(pub, "/media/posts/2026/01/01/a.jpg")
