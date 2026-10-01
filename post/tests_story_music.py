"""
STORIES UPGRADE - PART 3a (Music on a story) tests.

Covers: every rule in post/story_music.py (pure, no DB), creating a story with
music through the multipart API, the serialised shape on create / list / detail,
and that a rejected track saves nothing.
"""
import io
import json
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APITestCase

from user_profile.models import Follow

from .models import Story
from .story_music import MusicError, clean_music, clean_music_url, parse_music_payload

User = get_user_model()

_TMP_MEDIA = tempfile.mkdtemp(prefix="ls_story_music_tests_")

URL = "https://cdn.freesound.org/previews/123/123456_1-hq.mp3"


def track(**extra):
    base = {
        "id": 123456, "title": "Calm piano", "artist": "some_user",
        "preview_url": URL, "duration": 42.5, "start": 3, "license": "CC0",
    }
    base.update(extra)
    return base


def image_upload(name="m.jpg"):
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 30, 30)).save(buf, format="JPEG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


# ---------------------------------------------------------------------------
# Pure rules - no database needed.
# ---------------------------------------------------------------------------
class CleanMusicUrlTests(SimpleTestCase):
    def test_freesound_cdn_url_is_kept(self):
        self.assertEqual(clean_music_url(URL), URL)

    def test_host_is_lowercased_and_query_and_fragment_are_dropped(self):
        self.assertEqual(
            clean_music_url("https://CDN.Freesound.ORG/previews/1/1_1.mp3?token=SECRET#t=3"),
            "https://cdn.freesound.org/previews/1/1_1.mp3",
        )

    def test_bare_freesound_host_and_ogg_are_fine(self):
        self.assertEqual(clean_music_url("https://freesound.org/data/previews/1/1_1.ogg"),
                         "https://freesound.org/data/previews/1/1_1.ogg")

    def test_default_https_port_is_fine(self):
        self.assertEqual(clean_music_url("https://cdn.freesound.org:443/p/1.mp3"), "https://cdn.freesound.org/p/1.mp3")

    def test_rejected_urls(self):
        bad = [
            "", "   ", None, 12,
            "http://cdn.freesound.org/p/1.mp3",                    # not https
            "ftp://cdn.freesound.org/p/1.mp3",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "//cdn.freesound.org/p/1.mp3",                         # no scheme
            "https://evil.example/p/1.mp3",                        # wrong host
            "https://freesound.org.evil.example/p/1.mp3",          # look-alike host
            "https://notfreesound.org/p/1.mp3",                    # suffix without the dot
            "https://127.0.0.1/p/1.mp3",
            "https://user:pw@cdn.freesound.org/p/1.mp3",           # credentials
            "https://cdn.freesound.org:8443/p/1.mp3",              # odd port
            "https://cdn.freesound.org:abc/p/1.mp3",               # malformed port
            "https://cdn.freesound.org/p/1.exe",                   # not audio
            "https://cdn.freesound.org/p/1",                       # no extension
            "https://cdn.freesound.org/p/a b.mp3",                 # whitespace
            "https://cdn.freesound.org/" + "a" * 600 + ".mp3",     # too long
        ]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(MusicError):
                    clean_music_url(value)

    @override_settings(STORY_MUSIC_ALLOWED_HOSTS=("audio.example.org",))
    def test_allowed_hosts_setting_replaces_the_default(self):
        self.assertEqual(clean_music_url("https://audio.example.org/a.mp3"), "https://audio.example.org/a.mp3")
        with self.assertRaises(MusicError):
            clean_music_url(URL)


class ParseMusicPayloadTests(SimpleTestCase):
    def test_no_music_shapes(self):
        for raw in (None, "", "   ", "null", {}, "{}"):
            with self.subTest(raw=raw):
                self.assertIsNone(clean_music(parse_music_payload(raw)))

    def test_json_string_and_dict_give_the_same_result(self):
        self.assertEqual(clean_music(parse_music_payload(json.dumps(track()))), clean_music(parse_music_payload(track())))

    def test_bad_json_and_wrong_shapes(self):
        for raw in ("{not json", "[1, 2]", "12", '"text"', [1], 5):
            with self.subTest(raw=raw):
                with self.assertRaises(MusicError):
                    parse_music_payload(raw)

    def test_oversized_payload(self):
        with self.assertRaises(MusicError):
            parse_music_payload(json.dumps(track(title="x" * 5000)))


class CleanMusicTests(SimpleTestCase):
    def test_full_track_is_normalised(self):
        self.assertEqual(clean_music(track()), {
            "id": "123456", "title": "Calm piano", "artist": "some_user",
            "url": URL, "duration": 42.5, "start": 3.0, "license": "CC0",
        })

    def test_aliases_url_and_name(self):
        payload = track()
        payload["url"] = payload.pop("preview_url")
        payload["name"] = payload.pop("title")
        out = clean_music(payload)
        self.assertEqual((out["url"], out["title"]), (URL, "Calm piano"))

    def test_minimal_track(self):
        out = clean_music({"id": "9", "title": "T", "preview_url": URL})
        self.assertEqual(out, {"id": "9", "title": "T", "artist": "", "url": URL,
                               "duration": None, "start": 0.0, "license": "CC0"})

    def test_text_is_stripped_of_control_characters(self):
        out = clean_music(track(title="  Ca\x00lm\n piano \x07 ", artist="a\x1fb"))
        self.assertEqual(out["title"], "Calm piano")
        self.assertEqual(out["artist"], "ab")

    def test_numbers_may_arrive_as_strings(self):
        out = clean_music(track(duration="30.5", start="2"))
        self.assertEqual((out["duration"], out["start"]), (30.5, 2.0))

    def test_rejections(self):
        cases = {
            "no id": track(id=None),
            "bool id": track(id=True),
            "non-numeric id": track(id="abc"),
            "id with junk": track(id="12; DROP"),
            "huge id": track(id="1" * 30),
            "no title": track(title=""),
            "blank title": track(title="   "),
            "long title": track(title="x" * 101),
            "long artist": track(artist="x" * 61),
            "non-text title": track(title=5),
            "no url": track(preview_url=None),
            "cc-by": track(license="CC-BY"),
            "non-text license": track(license=5),
            "zero duration": track(duration=0),
            "negative duration": track(duration=-3),
            "huge duration": track(duration=601),
            "nan duration": track(duration=float("nan")),
            "inf duration": track(duration=float("inf")),
            "bool duration": track(duration=True),
            "text duration": track(duration="long"),
            "negative start": track(start=-1),
            "start past the end": track(start=42.5),
            "start beyond max": track(start=601, duration=None),
            "bool start": track(start=True),
        }
        for label, payload in cases.items():
            with self.subTest(label):
                with self.assertRaises(MusicError):
                    clean_music(payload)

    def test_limits_are_inclusive(self):
        out = clean_music(track(title="x" * 100, artist="y" * 60, duration=600, start=599.99))
        self.assertEqual((len(out["title"]), len(out["artist"]), out["duration"]), (100, 60, 600.0))

    def test_license_is_case_insensitive_and_optional(self):
        self.assertEqual(clean_music(track(license="cc0"))["license"], "CC0")
        payload = track()
        del payload["license"]
        self.assertEqual(clean_music(payload)["license"], "CC0")

    def test_start_without_a_known_duration_is_allowed(self):
        self.assertEqual(clean_music(track(duration=None, start=120))["start"], 120.0)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class StoryMusicApiTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.owner = User.objects.create_user(username="mu_owner", password="testpass123")
        self.fan = User.objects.create_user(username="mu_fan", password="testpass123")
        Follow.objects.create(follower=self.fan, following=self.owner, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.owner)

    def _create(self, music=None, raw=None, **data):
        payload = {"media": image_upload(), "media_type": "image", **data}
        if raw is not None:
            payload["music"] = raw
        elif music is not None:
            payload["music"] = json.dumps(music)
        return self.client.post(reverse("story-create"), payload, format="multipart")

    def test_story_without_music_has_null_music(self):
        res = self._create()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(res.data["music"])
        self.assertIsNone(Story.objects.get(pk=res.data["id"]).music)

    def test_empty_and_null_music_fields_are_fine(self):
        for raw in ("", "null", "{}"):
            with self.subTest(raw=raw):
                res = self._create(raw=raw)
                self.assertEqual(res.status_code, 201, res.data)
                self.assertIsNone(res.data["music"])

    def test_create_with_music_stores_the_cleaned_track(self):
        res = self._create(track(preview_url=URL + "?token=SECRET", title="  Calm piano "))
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["music"], {
            "id": "123456", "title": "Calm piano", "artist": "some_user",
            "url": URL, "duration": 42.5, "start": 3.0, "license": "CC0",
        })
        self.assertEqual(Story.objects.get(pk=res.data["id"]).music, res.data["music"])

    def test_music_works_on_a_video_story_too(self):
        res = self.client.post(reverse("story-create"), {
            "media": SimpleUploadedFile("v.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, content_type="video/mp4"),
            "media_type": "video", "music": json.dumps(track()),
        }, format="multipart")
        # Whatever the media validator says about this fake clip, music must not be the reason.
        if res.status_code == 400:
            self.assertNotIn("music", res.data.get("errors", {}))
        else:
            self.assertEqual(res.data["music"]["id"], "123456")

    def test_music_together_with_a_sticker_and_close_friends(self):
        res = self._create(track(), audience="close_friends",
                           stickers=json.dumps([{"kind": "link", "url": "https://example.com"}]))
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["music"]["title"], "Calm piano")
        self.assertEqual(len(res.data["stickers"]), 1)

    def test_bad_tracks_are_rejected_and_nothing_is_saved(self):
        bad = [
            track(preview_url="https://evil.example/a.mp3"),
            track(preview_url="http://cdn.freesound.org/a.mp3"),
            track(license="CC-BY"),
            track(title=""),
            track(id="abc"),
            track(start=99, duration=10),
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                res = self._create(payload)
                self.assertEqual(res.status_code, 400, getattr(res, "data", None))
                self.assertIn("music", res.data["errors"])
        self.assertEqual(Story.objects.count(), 0)

    def test_malformed_json_is_a_400(self):
        res = self._create(raw="{oops")
        self.assertEqual(res.status_code, 400)
        self.assertIn("music", res.data["errors"])
        self.assertEqual(Story.objects.count(), 0)

    def test_music_is_visible_in_list_and_detail_to_a_follower(self):
        created = self._create(track())
        self.assertEqual(created.status_code, 201, created.data)

        self.client.force_authenticate(self.fan)
        listing = self.client.get(reverse("story-list"))
        rows = listing.data["results"] if isinstance(listing.data, dict) and "results" in listing.data else listing.data
        self.assertEqual(rows[0]["music"]["url"], URL)

        detail = self.client.get(reverse("story-detail", args=[created.data["id"]]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["music"]["title"], "Calm piano")
