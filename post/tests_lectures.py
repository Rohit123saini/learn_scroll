"""
post/tests_lectures.py  (round 4 - video lectures)

Pure helpers (chapters, SRT -> VTT, resume rule, series neighbours), the HTTP layer (post/lecture_views.py) and the auto
caption Celery tasks (post/tasks.py; AWS is mocked, nothing leaves the process).

Run:  python manage.py test post.tests_lectures

Notes
  * written by reading the code only (not executed when it was written)
  * the video rows point at a made-up file name: nothing here plays or transcodes a video
  * caption files go to a temp MEDIA_ROOT that is deleted afterwards
"""
import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APITestCase

from . import lectures
from .lectures import LectureError
from .models import (Post, PostAttachment, PostMedia, VideoCaptionTrack, VideoChapter, VideoProgress, VideoSeries,
                     VideoSeriesItem)

User = get_user_model()

_TMP_MEDIA = tempfile.mkdtemp(prefix="lectures_media_")


def tearDownModule():
    shutil.rmtree(_TMP_MEDIA, ignore_errors=True)


def _user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345")


def _lecture(user, title="Lecture", duration=600, visibility="public", **kw):
    post = Post.objects.create(user=user, title=title, content=kw.pop("content", "c"), post_type="video", category="education",
                               visibility=visibility, moderation_status="approved", **kw)
    media = PostMedia.objects.create(
        post=post, media_type="video", file="posts/lecture.mp4", file_name="lecture.mp4", file_size_bytes=1,
        mime_type="video/mp4", duration_seconds=duration)
    return post, media


# --------------------------------------------------------------------------- chapters
class ChapterParsingTests(SimpleTestCase):
    def test_youtube_style_text(self):
        got = lectures.parse_chapter_text("0:00 Introduction\n5:30 Newton's laws\n1:02:03 Long one")
        self.assertEqual(got, [{"start": 0, "title": "Introduction"}, {"start": 330, "title": "Newton's laws"},
                               {"start": 3723, "title": "Long one"}])

    def test_bullets_dashes_and_brackets(self):
        got = lectures.parse_chapter_text("- 0:00 - Start\n* (2:00) Middle\n[3:10]: End")
        self.assertEqual([c["start"] for c in got], [0, 120, 190])
        self.assertEqual([c["title"] for c in got], ["Start", "Middle", "End"])

    def test_hindi_title(self):
        self.assertEqual(lectures.parse_chapter_text("0:00 परिचय")[0]["title"], "परिचय")

    def test_ignores_junk_and_bad_times(self):
        got = lectures.parse_chapter_text("hello\n0:75 bad seconds\n2:00\n3:00 ok")
        self.assertEqual(got, [{"start": 180, "title": "ok"}])

    def test_duplicate_timestamp_keeps_last_and_sorts(self):
        got = lectures.parse_chapter_text("5:00 B\n0:00 A\n5:00 C")
        self.assertEqual(got, [{"start": 0, "title": "A"}, {"start": 300, "title": "C"}])

    def test_chapters_after_the_end_are_dropped(self):
        got = lectures.parse_chapter_text("0:00 A\n10:00 B", duration=300)
        self.assertEqual(got, [{"start": 0, "title": "A"}])

    def test_clean_chapters_validates(self):
        self.assertEqual(lectures.clean_chapters([{"start": "30", "title": " Two  words "}]), [(30, "Two words")])
        for bad, code in [
            ("x", "invalid_chapters"), ([5], "invalid_chapters"),
            ([{"start": "a", "title": "t"}], "invalid_chapter_start"), ([{"start": -1, "title": "t"}], "invalid_chapter_start"),
            ([{"start": 1, "title": "  "}], "chapter_title_required"),
        ]:
            with self.assertRaises(LectureError) as cm:
                lectures.clean_chapters(bad)
            self.assertEqual(cm.exception.code, code)
        with self.assertRaises(LectureError) as cm:
            lectures.clean_chapters([{"start": 900, "title": "t"}], duration=600)
        self.assertEqual(cm.exception.code, "chapter_after_end")

    def test_too_many_chapters(self):
        items = [{"start": i, "title": "t"} for i in range(lectures.MAX_CHAPTERS + 1)]
        with self.assertRaises(LectureError) as cm:
            lectures.clean_chapters(items)
        self.assertEqual(cm.exception.code, "too_many_chapters")


# --------------------------------------------------------------------------- captions
class CaptionConversionTests(SimpleTestCase):
    def test_srt_becomes_vtt(self):
        srt = "\ufeff1\r\n00:00:01,000 --> 00:00:03,500\r\nHello <i>world</i>\r\n\r\n2\r\n00:00:04,000 --> 00:00:05,000\r\nBye\r\n"
        self.assertEqual(lectures.srt_to_vtt(srt),
                         "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nHello world\n\n00:00:04.000 --> 00:00:05.000\nBye\n")

    def test_vtt_with_short_timings_and_header_notes(self):
        vtt = "WEBVTT\n\nNOTE made by hand\n\nintro\n00:01.000 --> 00:03.000 align:start\nनमस्ते\n"
        out = lectures.srt_to_vtt(vtt)
        self.assertIn("00:00:01.000 --> 00:00:03.000\nनमस्ते", out)
        self.assertNotIn("NOTE", out)

    def test_not_a_subtitle_file(self):
        with self.assertRaises(LectureError) as cm:
            lectures.srt_to_vtt("just some text")
        self.assertEqual(cm.exception.code, "invalid_captions")


# --------------------------------------------------------------------------- resume rule
class ResumeRuleTests(SimpleTestCase):
    def test_last_ten_seconds_count_as_finished(self):
        self.assertTrue(lectures.is_completed(90, 100))
        self.assertFalse(lectures.is_completed(89, 100))
        self.assertTrue(lectures.is_completed(970, 1000))   # 97 % of a long video
        self.assertFalse(lectures.is_completed(500, None))

    def test_clamp_position(self):
        self.assertEqual(lectures.clamp_position("12.9"), 12)
        self.assertEqual(lectures.clamp_position(500, 100), 100)
        for bad in ("abc", None, -3):
            with self.assertRaises(LectureError):
                lectures.clamp_position(bad)


class NeighbourTests(SimpleTestCase):
    class _P:
        def __init__(self, i):
            self.id = i

    def test_neighbours(self):
        entries = [(None, self._P(i), None) for i in (1, 2, 3)]
        self.assertEqual(lectures.neighbours(entries, 1)[0:2], (0, None))
        idx, prev, nxt = lectures.neighbours(entries, 2)
        self.assertEqual((idx, prev[1].id, nxt[1].id), (1, 1, 3))
        self.assertEqual(lectures.neighbours(entries, 3)[2], None)
        self.assertEqual(lectures.neighbours(entries, 9), (-1, None, None))


# --------------------------------------------------------------------------- HTTP layer
@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class LectureApiTests(APITestCase):
    def setUp(self):
        self.owner, self.viewer = _user("lec_owner"), _user("lec_viewer")
        self.post, self.media = _lecture(self.owner, "Newton's laws", content="0:00 Intro\n5:30 First law")

    def as_(self, user):
        self.client.force_authenticate(user)
        return self.client

    # ---- bundle / visibility
    def test_bundle_for_viewer(self):
        r = self.as_(self.viewer).get(f"/post/media/{self.media.id}/lecture/")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()["data"]
        self.assertEqual(d["title"], "Newton's laws")
        self.assertFalse(d["can_manage"])
        self.assertEqual((d["chapters"], d["captions"], d["notes"], d["progress"], d["series"]), ([], [], [], None, None))

    def test_private_post_is_a_404_for_others_but_not_the_author(self):
        post, media = _lecture(self.owner, "secret", visibility="private")
        self.assertEqual(self.as_(self.viewer).get(f"/post/media/{media.id}/lecture/").status_code, 404)
        r = self.as_(self.owner).get(f"/post/media/{media.id}/lecture/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["data"]["can_manage"])

    def test_unapproved_or_deleted_are_hidden(self):
        Post.objects.filter(pk=self.post.pk).update(moderation_status="pending")
        self.assertEqual(self.as_(self.viewer).get(f"/post/media/{self.media.id}/lecture/").status_code, 404)
        Post.objects.filter(pk=self.post.pk).update(moderation_status="approved", is_deleted=True)
        self.assertEqual(self.as_(self.owner).get(f"/post/media/{self.media.id}/lecture/").status_code, 404)

    # ---- chapters
    def test_owner_sets_chapters_from_text_and_viewer_sees_them(self):
        url = f"/post/media/{self.media.id}/chapters/"
        r = self.as_(self.owner).put(url, {"text": "0:00 Intro\n5:30 First law"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([c["start"] for c in r.json()["data"]], [0, 330])
        self.assertEqual(r.json()["warnings"], [])
        d = self.as_(self.viewer).get(f"/post/media/{self.media.id}/lecture/").json()["data"]
        self.assertEqual([c["title"] for c in d["chapters"]], ["Intro", "First law"])

    def test_chapters_replace_and_clear(self):
        url = f"/post/media/{self.media.id}/chapters/"
        self.as_(self.owner).put(url, {"chapters": [{"start": 10, "title": "A"}]}, format="json")
        r = self.client.put(url, {"chapters": []}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(VideoChapter.objects.filter(media=self.media).count(), 0)

    def test_first_chapter_not_at_zero_warns(self):
        r = self.as_(self.owner).put(f"/post/media/{self.media.id}/chapters/",
                                     {"chapters": [{"start": 20, "title": "A"}]}, format="json")
        self.assertEqual(r.json()["warnings"], ["first_chapter_not_zero"])

    def test_chapters_from_description(self):
        r = self.as_(self.owner).put(f"/post/media/{self.media.id}/chapters/", {"from_description": True}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.json()["data"]), 2)

    def test_text_without_timestamps_is_rejected(self):
        r = self.as_(self.owner).put(f"/post/media/{self.media.id}/chapters/", {"text": "no times here"}, format="json")
        self.assertEqual((r.status_code, r.json()["code"]), (400, "no_chapters_found"))

    def test_only_the_owner_can_edit(self):
        r = self.as_(self.viewer).put(f"/post/media/{self.media.id}/chapters/", {"chapters": []}, format="json")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "not_owner"))

    # ---- resume
    def test_progress_roundtrip_and_completion(self):
        url = f"/post/media/{self.media.id}/progress/"
        self.assertIsNone(self.as_(self.viewer).get(url).json()["data"])
        r = self.client.put(url, {"position": 125}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()["data"]
        self.assertEqual((d["position"], d["completed"], d["resume_at"]), (125, False, 123))
        b = self.client.get(f"/post/media/{self.media.id}/lecture/").json()["data"]["progress"]
        self.assertEqual(b["position"], 125)
        d = self.client.put(url, {"position": 595}, format="json").json()["data"]
        self.assertTrue(d["completed"])
        self.assertEqual(d["resume_at"], 0)          # a finished lecture starts again from 0
        self.assertEqual(VideoProgress.objects.filter(user=self.viewer, media=self.media).count(), 1)

    def test_progress_rejects_bad_numbers_and_clamps(self):
        url = f"/post/media/{self.media.id}/progress/"
        self.assertEqual(self.as_(self.viewer).put(url, {"position": "x"}, format="json").status_code, 400)
        self.assertEqual(self.client.put(url, {"position": -5}, format="json").status_code, 400)
        self.assertEqual(self.client.put(url, {"position": 99999}, format="json").json()["data"]["position"], 600)

    def test_continue_item(self):
        self.as_(self.viewer).put(f"/post/media/{self.media.id}/progress/", {"position": 100}, format="json")
        d = self.client.get("/post/lectures/continue/").json()["data"]
        self.assertEqual((d["post_id"], d["position"]), (str(self.post.id), 100))
        self.client.put(f"/post/media/{self.media.id}/progress/", {"position": 598}, format="json")   # finished
        self.assertIsNone(self.client.get("/post/lectures/continue/").json()["data"])

    def test_continue_item_hidden_when_post_went_private(self):
        self.as_(self.viewer).put(f"/post/media/{self.media.id}/progress/", {"position": 100}, format="json")
        Post.objects.filter(pk=self.post.pk).update(visibility="private")
        self.assertIsNone(self.client.get("/post/lectures/continue/").json()["data"])

    # ---- captions
    def test_upload_srt_captions(self):
        srt = "1\n00:00:01,000 --> 00:00:02,000\nनमस्ते\n"
        f = SimpleUploadedFile("hi.srt", srt.encode("utf-8"), content_type="text/plain")
        r = self.as_(self.owner).post(f"/post/media/{self.media.id}/captions/", {"language": "hi", "file": f}, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()["data"]
        self.assertEqual((d["language"], d["source"], d["status"]), ("hi", "upload", "ready"))
        self.assertTrue(d["url"])
        track = VideoCaptionTrack.objects.get(media=self.media, language="hi")
        self.assertIn("00:00:01.000 --> 00:00:02.000", track.file.read().decode("utf-8"))
        # a viewer sees it, a replacement keeps ONE row per language
        self.assertEqual(len(self.as_(self.viewer).get(f"/post/media/{self.media.id}/lecture/").json()["data"]["captions"]), 1)
        f2 = SimpleUploadedFile("hi2.vtt", b"WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nnew\n")
        self.as_(self.owner).post(f"/post/media/{self.media.id}/captions/", {"language": "hi", "file": f2}, format="multipart")
        self.assertEqual(VideoCaptionTrack.objects.filter(media=self.media).count(), 1)

    def test_caption_upload_validation(self):
        url = f"/post/media/{self.media.id}/captions/"
        self.as_(self.owner)
        bad_lang = SimpleUploadedFile("a.vtt", b"WEBVTT\n")
        self.assertEqual(self.client.post(url, {"language": "fr", "file": bad_lang}, format="multipart").json()["code"], "invalid_language")
        txt = SimpleUploadedFile("a.txt", b"hello")
        self.assertEqual(self.client.post(url, {"language": "en", "file": txt}, format="multipart").json()["code"], "unsupported_type")
        junk = SimpleUploadedFile("a.vtt", b"not subtitles")
        self.assertEqual(self.client.post(url, {"language": "en", "file": junk}, format="multipart").json()["code"], "invalid_captions")
        self.assertEqual(self.client.post(url, {"language": "en"}, format="json").json()["code"], "file_required")

    def test_viewer_cannot_add_captions_and_sees_only_ready_ones(self):
        VideoCaptionTrack.objects.create(media=self.media, language="en", source="auto", status="processing")
        r = self.as_(self.viewer).post(f"/post/media/{self.media.id}/captions/", {"language": "en", "auto": True}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get(f"/post/media/{self.media.id}/lecture/").json()["data"]["captions"], [])
        owner_view = self.as_(self.owner).get(f"/post/media/{self.media.id}/captions/").json()["data"]
        self.assertEqual(owner_view[0]["status"], "processing")

    @override_settings(CAPTIONS_S3_BUCKET="", AWS_STORAGE_BUCKET_NAME="")
    def test_auto_captions_unavailable_without_a_bucket(self):
        r = self.as_(self.owner).post(f"/post/media/{self.media.id}/captions/", {"language": "hi", "auto": True}, format="json")
        self.assertEqual((r.status_code, r.json()["code"]), (503, "auto_captions_unavailable"))

    @override_settings(CAPTIONS_S3_BUCKET="caps")
    def test_auto_captions_queue_a_job_once(self):
        url = f"/post/media/{self.media.id}/captions/"
        with mock.patch("post.lecture_views._safe_delay", return_value=True) as delay:
            r = self.as_(self.owner).post(url, {"language": "hi", "auto": True}, format="json")
            self.assertEqual(r.status_code, 202, r.content)
            self.assertEqual(r.json()["data"]["status"], "pending")
            again = self.client.post(url, {"language": "hi", "auto": True}, format="json")   # already running
            self.assertEqual(again.status_code, 200)
            self.assertEqual(delay.call_count, 1)

    @override_settings(CAPTIONS_S3_BUCKET="caps")
    def test_auto_captions_broker_down(self):
        with mock.patch("post.lecture_views._safe_delay", return_value=False):
            r = self.as_(self.owner).post(f"/post/media/{self.media.id}/captions/", {"language": "en", "auto": True}, format="json")
        self.assertEqual((r.status_code, r.json()["code"]), (503, "queue_unavailable"))
        self.assertEqual(VideoCaptionTrack.objects.get(media=self.media).status, "failed")

    @override_settings(CAPTIONS_S3_BUCKET="caps", LECTURE_AUTO_CAPTION_MAX_SECONDS=300)
    def test_auto_captions_refuse_a_very_long_video(self):
        r = self.as_(self.owner).post(f"/post/media/{self.media.id}/captions/", {"language": "en", "auto": True}, format="json")
        self.assertEqual(r.json()["code"], "video_too_long")

    def test_delete_captions(self):
        VideoCaptionTrack.objects.create(media=self.media, language="en", source="auto", status="failed")
        r = self.as_(self.owner).delete(f"/post/media/{self.media.id}/captions/?language=en")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(VideoCaptionTrack.objects.filter(media=self.media).exists())
        self.assertEqual(self.client.delete(f"/post/media/{self.media.id}/captions/?language=en").status_code, 404)

    # ---- notes
    def test_attach_existing_pdf_as_notes_and_detach(self):
        att = PostAttachment.objects.create(post=self.post, kind="pdf", file="post_resources/x.pdf", original_name="n.pdf",
                                            mime="application/pdf", size=10, pages=3)
        url = f"/post/media/{self.media.id}/notes/"
        r = self.as_(self.owner).post(url, {"attachment_id": str(att.id)}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(r.json()["data"]), 1)
        self.assertEqual(len(self.as_(self.viewer).get(f"/post/media/{self.media.id}/lecture/").json()["data"]["notes"]), 1)
        r = self.as_(self.owner).delete(f"{url}{att.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"], [])
        self.assertFalse(PostAttachment.objects.filter(pk=att.pk).exists())   # a video post: the PDF only existed as notes

    def test_notes_reject_a_pdf_of_another_post(self):
        other, _m = _lecture(self.owner, "other")
        att = PostAttachment.objects.create(post=other, kind="pdf", file="post_resources/y.pdf", original_name="y.pdf",
                                            mime="application/pdf", size=10)
        r = self.as_(self.owner).post(f"/post/media/{self.media.id}/notes/", {"attachment_id": str(att.id)}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_notes_upload_must_be_a_pdf(self):
        png = SimpleUploadedFile("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 40, content_type="image/png")
        r = self.as_(self.owner).post(f"/post/media/{self.media.id}/notes/", {"file": png}, format="multipart")
        self.assertEqual(r.status_code, 400, r.content)

    def test_viewer_cannot_attach_notes(self):
        r = self.as_(self.viewer).post(f"/post/media/{self.media.id}/notes/", {"attachment_id": "x"}, format="json")
        self.assertEqual(r.status_code, 403)


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class SeriesApiTests(APITestCase):
    def setUp(self):
        self.owner, self.viewer, self.other = _user("ser_owner"), _user("ser_viewer"), _user("ser_other")
        self.p1, self.m1 = _lecture(self.owner, "L1")
        self.p2, self.m2 = _lecture(self.owner, "L2")
        self.p3, self.m3 = _lecture(self.owner, "L3")

    def as_(self, user):
        self.client.force_authenticate(user)
        return self.client

    def _series(self, **kw):
        body = {"title": "Physics 11", "is_public": True}
        body.update(kw)
        r = self.as_(self.owner).post("/post/series/", body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["data"]["id"]

    def _add(self, sid, *posts, section=""):
        r = self.as_(self.owner).post(f"/post/series/{sid}/items/", {"post_ids": [str(p.id) for p in posts], "section": section},
                                      format="json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_create_requires_a_title_and_a_valid_kind(self):
        self.as_(self.owner)
        self.assertEqual(self.client.post("/post/series/", {"title": "  "}, format="json").json()["code"], "title_required")
        self.assertEqual(self.client.post("/post/series/", {"title": "x", "kind": "zzz"}, format="json").json()["code"], "invalid_kind")

    def test_classroom_series_needs_a_class_you_teach(self):
        r = self.as_(self.owner).post("/post/series/", {"title": "x", "kind": "classroom", "classroom_id": "nope"}, format="json")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "not_your_classroom"))
        with mock.patch("post.lectures.classroom_is_taught_by", return_value=True):
            r = self.client.post("/post/series/", {"title": "Class 11", "kind": "classroom", "classroom_id": "abc"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(r.json()["data"]["is_public"])      # a class course is never public by default

    def test_add_order_sections_and_detail(self):
        sid = self._series()
        self._add(sid, self.p1, self.p2, section="Unit 1")
        self._add(sid, self.p3, section="Unit 2")
        # adding twice is a no-op
        r = self.client.post(f"/post/series/{sid}/items/", {"post_id": str(self.p1.id)}, format="json")
        self.assertEqual(r.json()["data"]["added"], 0)
        r = self.client.put(f"/post/series/{sid}/order/", {"order": [str(self.p3.id), str(self.p1.id)],
                                                          "sections": {str(self.p3.id): "Intro"}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        d = self.as_(self.viewer).get(f"/post/series/{sid}/").json()["data"]
        self.assertEqual([e["title"] for e in d["entries"]], ["L3", "L1", "L2"])     # p2 was not listed: kept, after the rest
        self.assertEqual([e["section"] for e in d["entries"]], ["Intro", "Unit 1", "Unit 1"])
        self.assertEqual(d["continue_post_id"], str(self.p3.id))

    def test_only_own_video_posts_can_be_added(self):
        sid = self._series()
        text_post = Post.objects.create(user=self.owner, post_type="text", content="x", visibility="public",
                                        moderation_status="approved")
        foreign, _m = _lecture(self.other, "not mine")
        self.as_(self.owner)
        for pid in (text_post.id, foreign.id):
            r = self.client.post(f"/post/series/{sid}/items/", {"post_id": str(pid)}, format="json")
            self.assertEqual((r.status_code, r.json()["code"]), (400, "not_a_lecture"))

    def test_remove_item_renumbers(self):
        sid = self._series()
        self._add(sid, self.p1, self.p2, self.p3)
        self.assertEqual(self.client.delete(f"/post/series/{sid}/items/{self.p2.id}/").status_code, 200)
        self.assertEqual(list(VideoSeriesItem.objects.filter(series_id=sid).order_by("position").values_list("position", flat=True)),
                         [0, 1])
        self.assertEqual(self.client.delete(f"/post/series/{sid}/items/{self.p2.id}/").status_code, 404)

    def test_visibility_of_a_series(self):
        private_id = self._series(title="private one", is_public=False)
        public_id = self._series(title="public one", is_public=True)
        self.as_(self.viewer)
        self.assertEqual(self.client.get(f"/post/series/{private_id}/").status_code, 404)
        self.assertEqual(self.client.get(f"/post/series/{public_id}/").status_code, 200)
        titles = [s["title"] for s in self.client.get("/post/series/").json()["data"]]
        self.assertEqual(titles, ["public one"])
        mine = [s["title"] for s in self.as_(self.owner).get("/post/series/?mine=1").json()["data"]]
        self.assertEqual(sorted(mine), ["private one", "public one"])

    def test_only_the_owner_changes_a_series(self):
        sid = self._series()
        self.as_(self.viewer)
        self.assertEqual(self.client.patch(f"/post/series/{sid}/", {"title": "hack"}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(f"/post/series/{sid}/").status_code, 403)
        self.assertEqual(self.client.post(f"/post/series/{sid}/items/", {"post_id": str(self.p1.id)}, format="json").status_code, 403)
        self.assertEqual(self.client.put(f"/post/series/{sid}/order/", {"order": []}, format="json").status_code, 403)

    def test_patch_and_delete(self):
        sid = self._series()
        r = self.as_(self.owner).patch(f"/post/series/{sid}/", {"title": "New name", "is_public": False}, format="json")
        self.assertEqual((r.json()["data"]["title"], r.json()["data"]["is_public"]), ("New name", False))
        self.assertEqual(self.client.delete(f"/post/series/{sid}/").status_code, 200)
        self.assertFalse(VideoSeries.objects.filter(pk=sid).exists())

    def test_next_and_previous_lecture(self):
        sid = self._series()
        self._add(sid, self.p1, self.p2, self.p3)
        self.as_(self.viewer)
        d = self.client.get(f"/post/{self.p2.id}/next-lecture/?series={sid}").json()["data"]
        self.assertEqual((d["index"], d["total"]), (1, 3))
        self.assertEqual((d["previous"]["post_id"], d["next"]["post_id"]), (str(self.p1.id), str(self.p3.id)))
        self.assertEqual(d["next"]["media_id"], str(self.m3.id))
        last = self.client.get(f"/post/{self.p3.id}/next-lecture/?series={sid}").json()["data"]
        self.assertIsNone(last["next"])
        # series omitted -> the first visible series that holds the lecture
        self.assertEqual(self.client.get(f"/post/{self.p1.id}/next-lecture/").json()["data"]["id"], sid)
        # a lecture that is in no series
        lonely, _m = _lecture(self.owner, "alone")
        self.assertIsNone(self.client.get(f"/post/{lonely.id}/next-lecture/").json()["data"])
        # a series the viewer cannot open
        hidden = self._series(title="hidden", is_public=False)
        self.assertEqual(self.as_(self.viewer).get(f"/post/{self.p1.id}/next-lecture/?series={hidden}").status_code, 404)

    def test_next_skips_a_lecture_the_viewer_cannot_watch(self):
        sid = self._series()
        self._add(sid, self.p1, self.p2, self.p3)
        Post.objects.filter(pk=self.p2.pk).update(visibility="private")
        d = self.as_(self.viewer).get(f"/post/{self.p1.id}/next-lecture/?series={sid}").json()["data"]
        self.assertEqual(d["next"]["post_id"], str(self.p3.id))
        self.assertEqual(d["total"], 2)

    def test_bundle_carries_series_navigation(self):
        sid = self._series()
        self._add(sid, self.p1, self.p2)
        d = self.as_(self.viewer).get(f"/post/media/{self.m1.id}/lecture/?series={sid}").json()["data"]
        self.assertEqual(d["series"]["next"]["post_id"], str(self.p2.id))
        self.assertIsNone(self.client.get(f"/post/media/{self.m3.id}/lecture/?series={sid}").json()["data"]["series"])  # not in it

    def test_series_detail_shows_my_progress(self):
        sid = self._series()
        self._add(sid, self.p1, self.p2)
        self.as_(self.viewer).put(f"/post/media/{self.m1.id}/progress/", {"position": 599}, format="json")
        d = self.client.get(f"/post/series/{sid}/").json()["data"]
        self.assertEqual(d["completed_count"], 1)
        self.assertEqual(d["continue_post_id"], str(self.p2.id))


# --------------------------------------------------------------------------- auto caption tasks (AWS mocked)
@override_settings(MEDIA_ROOT=_TMP_MEDIA, CAPTIONS_S3_BUCKET="caps", AWS_REGION="ap-south-1")
class CaptionTaskTests(TestCase):
    def setUp(self):
        self.owner = _user("cap_owner")
        _post, self.media = _lecture(self.owner, "cap lecture")
        self.track = VideoCaptionTrack.objects.create(media=self.media, language="hi", source="auto", status="pending")

    def test_generate_starts_one_job_with_the_right_language(self):
        from . import tasks
        transcribe = mock.Mock()
        with mock.patch("boto3.client", side_effect=lambda name, **kw: transcribe if name == "transcribe" else mock.Mock()), \
                mock.patch.object(tasks, "_caption_media_uri", return_value=("s3://media/posts/lecture.mp4", False)):
            self.assertEqual(tasks.generate_video_captions.run(str(self.track.id)), "started")
            self.assertEqual(tasks.generate_video_captions.run(str(self.track.id)), "skipped")   # second delivery does nothing
        kwargs = transcribe.start_transcription_job.call_args.kwargs
        self.assertEqual(kwargs["LanguageCode"], "hi-IN")
        self.assertEqual(kwargs["OutputBucketName"], "caps")
        self.assertEqual(kwargs["Subtitles"]["Formats"], ["vtt"])
        self.assertEqual(transcribe.start_transcription_job.call_count, 1)
        self.track.refresh_from_db()
        self.assertEqual(self.track.status, "processing")
        self.assertTrue(self.track.job_name.startswith(f"lecture-{self.track.id}-"))

    def test_generate_marks_failed_when_aws_refuses(self):
        from . import tasks
        transcribe = mock.Mock()
        transcribe.start_transcription_job.side_effect = RuntimeError("boom")
        with mock.patch("boto3.client", return_value=transcribe), \
                mock.patch.object(tasks, "_caption_media_uri", return_value=("s3://media/x.mp4", False)):
            self.assertEqual(tasks.generate_video_captions.run(str(self.track.id)), "failed")
        self.track.refresh_from_db()
        self.assertEqual(self.track.status, "failed")
        self.assertIn("start_failed", self.track.error)

    @override_settings(CAPTIONS_S3_BUCKET="", AWS_STORAGE_BUCKET_NAME="")
    def test_generate_without_a_bucket_fails_cleanly(self):
        from . import tasks
        self.assertEqual(tasks.generate_video_captions.run(str(self.track.id)), "failed")
        self.track.refresh_from_db()
        self.assertEqual((self.track.status, self.track.error), ("failed", "not_configured"))

    def _processing(self):
        VideoCaptionTrack.objects.filter(pk=self.track.pk).update(status="processing", job_name="job-1")

    def _clients(self, job_state, body=b"", failure=""):
        transcribe, s3 = mock.Mock(), mock.Mock()
        job = {"TranscriptionJobStatus": job_state, "FailureReason": failure}
        transcribe.get_transcription_job.return_value = {"TranscriptionJob": job}
        s3.get_object.return_value = {"Body": mock.Mock(read=mock.Mock(return_value=body))}
        s3.list_objects_v2.return_value = {}
        return lambda name, **kw: transcribe if name == "transcribe" else s3

    def test_poll_saves_the_vtt_when_the_job_completes(self):
        from . import tasks
        self._processing()
        vtt = b"WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nNamaste\n"
        with mock.patch("boto3.client", side_effect=self._clients("COMPLETED", vtt)):
            self.assertEqual(tasks.poll_video_caption_jobs.run(), 1)
        self.track.refresh_from_db()
        self.assertEqual(self.track.status, "ready")
        self.assertIn("Namaste", self.track.file.read().decode("utf-8"))

    def test_poll_marks_failed_and_silent_videos(self):
        from . import tasks
        self._processing()
        with mock.patch("boto3.client", side_effect=self._clients("FAILED", failure="Bad audio")):
            self.assertEqual(tasks.poll_video_caption_jobs.run(), 1)
        self.track.refresh_from_db()
        self.assertEqual((self.track.status, self.track.error), ("failed", "Bad audio"))
        self._processing()
        with mock.patch("boto3.client", side_effect=self._clients("COMPLETED", b"WEBVTT\n\n")):
            tasks.poll_video_caption_jobs.run()
        self.track.refresh_from_db()
        self.assertEqual((self.track.status, self.track.error), ("failed", "no_speech"))

    def test_poll_leaves_a_running_job_alone_until_the_timeout(self):
        from datetime import timedelta
        from django.utils import timezone
        from . import tasks
        self._processing()
        with mock.patch("boto3.client", side_effect=self._clients("IN_PROGRESS")):
            self.assertEqual(tasks.poll_video_caption_jobs.run(), 0)
            self.track.refresh_from_db()
            self.assertEqual(self.track.status, "processing")
            VideoCaptionTrack.objects.filter(pk=self.track.pk).update(updated_at=timezone.now() - timedelta(hours=7))
            self.assertEqual(tasks.poll_video_caption_jobs.run(), 1)
        self.track.refresh_from_db()
        self.assertEqual((self.track.status, self.track.error), ("failed", "timeout"))

    def test_poll_fails_a_track_that_was_never_started(self):
        from datetime import timedelta
        from django.utils import timezone
        from . import tasks
        VideoCaptionTrack.objects.filter(pk=self.track.pk).update(updated_at=timezone.now() - timedelta(hours=1))
        with mock.patch("boto3.client", side_effect=self._clients("IN_PROGRESS")):
            tasks.poll_video_caption_jobs.run()
        self.track.refresh_from_db()
        self.assertEqual((self.track.status, self.track.error), ("failed", "not_started"))
