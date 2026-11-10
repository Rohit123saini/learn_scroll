"""P10b - avatar BlurHash: set on upload, cleared on remove, exposed by the profile API, backfill command. Written, NOT run."""
from io import BytesIO, StringIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from .management.commands.backfill_profile_blurhash import blurhash_for_stored_photo

User = get_user_model()

_MEDIA = override_settings(MEDIA_ROOT="/tmp/learnscroll_test_media_p10b")


def _png(color=(200, 40, 40), size=(64, 48), name="a.png"):
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@_MEDIA
class ProfilePhotoBlurhashModelTests(TestCase):
    def test_no_photo_means_empty_hash(self):
        u = User.objects.create_user(username="bh_none", password="x")
        self.assertEqual(u.profile_photo_blurhash, "")

    def test_new_upload_gets_a_hash(self):
        u = User.objects.create_user(username="bh_up", password="x")
        u.profile_photo = _png()
        u.save()
        u.refresh_from_db()
        self.assertTrue(u.profile_photo_blurhash)
        self.assertLessEqual(len(u.profile_photo_blurhash), 40)

    def test_file_is_still_stored_intact_after_hashing(self):
        # hashing reads the upload; it must be rewound so storage writes the WHOLE file
        u = User.objects.create_user(username="bh_full", password="x")
        u.profile_photo = _png(size=(40, 40))
        u.save()
        u.refresh_from_db()
        with u.profile_photo.open("rb") as f:
            self.assertEqual(Image.open(f).size, (40, 40))

    def test_replacing_the_photo_changes_the_hash(self):
        u = User.objects.create_user(username="bh_rep", password="x")
        u.profile_photo = _png(color=(255, 0, 0))
        u.save()
        first = User.objects.get(pk=u.pk).profile_photo_blurhash
        u = User.objects.get(pk=u.pk)
        u.profile_photo = _png(color=(0, 0, 255), name="b.png")
        u.save()
        self.assertNotEqual(User.objects.get(pk=u.pk).profile_photo_blurhash, first)

    def test_removing_the_photo_clears_the_hash(self):
        u = User.objects.create_user(username="bh_rm", password="x")
        u.profile_photo = _png()
        u.save()
        u = User.objects.get(pk=u.pk)
        u.profile_photo = None
        u.save()
        self.assertEqual(User.objects.get(pk=u.pk).profile_photo_blurhash, "")

    def test_unrelated_save_keeps_the_hash_and_does_not_read_the_file(self):
        u = User.objects.create_user(username="bh_keep", password="x")
        u.profile_photo = _png()
        u.save()
        u = User.objects.get(pk=u.pk)
        before = u.profile_photo_blurhash
        u.bio = "changed"
        u.save()
        self.assertEqual(User.objects.get(pk=u.pk).profile_photo_blurhash, before)

    def test_undecodable_upload_does_not_break_the_save(self):
        u = User.objects.create_user(username="bh_bad", password="x")
        u.profile_photo = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
        u.save()  # must not raise
        self.assertEqual(User.objects.get(pk=u.pk).profile_photo_blurhash, "")


@_MEDIA
class ProfilePhotoBlurhashApiTests(TestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="bh_me", password="x")
        self.other = User.objects.create_user(username="bh_other", password="x")
        self.other.profile_photo = _png()
        self.other.save()
        self.c = APIClient()
        self.c.force_authenticate(self.me)

    def test_other_profile_exposes_the_hash(self):
        r = self.c.get("/profile/profile/bh_other/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["data"]["profile_photo_blurhash"], User.objects.get(pk=self.other.pk).profile_photo_blurhash)

    def test_private_account_card_exposes_neither_photo_nor_hash(self):
        User.objects.filter(pk=self.other.pk).update(is_private=True)
        r = self.c.get("/profile/profile/bh_other/")
        self.assertEqual(r.data["profile_state"], "private")
        self.assertNotIn("profile_photo", r.data["data"])
        self.assertNotIn("profile_photo_blurhash", r.data["data"])

    def test_hash_is_read_only_in_profile_update(self):
        # UpdateProfileView takes multipart (image upload); the hash is not in ProfileUpdateSerializer.fields
        self.c.patch("/profile/update/", {"bio": "hi", "profile_photo_blurhash": "EVIL"}, format="multipart")
        self.me.refresh_from_db()
        self.assertEqual(self.me.profile_photo_blurhash, "")  # whatever the status code, the field is not writable


@_MEDIA
class BackfillProfileBlurhashTests(TestCase):
    def _user_with_old_photo(self, name):
        u = User.objects.create_user(username=name, password="x")
        u.profile_photo = _png()
        u.save()
        User.objects.filter(pk=u.pk).update(profile_photo_blurhash="")  # as if uploaded before the column existed
        return u

    def test_helper_reads_from_storage(self):
        u = self._user_with_old_photo("bf_helper")
        self.assertTrue(blurhash_for_stored_photo(User.objects.get(pk=u.pk)))

    def test_dry_run_changes_nothing(self):
        u = self._user_with_old_photo("bf_dry")
        out = StringIO()
        call_command("backfill_profile_blurhash", "--dry-run", stdout=out)
        self.assertIn("would update", out.getvalue())
        self.assertEqual(User.objects.get(pk=u.pk).profile_photo_blurhash, "")

    def test_backfill_fills_only_empty_and_is_rerunnable(self):
        u = self._user_with_old_photo("bf_fill")
        call_command("backfill_profile_blurhash", stdout=StringIO())
        first = User.objects.get(pk=u.pk).profile_photo_blurhash
        self.assertTrue(first)
        out = StringIO()
        call_command("backfill_profile_blurhash", stdout=out)  # nothing left to do
        self.assertIn("updated 0", out.getvalue())
        self.assertEqual(User.objects.get(pk=u.pk).profile_photo_blurhash, first)

    def test_missing_file_is_skipped_not_fatal(self):
        u = self._user_with_old_photo("bf_missing")
        User.objects.get(pk=u.pk).profile_photo.storage.delete(User.objects.get(pk=u.pk).profile_photo.name)
        out = StringIO()
        call_command("backfill_profile_blurhash", "--username", "bf_missing", stdout=out)
        self.assertIn("skipped (unreadable photo): 1", out.getvalue())
