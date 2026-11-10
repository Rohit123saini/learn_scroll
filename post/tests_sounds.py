# post/tests_sounds.py - X7 reel sounds: policy, blocked sounds (reel / story / search), credit, sound page, report, takedown.
# NOT RUN when written. Run: python manage.py test post.tests_sounds   (needs the 0035_reel_sounds migration applied)
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from . import sounds
from .models import Post, PostMedia, SoundReport, SoundTakedown
from .reel_meta import ReelMetaError, clean_reel_meta
from .story_music import MusicError, clean_music

User = get_user_model()

FS_URL = "https://cdn.freesound.org/previews/1/123_1-hq.mp3"


def reel_block(**music):
    d = {"v": 1, "clips": 2, "duration": 20.0}
    if music:
        d["music"] = music
    return d


def cc0(sid="123", name="Calm piano", artist="bob"):
    return {"id": sid, "name": name, "artist": artist, "license": "CC0"}


class SoundsBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.viewer = User.objects.create_user(username="viewer", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        self.staff = User.objects.create_user(username="staff", password="x", is_staff=True)
        self.client.force_authenticate(self.viewer)

    def reel(self, music=None, user=None, extra=None, **kw):
        block = {"v": 1, "clips": 2, "duration": 20.0}
        if music is not None:
            block["music"] = music
        block.update(extra or {})
        kw.setdefault("post_type", "video")
        kw.setdefault("visibility", "public")
        kw.setdefault("category", "tech")
        post = Post.objects.create(user=user or self.author, content="c", metadata={"reel": block}, **kw)
        PostMedia.objects.create(post=post, media_type="video", file="posts/x.mp4", file_name="x.mp4",
                                 file_size_bytes=1, mime_type="video/mp4")
        return post


class PolicyTests(SoundsBase):
    @override_settings(COPYRIGHT_EMAIL="legal@example.com")
    def test_policy_is_cc0_and_own_audio_only(self):
        self.client.force_authenticate(None)
        r = self.client.get(reverse("sound-policy"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["mode"], "cc0_and_own_audio")
        self.assertFalse(r.data["licensed_library"])
        kinds = {s["kind"]: s for s in r.data["sources"]}
        self.assertEqual(set(kinds), {"cc0", "user_audio"})
        self.assertTrue(kinds["cc0"]["reusable"])
        self.assertFalse(kinds["user_audio"]["reusable"])
        self.assertEqual(r.data["takedown_contact"], "legal@example.com")


class EnforcementTests(SoundsBase):
    def test_blocked_sound_is_refused_in_a_new_reel(self):
        sounds.block_sound("123", by=self.staff, title="Calm piano")
        with self.assertRaises(ReelMetaError) as cm:
            clean_reel_meta(reel_block(**cc0()), None)
        self.assertEqual(str(cm.exception), sounds.BLOCKED_TEXT)

    def test_other_sound_still_works_and_user_audio_is_untouched(self):
        sounds.block_sound("123", by=self.staff)
        out = clean_reel_meta(reel_block(**cc0(sid="999")), None)
        self.assertEqual(out["music"]["id"], "999")
        own = clean_reel_meta(reel_block(id="a1", name="Mine", license="user_audio", rights_confirmed=True), None)
        self.assertTrue(own["copyright_flag"])

    def test_restore_allows_the_sound_again(self):
        sounds.block_sound("123", by=self.staff)
        self.assertEqual(sounds.restore_sound("123", by=self.staff), 1)
        self.assertEqual(clean_reel_meta(reel_block(**cc0()), None)["music"]["id"], "123")

    def test_blocked_sound_is_refused_on_a_new_story(self):
        payload = {"id": 123, "title": "Calm", "artist": "bob", "preview_url": FS_URL, "license": "CC0"}
        self.assertEqual(clean_music(payload)["id"], "123")
        sounds.block_sound("123", by=self.staff)
        with self.assertRaises(MusicError):
            clean_music(payload)

    @override_settings(FREESOUND_API_KEY="k")
    def test_search_hides_blocked_sounds(self):
        sounds.block_sound("1", by=self.staff)
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"count": 2, "results": [
            {"id": 1, "name": "gone", "username": "u", "duration": 5, "previews": {"preview-hq-mp3": FS_URL}, "tags": []},
            {"id": 2, "name": "kept", "username": "u", "duration": 5, "previews": {"preview-hq-mp3": FS_URL}, "tags": []},
        ]}
        with mock.patch("post.views.requests.get", return_value=resp):
            r = self.client.get(reverse("post-music-search"), {"q": "piano"})
        self.assertEqual([x["id"] for x in r.data["data"]["results"]], [2])

    def test_blocked_story_music_is_not_served(self):
        from .serializers import StorySerializer

        music = {"id": "123", "title": "Calm", "artist": "bob", "url": FS_URL, "license": "CC0", "start": 0.0}
        story = mock.Mock(music=music)
        ser = StorySerializer()
        self.assertEqual(ser.get_music(story), music)
        sounds.block_sound("123", by=self.staff)
        self.assertIsNone(ser.get_music(story))
        self.assertEqual(ser.get_music(mock.Mock(music=None)), None)


class CreditTests(SoundsBase):
    def test_cc0_credit_has_source_and_link(self):
        c = sounds.credit_for(self.reel(cc0()))
        self.assertEqual((c["kind"], c["license"], c["source"]), ("cc0", "CC0", "Freesound"))
        self.assertEqual(c["name"], "Calm piano")
        self.assertEqual(c["credit_url"], "https://freesound.org/s/123/")
        self.assertTrue(c["reusable"])
        self.assertFalse(c["removed"])

    def test_user_audio_is_never_reusable_and_has_no_link(self):
        c = sounds.credit_for(self.reel({"id": "a1", "name": "My song", "license": "user_audio", "rights_confirmed": True}))
        self.assertEqual(c["kind"], "user_audio")
        self.assertFalse(c["reusable"])
        self.assertEqual(c["credit_url"], "")

    def test_original_audio_and_no_info(self):
        self.assertEqual(sounds.credit_for(self.reel(None))["kind"], "original")
        self.assertIsNone(sounds.credit_for(self.reel(None, extra={"original_audio": False})))
        plain = Post.objects.create(user=self.author, content="c", post_type="video", visibility="public", category="tech")
        self.assertIsNone(sounds.credit_for(plain))

    def test_removed_sound_hides_name_and_artist(self):
        post = self.reel(cc0())
        sounds.block_sound("123", by=self.staff)
        c = sounds.credit_for(post)
        self.assertTrue(c["removed"])
        self.assertEqual((c["name"], c["artist"], c["credit_url"]), ("", "", ""))
        self.assertFalse(c["reusable"])


class SoundPageTests(SoundsBase):
    def test_detail_and_reels_list_only_this_sound(self):
        a = self.reel(cc0())
        self.reel(cc0(sid="777"))
        with mock.patch("post.sounds._freesound_get", return_value=None):
            r = self.client.get(reverse("sound-detail", args=["123"]))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["id"], r.data["uses"], r.data["name"]), ("123", 1, "Calm piano"))
        r = self.client.get(reverse("sound-reels", args=["123"]))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual([x["id"] for x in r.data["results"]], [str(a.id)])
        self.assertEqual(r.data["results"][0]["sound"]["id"], "123")
        self.assertIsNone(r.data["next_page"])

    def test_detail_adds_preview_only_when_freesound_still_says_cc0(self):
        self.reel(cc0())
        raw = {"id": 123, "name": "Calm piano", "username": "bob", "duration": 42.5, "tags": ["bpm-90"],
               "license": "http://creativecommons.org/publicdomain/zero/1.0/", "previews": {"preview-hq-mp3": FS_URL}}
        with mock.patch("post.sounds._freesound_get", return_value=raw):
            r = self.client.get(reverse("sound-detail", args=["123"]))
        self.assertEqual((r.data["preview_url"], r.data["duration"], r.data["reusable"]), (FS_URL, 42.5, True))
        self.assertNotIn("token", str(r.data))

        cache.clear()
        raw["license"] = "http://creativecommons.org/licenses/by/4.0/"
        with mock.patch("post.sounds._freesound_get", return_value=raw):
            r = self.client.get(reverse("sound-detail", args=["123"]))
        self.assertFalse(r.data["reusable"])
        self.assertTrue(r.data["license_changed"])
        self.assertEqual(r.data["preview_url"], "")

    def test_detail_without_freesound_still_opens_but_is_not_reusable(self):
        self.reel(cc0())
        with mock.patch("post.sounds._freesound_get", return_value=None):
            r = self.client.get(reverse("sound-detail", args=["123"]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["reusable"])
        self.assertEqual(r.data["preview_url"], "")

    def test_unknown_sound_is_404(self):
        self.assertEqual(self.client.get(reverse("sound-detail", args=["424242"])).status_code, 404)

    def test_hidden_reels_are_not_listed(self):
        self.reel(cc0(), is_deleted=True)
        self.reel(cc0(), visibility="private")
        self.assertEqual(self.client.get(reverse("sound-detail", args=["123"])).status_code, 404)

    def test_user_audio_reels_never_appear_on_a_sound_page(self):
        self.reel({"id": "123", "name": "x", "license": "user_audio", "rights_confirmed": True})
        self.assertEqual(self.client.get(reverse("sound-detail", args=["123"])).status_code, 404)

    def test_paging(self):
        for _ in range(3):
            self.reel(cc0())
        r = self.client.get(reverse("sound-reels", args=["123"]), {"page_size": 2})
        self.assertEqual((len(r.data["results"]), r.data["next_page"], r.data["count"]), (2, 2, 3))
        r = self.client.get(reverse("sound-reels", args=["123"]), {"page_size": 2, "page": 2})
        self.assertEqual((len(r.data["results"]), r.data["next_page"]), (1, None))

    def test_blocked_sound_page_says_removed_with_no_reels(self):
        self.reel(cc0())
        sounds.block_sound("123", by=self.staff)
        r = self.client.get(reverse("sound-detail", args=["123"]))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["removed"])
        self.assertEqual(r.data["name"], "")
        self.assertEqual(self.client.get(reverse("sound-reels", args=["123"])).data["results"], [])

    def test_login_required(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(reverse("sound-detail", args=["123"])).status_code, (401, 403))


class ReportTests(SoundsBase):
    def test_report_creates_then_updates_one_row(self):
        url = reverse("sound-report", args=["123"])
        r = self.client.post(url, {"reason": "copyright", "note": "this is a Bollywood song"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        r = self.client.post(url, {"reason": "wrong_license"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(SoundReport.objects.filter(sound_id="123").count(), 1)
        self.assertEqual(SoundReport.objects.get(sound_id="123").reason, "wrong_license")

    def test_bad_reason_is_400(self):
        r = self.client.post(reverse("sound-report", args=["123"]), {"reason": "boring"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["code"], "sound_reason")

    def test_summary_counts_and_flags_needs_look(self):
        for i in range(3):
            u = User.objects.create_user(username=f"u{i}", password="x")
            sounds.report_sound(u, "55", "copyright")
        sounds.report_sound(self.viewer, "66", "other")
        rows = sounds.open_reports_summary()
        self.assertEqual(rows[0]["sound_id"], "55")
        self.assertEqual(rows[0]["reports"], 3)
        self.assertTrue(rows[0]["needs_look"])
        self.assertFalse(rows[1]["needs_look"])


@override_settings(COPYRIGHT_EMAILS_ENABLED=False)
class TakedownTests(SoundsBase):
    def test_block_is_idempotent_closes_reports_and_tells_owners_once_each(self):
        self.reel(cc0())
        self.reel(cc0())
        other = User.objects.create_user(username="other", password="x")
        self.reel(cc0(), user=other)
        sounds.report_sound(self.viewer, "123", "copyright")
        with mock.patch("copyrights.notify.send") as send:
            row = sounds.block_sound("123", by=self.staff, reason="copyright")
            again = sounds.block_sound("123", by=self.staff)
        self.assertEqual(row.pk, again.pk)
        self.assertEqual(send.call_count, 2)               # two owners, once each
        self.assertEqual(row.owners_told, 2)
        self.assertEqual(row.title, "Calm piano")           # filled from the reels that use it
        self.assertEqual(SoundTakedown.objects.filter(status="blocked").count(), 1)
        self.assertEqual(SoundReport.objects.get(sound_id="123").status, "actioned")
        data = send.call_args_list[0].kwargs["data"]
        self.assertEqual((data["kind"], data["sound_id"]), ("sound_removed", "123"))
        self.assertNotEqual(data.get("role"), "owner")      # a sound notice is not a legal copyright notice (no e-mail)

    def test_reels_stay_visible_and_hold_option_queues_them(self):
        post = self.reel(cc0())
        sounds.block_sound("123", by=self.staff, hold_reels=True)
        post.refresh_from_db()
        self.assertEqual(post.moderation_status, "approved")   # not hidden: only the credit is replaced
        from user_profile.models import AutoModerationFlag

        flag = AutoModerationFlag.objects.get(target_type="post", target_id=str(post.pk))
        self.assertEqual(flag.reason, "audio_copyright")

    def test_no_hold_by_default(self):
        post = self.reel(cc0())
        sounds.block_sound("123", by=self.staff)
        from user_profile.models import AutoModerationFlag

        self.assertFalse(AutoModerationFlag.objects.filter(target_id=str(post.pk)).exists())

    def test_bad_input(self):
        with self.assertRaises(sounds.SoundError):
            sounds.block_sound("abc", by=self.staff)
        with self.assertRaises(sounds.SoundError):
            sounds.block_sound("1", by=self.staff, reason="because")

    def test_staff_api_block_restore_dismiss(self):
        self.reel(cc0())
        sounds.report_sound(self.viewer, "123", "copyright")
        url = reverse("staff-sound-action")
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post(url, {"action": "block", "sound_id": "123"}, format="json").status_code, 403)
        self.assertEqual(self.client.get(reverse("staff-sound-reports")).status_code, 403)

        self.client.force_authenticate(self.staff)
        lst = self.client.get(reverse("staff-sound-reports"))
        self.assertEqual(lst.data["results"][0]["sound_id"], "123")
        r = self.client.post(url, {"action": "block", "sound_id": "123", "hold_reels": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["reels_held"], 1)
        self.assertTrue(sounds.is_blocked("123"))
        r = self.client.post(url, {"action": "restore", "sound_id": "123"}, format="json")
        self.assertEqual(r.data["restored"], 1)
        self.assertFalse(sounds.is_blocked("123"))
        sounds.report_sound(self.viewer, "123", "other")
        self.assertEqual(self.client.post(url, {"action": "dismiss", "sound_id": "123"}, format="json").data["dismissed"], 1)
        self.assertEqual(self.client.post(url, {"action": "nope", "sound_id": "123"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"action": "block", "sound_id": "x"}, format="json").status_code, 400)

    def test_blocked_set_is_cached_and_cleared_by_admin_style_save(self):
        sounds.block_sound("5", by=self.staff)
        self.assertTrue(sounds.is_blocked("5"))
        SoundTakedown.objects.filter(sound_id="5").first().delete()   # model.delete() clears the cache
        self.assertFalse(sounds.is_blocked("5"))
