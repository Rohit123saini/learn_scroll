"""
post/tests_resources.py  (P1)

Backend tests for the knowledge-sharing work: hashtag helpers, audience rules,
POST/GET /post/resources/, attachment download rules, hashtag search/suggest,
convert-to-doubt, and the `hashtags` / `media_alt_texts` fields of POST /post/create/.

Run:  python manage.py test post.tests_resources

Notes
  * written by reading the code only (not executed when it was written)
  * uses only image uploads (PNG made with Pillow) so no PDF library is needed
  * files go to a temp MEDIA_ROOT that is deleted afterwards
"""
import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from user_profile.models import Follow
from . import hashtags as ht
from .audience import can_view_post, filter_posts_visible_to
from .models import CloseFriend, Hashtag, Post, PostAttachment, PostMedia

User = get_user_model()

_TMP_MEDIA = tempfile.mkdtemp(prefix="p1_media_")


def tearDownModule():
    shutil.rmtree(_TMP_MEDIA, ignore_errors=True)


def _user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345")


def _png(name="a.png", size=(8, 8)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _post(user, **kw):
    fields = dict(user=user, post_type="text", content="hello", visibility="public", moderation_status="approved")
    fields.update(kw)
    return Post.objects.create(**fields)


def _results(resp):
    data = resp.json()
    return data["results"] if isinstance(data, dict) and "results" in data else data


# --------------------------------------------------------------------------- helpers
class HashtagHelperTests(SimpleTestCase):
    def test_normalize_tag(self):
        self.assertEqual(ht.normalize_tag("#Physics "), "physics")
        self.assertEqual(ht.normalize_tag("PHYSICS"), "physics")
        self.assertEqual(ht.normalize_tag("a b"), "")          # space is not allowed
        self.assertEqual(ht.normalize_tag(""), "")
        self.assertEqual(ht.normalize_tag("x" * 51), "")       # too long

    def test_normalize_list_dedupes_and_caps(self):
        self.assertEqual(ht.normalize_list(["#A", "a", "B", "!!"]), ["a", "b"])
        many = [f"t{i}" for i in range(30)]
        self.assertEqual(len(ht.normalize_list(many)), ht.MAX_HASHTAGS_PER_POST)

    def test_hindi_tag_kept(self):
        self.assertEqual(ht.normalize_tag("#भौतिकी"), "भौतिकी")

    def test_extract_from_text(self):
        self.assertEqual(ht.extract_from_text("learn #Physics and #maths_10 now"), ["Physics", "maths_10"])

    def test_clean_topics(self):
        self.assertEqual(ht.clean_topics("Physics, class 10 , PHYSICS"), ["Physics", "class 10"])
        self.assertEqual(len(ht.clean_topics([str(i) for i in range(30)])), 10)


# --------------------------------------------------------------------------- audience
class AudienceTests(APITestCase):
    def setUp(self):
        self.author = _user("aud_author")
        self.viewer = _user("aud_viewer")
        self.public = _post(self.author, visibility="public")
        self.conn = _post(self.author, visibility="connections")
        self.close = _post(self.author, visibility="close_friends")
        self.private = _post(self.author, visibility="private")

    def test_stranger_sees_only_public(self):
        self.assertTrue(can_view_post(self.public, self.viewer))
        for p in (self.conn, self.close, self.private):
            self.assertFalse(can_view_post(p, self.viewer))

    def test_author_sees_everything(self):
        for p in (self.public, self.conn, self.close, self.private):
            self.assertTrue(can_view_post(p, self.author))

    def test_follower_sees_connections_only(self):
        Follow.objects.create(follower=self.viewer, following=self.author, status=Follow.Status.ACCEPTED)
        self.assertTrue(can_view_post(self.conn, self.viewer))
        self.assertFalse(can_view_post(self.close, self.viewer))

    def test_close_friend(self):
        CloseFriend.objects.create(owner=self.author, friend=self.viewer)
        self.assertTrue(can_view_post(self.close, self.viewer))
        self.assertFalse(can_view_post(self.private, self.viewer))

    def test_filter_matches_can_view(self):
        Follow.objects.create(follower=self.viewer, following=self.author, status=Follow.Status.ACCEPTED)
        ids = set(filter_posts_visible_to(Post.objects.all(), self.viewer).values_list("id", flat=True))
        self.assertEqual(ids, {self.public.id, self.conn.id})

    def test_anonymous_only_public(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertTrue(can_view_post(self.public, AnonymousUser()))
        self.assertFalse(can_view_post(self.conn, AnonymousUser()))


# --------------------------------------------------------------------------- resources API
@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class ResourceApiTests(APITestCase):
    def setUp(self):
        self.teacher = _user("res_teacher")
        self.student = _user("res_student")
        self.url = reverse("post-resources")

    def _create(self, user=None, files=None, **extra):
        self.client.force_authenticate(user or self.teacher)
        body = {"title": "Newton notes", "subject": "Physics", "level": "Class 11",
                "content": "Chapter 3 #Motion", "hashtags": '["Laws", "motion"]',
                "topics": '["Newton", "Class 11"]'}
        body.update(extra)
        body = {k: v for k, v in body.items() if v is not None}
        body["files"] = files if files is not None else [_png()]
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, body, format="multipart")

    def test_create_ok(self):
        r = self._create()
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.content)
        post = Post.objects.get(kind=Post.KIND_RESOURCE)
        self.assertEqual(post.subject, "Physics")
        self.assertEqual(post.hashtags, ["laws", "motion"])      # chips + #text merged, de-duplicated
        self.assertEqual(post.topics, ["Newton", "Class 11"])
        self.assertEqual(PostAttachment.objects.filter(post=post).count(), 1)
        self.assertEqual(Hashtag.objects.get(name="motion").posts_count, 1)

    def test_subject_file_required(self):
        # T3.1: the title is optional now (tests_t3_flexible_post covers the auto title); subject + file still are.
        self.assertEqual(self._create(subject="").json()["code"], "subject_required")
        self.client.force_authenticate(self.teacher)
        r = self.client.post(self.url, {"title": "t", "subject": "s"}, format="multipart")
        self.assertEqual(r.json()["code"], "file_required")

    def test_bad_file_rejected_and_nothing_saved(self):
        bad = SimpleUploadedFile("evil.exe", b"MZ\x90\x00not-allowed", content_type="application/octet-stream")
        r = self._create(files=[_png(), bad])
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(r.json()["code"], "unsupported_type")
        self.assertFalse(Post.objects.filter(kind=Post.KIND_RESOURCE).exists())

    def test_extension_mismatch(self):
        f = _png("photo.pdf")
        self.assertEqual(self._create(files=[f]).json()["code"], "extension_mismatch")

    def test_too_many_hashtags(self):
        tags = ",".join(f"t{i}" for i in range(11))
        self.assertEqual(self._create(hashtags=tags, content="x").json()["code"], "too_many_hashtags")

    def test_invalid_visibility_and_past_schedule(self):
        self.assertEqual(self._create(visibility="nobody").json()["code"], "invalid_visibility")
        self.assertEqual(self._create(scheduled_at="2000-01-01T00:00:00Z").json()["code"], "schedule_in_past")

    def test_list_respects_audience(self):
        self._create(visibility="private", title="Private notes")
        self._create(visibility="public", title="Public notes")
        self.client.force_authenticate(self.student)
        titles = [p["title"] for p in _results(self.client.get(self.url))]
        self.assertEqual(titles, ["Public notes"])
        self.client.force_authenticate(self.teacher)
        titles = {p["title"] for p in _results(self.client.get(self.url, {"mine": "1"}))}
        self.assertEqual(titles, {"Public notes", "Private notes"})

    def test_list_filters(self):
        self._create(title="Newton notes")
        self._create(title="Algebra", subject="Maths", hashtags='["algebra"]', content="", topics='["Quadratics"]')
        self.client.force_authenticate(self.student)
        self.assertEqual(len(_results(self.client.get(self.url, {"subject": "maths"}))), 1)
        self.assertEqual(len(_results(self.client.get(self.url, {"tag": "#Algebra"}))), 1)
        self.assertEqual(len(_results(self.client.get(self.url, {"topic": "quadr"}))), 1)
        self.assertEqual(len(_results(self.client.get(self.url, {"q": "newton"}))), 1)
        self.assertEqual(len(_results(self.client.get(self.url, {"file_kind": "image"}))), 2)
        self.assertEqual(len(_results(self.client.get(self.url, {"file_kind": "pdf"}))), 0)

    def test_download_rules(self):
        self._create(download_allowed="false")
        att = PostAttachment.objects.get()
        url = reverse("post-attachment-download", kwargs={"attachment_id": att.id})
        self.client.force_authenticate(self.student)
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(r.json()["code"], "download_not_allowed")
        self.client.force_authenticate(self.teacher)               # the author may always download
        self.assertEqual(self.client.get(url).status_code, status.HTTP_302_FOUND)

    def test_download_hidden_post_is_404(self):
        self._create(visibility="private", download_allowed="true")
        att = PostAttachment.objects.get()
        self.client.force_authenticate(self.student)
        r = self.client.get(reverse("post-attachment-download", kwargs={"attachment_id": att.id}))
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_download_allowed_redirects(self):
        self._create(download_allowed="true")
        att = PostAttachment.objects.get()
        self.client.force_authenticate(self.student)
        r = self.client.get(reverse("post-attachment-download", kwargs={"attachment_id": att.id}))
        self.assertEqual(r.status_code, status.HTTP_302_FOUND)

    def test_attachment_payload_hides_download_url_when_off(self):
        self._create(download_allowed="false")
        self.client.force_authenticate(self.student)
        item = _results(self.client.get(self.url))[0]
        att = item["attachments"][0]
        self.assertFalse(att["can_download"])
        self.assertIsNone(att["download_url"])
        self.assertFalse(item["download_allowed"])


# --------------------------------------------------------------------------- hashtags API
class HashtagApiTests(APITestCase):
    def setUp(self):
        self.me = _user("ht_me")
        self.client.force_authenticate(self.me)
        Hashtag.objects.create(name="physics", posts_count=5)
        Hashtag.objects.create(name="phy_lab", posts_count=2)
        Hashtag.objects.create(name="maths", posts_count=9)
        Hashtag.objects.create(name="dead", posts_count=0)

    def test_search_prefix_most_used_first(self):
        r = self.client.get(reverse("hashtag-search"), {"q": "#PHY"}).json()
        self.assertEqual([x["hashtag"] for x in r["results"]], ["physics", "phy_lab"])
        self.assertEqual(r["results"][0]["count"], 5)

    def test_search_empty_and_zero_count(self):
        self.assertEqual(self.client.get(reverse("hashtag-search")).json()["results"], [])
        self.assertEqual(self.client.get(reverse("hashtag-search"), {"q": "dead"}).json()["results"], [])

    def test_suggest_mine_first_then_trending(self):
        _post(self.me, hashtags=["mine_tag"])
        r = self.client.get(reverse("hashtag-suggest")).json()
        self.assertIn("mine_tag", r["mine"])
        self.assertIn("maths", r["trending"])
        self.assertNotIn("dead", r["trending"])


# --------------------------------------------------------------------------- convert to doubt
class ConvertToDoubtTests(APITestCase):
    def setUp(self):
        self.me = _user("cd_me")
        self.other = _user("cd_other")

    def _url(self, post):
        return reverse("post-convert-to-doubt", kwargs={"id": post.id})

    def test_author_converts(self):
        p = _post(self.me, content="why is the sky blue?")
        self.client.force_authenticate(self.me)
        r = self.client.post(self._url(p))
        self.assertEqual(r.status_code, 200, r.content)
        p.refresh_from_db()
        self.assertEqual((p.post_type, p.kind), ("doubt", Post.KIND_DOUBT))

    def test_only_author(self):
        p = _post(self.me)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(self._url(p)).status_code, 403)

    def test_cannot_convert_resource_or_empty(self):
        res = _post(self.me, kind=Post.KIND_RESOURCE)
        empty = _post(self.me, content=None, title=None)
        self.client.force_authenticate(self.me)
        self.assertEqual(self.client.post(self._url(res)).json()["code"], "cannot_convert")
        self.assertEqual(self.client.post(self._url(empty)).json()["code"], "empty_post")


# --------------------------------------------------------------------------- normal create
@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class PostCreateP1Tests(APITestCase):
    def setUp(self):
        self.me = _user("pc_me")
        self.client.force_authenticate(self.me)

    def _create(self, **body):
        body.setdefault("post_type", "text")
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("post-create"), body, format="multipart")

    def test_json_encoded_hashtags_are_decoded_and_merged(self):
        r = self._create(content="note #Algebra", hashtags='["Maths", "algebra"]')
        self.assertIn(r.status_code, (200, 201), r.content)
        post = Post.objects.get(user=self.me)
        self.assertEqual(post.hashtags, ["maths", "algebra"])
        self.assertEqual(Hashtag.objects.get(name="maths").posts_count, 1)

    def test_media_alt_texts_saved_per_file(self):
        r = self._create(post_type="image", content="pics", category="general",
                         media_files=[_png("a.png"), _png("b.png")],
                         media_types='["image","image"]',
                         media_alt_texts='["A red square", ""]')
        self.assertIn(r.status_code, (200, 201), r.content)
        alts = list(PostMedia.objects.filter(post__user=self.me).order_by("display_order").values_list("alt_text", flat=True))
        self.assertEqual(alts, ["A red square", ""])

    def test_json_encoded_topics_are_decoded_and_cleaned(self):
        r = self._create(content="notes", topics='["Physics", "physics", "  Class   10 "]')
        self.assertIn(r.status_code, (200, 201), r.content)
        post = Post.objects.get(user=self.me)
        self.assertEqual(post.topics, ["Physics", "Class 10"])


# --------------------------------------------------------------------------- edit: topics only
class PostEditTopicsTests(APITestCase):
    """The app sets Topics through PATCH /post/<id>/edit/ after a chunked (large video) upload."""

    def setUp(self):
        self.me = _user("et_me")
        self.client.force_authenticate(self.me)

    def test_topics_only_edit_does_not_mark_post_edited(self):
        p = _post(self.me)
        r = self.client.patch(reverse("post-edit", kwargs={"id": p.id}), {"topics": ["Maths", "maths", "Algebra"]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        p.refresh_from_db()
        self.assertEqual(p.topics, ["Maths", "Algebra"])
        self.assertFalse(p.is_edited)

    def test_content_edit_still_marks_post_edited(self):
        p = _post(self.me)
        r = self.client.patch(reverse("post-edit", kwargs={"id": p.id}), {"content": "changed"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        p.refresh_from_db()
        self.assertTrue(p.is_edited)
