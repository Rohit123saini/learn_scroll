# post/tests_reel_meta.py - Reel studio backend: metadata.reel sanitizer + trending music param.
# NOT RUN when written. Run: python manage.py test post.tests_reel_meta   (no migration needed)
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import Post, PostMedia
from .reel_meta import ReelMetaError, clean_post_metadata, clean_reel_meta

User = get_user_model()


def base(**kw):
    d = {"v": 1, "clips": 3, "duration": 24.5}
    d.update(kw)
    return d


class CleanReelMetaTests(SimpleTestCase):
    def test_minimal_block_is_kept_with_defaults(self):
        out = clean_reel_meta(base(), None)
        self.assertEqual(out["clips"], 3)
        self.assertEqual(out["duration"], 24.5)
        self.assertTrue(out["original_audio"])
        self.assertTrue(out["allow_remix"])
        self.assertFalse(out["voiceover"])

    def test_unknown_keys_are_dropped(self):
        out = clean_reel_meta(base(evil="<script>", url="http://x"), None)
        self.assertNotIn("evil", out)
        self.assertNotIn("url", out)

    def test_wrong_version_or_bad_numbers_drop_the_block(self):
        self.assertIsNone(clean_reel_meta({"v": 2, "clips": 1, "duration": 5}, None))
        self.assertIsNone(clean_reel_meta(base(clips=0), None))
        self.assertIsNone(clean_reel_meta(base(clips=31), None))
        self.assertIsNone(clean_reel_meta(base(duration=500), None))
        self.assertIsNone(clean_reel_meta(base(duration=True), None))
        self.assertIsNone(clean_reel_meta(base(duration=float("nan")), None))
        self.assertIsNone(clean_reel_meta("not json", None))
        self.assertIsNone(clean_reel_meta([1, 2], None))

    def test_json_string_is_accepted(self):
        out = clean_reel_meta('{"v": 1, "clips": 2, "duration": 10}', None)
        self.assertEqual(out["clips"], 2)

    def test_cc0_music_is_kept(self):
        out = clean_reel_meta(base(music={"id": 123, "name": " Calm\x00 piano ", "artist": "bob", "license": "CC0"}), None)
        self.assertEqual(out["music"], {"id": "123", "name": "Calm  piano", "license": "CC0", "artist": "bob"})
        self.assertNotIn("copyright_flag", out)

    def test_user_audio_needs_rights_and_is_flagged(self):
        with self.assertRaises(ReelMetaError):
            clean_reel_meta(base(music={"id": "a1", "name": "Song", "license": "user_audio"}), None)
        with self.assertRaises(ReelMetaError):
            clean_reel_meta(base(music={"id": "a1", "name": "Song", "license": "user_audio", "rights_confirmed": "yes"}), None)
        out = clean_reel_meta(base(music={"id": "a1", "name": "Song", "license": "user_audio", "rights_confirmed": True}), None)
        self.assertTrue(out["copyright_flag"])
        self.assertTrue(out["music"]["copyright_flag"])

    def test_unknown_music_license_is_refused(self):
        with self.assertRaises(ReelMetaError):
            clean_reel_meta(base(music={"id": "1", "name": "x", "license": "CC-BY"}), None)

    def test_music_without_id_or_name_is_ignored(self):
        self.assertNotIn("music", clean_reel_meta(base(music={"id": "", "name": "x", "license": "CC0"}), None))
        self.assertNotIn("music", clean_reel_meta(base(music={"id": "1", "name": "  ", "license": "CC0"}), None))

    def test_captions_are_sorted_clamped_and_overlap_free(self):
        cues = [
            {"s": 3, "e": 5, "t": "second"},
            {"s": 0, "e": 3.5, "t": "first"},
            {"s": 6, "e": 6.05, "t": "too short"},
            {"s": 7, "e": 8, "t": "   "},
            {"s": -1, "e": 2, "t": "negative"},
            "junk",
        ]
        out = clean_reel_meta(base(captions={"language": "hi", "cues": cues}), None)
        got = [(c["s"], c["e"], c["t"]) for c in out["captions"]["cues"]]
        self.assertEqual(got, [(0.0, 3.0, "first"), (3.0, 5.0, "second")])
        self.assertEqual(out["captions"]["language"], "hi")

    def test_caption_language_falls_back_and_cue_count_is_capped(self):
        cues = [{"s": i, "e": i + 0.9, "t": f"c{i}"} for i in range(60)]
        out = clean_reel_meta(base(duration=70, captions={"language": "<bad>", "cues": cues}), None)
        # all 60 cues are valid; only the first 40 survive the cap
        self.assertEqual(out["captions"]["language"], "en")
        self.assertEqual(len(out["captions"]["cues"]), 40)

    def test_template_must_be_known(self):
        self.assertEqual(clean_reel_meta(base(template="tips3"), None)["template"], "tips3")
        self.assertNotIn("template", clean_reel_meta(base(template="made-up"), None))

    def test_clean_post_metadata_only_touches_the_reel_key(self):
        meta = {"other": {"a": 1}, "reel": base(junk=1)}
        out = clean_post_metadata(meta, "video", None)
        self.assertEqual(out["other"], {"a": 1})
        self.assertNotIn("junk", out["reel"])

    def test_reel_block_is_removed_from_non_video_posts(self):
        out = clean_post_metadata({"other": 1, "reel": base()}, "image", None)
        self.assertEqual(out, {"other": 1})

    def test_invalid_reel_block_is_removed_not_stored(self):
        out = clean_post_metadata({"reel": {"v": 9}}, "video", None)
        self.assertEqual(out, {})


class RemixSourceTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.author = User.objects.create_user(username="author", password="x")

    def video(self, **kw):
        kw.setdefault("post_type", "video")
        kw.setdefault("visibility", "public")
        kw.setdefault("category", "tech")
        post = Post.objects.create(user=kw.pop("user", self.author), content="c", **kw)
        PostMedia.objects.create(post=post, media_type="video", file="posts/x.mp4", file_name="x.mp4", file_size_bytes=1, mime_type="video/mp4")
        return post

    def remix(self, post, mode="duet"):
        return base(remix={"mode": mode, "source_post_id": str(post.id)})

    def test_public_video_can_be_remixed(self):
        src = self.video()
        out = clean_reel_meta(self.remix(src), self.me)
        self.assertEqual(out["remix"]["source_post_id"], str(src.id))
        self.assertEqual(out["remix"]["mode"], "duet")

    def test_missing_deleted_archived_or_non_video_source_is_refused(self):
        for kw in ({"is_deleted": True}, {"is_archived": True}, {"post_type": "text"}):
            with self.assertRaises(ReelMetaError, msg=str(kw)):
                clean_reel_meta(self.remix(self.video(**kw)), self.me)
        with self.assertRaises(ReelMetaError):
            clean_reel_meta(base(remix={"mode": "duet", "source_post_id": "not-a-uuid"}), self.me)

    def test_private_source_of_someone_else_is_refused(self):
        with self.assertRaises(ReelMetaError):
            clean_reel_meta(self.remix(self.video(visibility="private")), self.me)

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ReelMetaError):
            clean_reel_meta(self.remix(self.video(), mode="steal"), self.me)

    def test_remixes_turned_off_by_the_creator(self):
        src = self.video(metadata={"reel": {"v": 1, "allow_remix": False}})
        with self.assertRaises(ReelMetaError):
            clean_reel_meta(self.remix(src), self.me)
        # the creator may still remix his own reel
        out = clean_reel_meta(self.remix(src), self.author)
        self.assertEqual(out["remix"]["mode"], "duet")


@override_settings(FREESOUND_API_KEY="k")
class TrendingMusicTests(APITestCase):
    def setUp(self):
        cache.clear()

    def _resp(self):
        r = mock.Mock(status_code=200)
        r.json.return_value = {"results": [{"id": 1, "name": "n", "username": "u", "duration": 12, "previews": {"preview-hq-mp3": "https://cdn.freesound.org/p.mp3"}, "tags": ["bpm-120"]}], "count": 1}
        return r

    def test_q_is_still_required_without_trending(self):
        r = self.client.get(reverse("post-music-search"))
        self.assertEqual(r.status_code, 400)

    def test_trending_without_q_sorts_by_downloads(self):
        with mock.patch("post.views.requests.get", return_value=self._resp()) as g:
            r = self.client.get(reverse("post-music-search"), {"trending": "1"})
        self.assertEqual(r.status_code, 200, r.data)
        params = g.call_args.kwargs["params"]
        self.assertEqual(params["sort"], "downloads_desc")
        self.assertEqual(r.data["data"]["results"][0]["license"], "CC0")

    def test_normal_search_does_not_sort(self):
        with mock.patch("post.views.requests.get", return_value=self._resp()) as g:
            self.client.get(reverse("post-music-search"), {"q": "piano"})
        self.assertNotIn("sort", g.call_args.kwargs["params"])


class CreateAndEditTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.client.force_authenticate(self.me)

    def test_edit_cannot_add_or_change_the_reel_block(self):
        post = Post.objects.create(user=self.me, content="c", post_type="video", category="tech", visibility="public",
                                   metadata={"reel": base(), "keep": 1})
        from .serializers import PostEditSerializer
        req = mock.Mock(user=self.me)
        s = PostEditSerializer(post, data={"metadata": {"reel": {"v": 1, "clips": 9, "duration": 9}, "x": 2}}, partial=True, context={"request": req})
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data["metadata"]["reel"], base())
        self.assertEqual(s.validated_data["metadata"]["x"], 2)


class StudyMetaTests(SimpleTestCase):
    """Chapters / subject / exams / series / timer of the reel studio (F2 round 9). No database needed."""

    def test_chapters_are_sorted_trimmed_gapped_and_inside_the_reel(self):
        raw = [
            {"t": 20, "title": "  Formula  "},
            {"t": 0, "title": "Intro"},
            {"t": 20.4, "title": "too close"},
            {"t": 30, "title": ""},
            {"t": 99, "title": "after the end"},
            {"t": -1, "title": "negative"},
            "junk",
            {"t": "5", "title": "string time"},
        ]
        out = clean_reel_meta(base(duration=30, chapters=raw), None)
        self.assertEqual(out["chapters"], [{"t": 0.0, "title": "Intro"}, {"t": 20.0, "title": "Formula"}])

    def test_chapter_title_is_cut_and_count_is_capped(self):
        many = [{"t": float(i * 2), "title": "c%d" % i} for i in range(20)]
        out = clean_reel_meta(base(duration=60, chapters=many), None)
        self.assertEqual(len(out["chapters"]), 12)
        long = clean_reel_meta(base(duration=60, chapters=[{"t": 1, "title": "x" * 90}]), None)
        self.assertEqual(len(long["chapters"][0]["title"]), 40)

    def test_bad_chapters_value_is_ignored(self):
        self.assertNotIn("chapters", clean_reel_meta(base(chapters="x"), None))
        self.assertNotIn("chapters", clean_reel_meta(base(chapters=[]), None))

    def test_subject_and_exams_are_whitelisted(self):
        out = clean_reel_meta(base(subject="physics", exams=["jee", "jee", "neet", "boards", "upsc", "evil"]), None)
        self.assertEqual(out["subject"], "physics")
        self.assertEqual(out["exams"], ["jee", "neet", "boards"])
        out = clean_reel_meta(base(subject="<script>", exams=["nope", 5]), None)
        self.assertNotIn("subject", out)
        self.assertNotIn("exams", out)

    def test_series_rules(self):
        out = clean_reel_meta(base(series={"title": "  Algebra  ", "part": 2, "total": 5}), None)
        self.assertEqual(out["series"], {"title": "Algebra", "part": 2, "total": 5})
        out = clean_reel_meta(base(series={"title": "A", "part": 4, "total": 2}), None)
        self.assertEqual(out["series"]["total"], 4)  # total below part is lifted to part
        out = clean_reel_meta(base(series={"title": "A", "part": 3}), None)
        self.assertEqual(out["series"]["total"], 0)
        for bad in ({"title": "", "part": 1}, {"title": "A", "part": 0}, {"title": "A", "part": 100}, {"title": "A"}, "x"):
            self.assertNotIn("series", clean_reel_meta(base(series=bad), None))

    def test_timer_rules(self):
        out = clean_reel_meta(base(timer={"label": "Solve\x00 it", "seconds": 60}), None)
        self.assertEqual(out["timer"], {"label": "Solve  it", "seconds": 60})
        self.assertEqual(len(clean_reel_meta(base(timer={"label": "y" * 80, "seconds": 5}), None)["timer"]["label"]), 28)
        self.assertNotIn("timer", clean_reel_meta(base(timer={"label": "x", "seconds": 0}), None))
        self.assertNotIn("timer", clean_reel_meta(base(timer={"label": "x", "seconds": 500}), None))

    def test_links_with_a_bad_id_are_dropped_without_error(self):
        out = clean_reel_meta(base(notes={"post_id": "nope", "title": "x"}, test={"series_id": 5, "title": "y"}), None)
        self.assertNotIn("notes", out)
        self.assertNotIn("test", out)


class StudyLinkTests(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="author", password="x")
        self.other = User.objects.create_user(username="other", password="x")

    def _notes(self, **kw):
        d = dict(user=self.me, content="notes", title="Optics notes", post_type="text", kind=Post.KIND_RESOURCE, category="tech", visibility="public")
        d.update(kw)
        return Post.objects.create(**d)

    def test_visible_notes_post_is_linked_with_the_server_title(self):
        post = self._notes()
        out = clean_reel_meta(base(notes={"post_id": str(post.pk), "title": "client title"}), self.other)
        self.assertEqual(out["notes"], {"post_id": str(post.pk), "title": "Optics notes"})

    def test_deleted_or_ordinary_post_is_not_linked(self):
        gone = self._notes(is_deleted=True)
        plain = self._notes(kind=Post.KIND_POST)
        for p in (gone, plain):
            out = clean_reel_meta(base(notes={"post_id": str(p.pk), "title": "x"}), self.other)
            self.assertNotIn("notes", out)

    def test_private_notes_are_not_linkable_by_others(self):
        post = self._notes(visibility="private")
        self.assertNotIn("notes", clean_reel_meta(base(notes={"post_id": str(post.pk)}), self.other))
        self.assertIn("notes", clean_reel_meta(base(notes={"post_id": str(post.pk)}), self.me))

    def test_only_a_published_test_series_is_linked(self):
        from testseries.models import TestSeries

        def make(status):
            return TestSeries.objects.create(source=TestSeries.Source.INDIVIDUAL, creator=self.me, title="Mock %s" % status, status=status)

        pub = make(TestSeries.Status.PUBLISHED)
        draft = make(TestSeries.Status.DRAFT)
        out = clean_reel_meta(base(test={"series_id": str(pub.pk), "title": "x"}), self.me)
        self.assertEqual(out["test"], {"series_id": str(pub.pk), "title": "Mock published"})
        self.assertNotIn("test", clean_reel_meta(base(test={"series_id": str(draft.pk)}), self.me))


class ReelCommentSettingsPayloadTests(SimpleTestCase):
    """X3: the Reels payload carries who may comment (the Reels comment sheet reads it)."""

    def test_reel_serializer_exposes_comment_settings(self):
        from .serializers import ReelSerializer

        fields = ReelSerializer.Meta.fields
        self.assertIn("comments_limit", fields)
        self.assertIn("is_comments_disabled", fields)
