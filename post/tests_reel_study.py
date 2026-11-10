# post/tests_reel_study.py - X2: study info of a reel in the reels feed + the series endpoint.
#   * ReelSerializer.study  = stored metadata.reel series / chapters / subject / exams / notes / test (null when none)
#   * GET /post/reels/<id>/series/  = parts of the series, sorted, current + next marked, visibility rules
# NOT RUN when written. Run: python manage.py test post.tests_reel_study   (no migration needed)
from django.test import SimpleTestCase
from django.urls import reverse

from .models import Post
from .reel_meta import series_of, study_payload
from .tests_reels import ReelsBase


class _Fake:
    def __init__(self, metadata):
        self.metadata = metadata


class StudyPayloadUnitTests(SimpleTestCase):
    def test_none_without_reel_block(self):
        self.assertIsNone(study_payload(_Fake({})))
        self.assertIsNone(study_payload(_Fake(None)))
        self.assertIsNone(study_payload(_Fake({"reel": "x"})))
        self.assertIsNone(study_payload(_Fake({"reel": {"v": 1, "clips": 1, "duration": 5}})))

    def test_only_study_keys_are_returned(self):
        reel = {"v": 1, "clips": 2, "duration": 20, "music": {"id": "1"}, "captions": {"cues": []},
                "series": {"title": "Algebra", "part": 2, "total": 5},
                "chapters": [{"t": 0, "title": "Intro"}], "subject": "maths", "exams": ["jee"],
                "notes": {"post_id": "x", "title": "N"}, "test": {"series_id": "y", "title": "T"}}
        out = study_payload(_Fake({"reel": reel}))
        self.assertEqual(set(out), {"series", "chapters", "subject", "exams", "notes", "test"})
        self.assertEqual(out["series"]["part"], 2)

    def test_series_of(self):
        self.assertIsNone(series_of(_Fake({"reel": {"subject": "maths"}})))
        self.assertEqual(series_of(_Fake({"reel": {"series": {"title": "A", "part": 1, "total": 0}}}))["title"], "A")


def _meta(**study):
    return {"reel": dict({"v": 1, "clips": 1, "duration": 20}, **study)}


class StudyInFeedTests(ReelsBase):
    def test_reel_with_study_info_has_study_block(self):
        p = self.reel(metadata=_meta(series={"title": "Algebra", "part": 1, "total": 3}, subject="maths", exams=["jee"]))
        item = next(x for x in self.get().data["results"] if x["id"] == str(p.id))
        self.assertEqual(item["study"]["series"], {"title": "Algebra", "part": 1, "total": 3})
        self.assertEqual(item["study"]["subject"], "maths")
        self.assertEqual(item["study"]["exams"], ["jee"])

    def test_reel_without_study_info_has_null(self):
        p = self.reel()
        item = next(x for x in self.get().data["results"] if x["id"] == str(p.id))
        self.assertIsNone(item["study"])


class SeriesEndpointTests(ReelsBase):
    def part(self, n, title="Algebra", user=None, total=3, **kw):
        return self.reel(user or self.stranger, metadata=_meta(series={"title": title, "part": n, "total": total}), **kw)

    def url_of(self, post):
        return reverse("reel-series", args=[post.pk])

    def test_parts_sorted_by_part_with_current_and_next(self):
        p3, p1, p2 = self.part(3), self.part(1), self.part(2)
        r = self.client.get(self.url_of(p1))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x["part"] for x in r.data["parts"]], [1, 2, 3])
        self.assertEqual([x["id"] for x in r.data["parts"]], [str(p1.id), str(p2.id), str(p3.id)])
        self.assertEqual(r.data["series"], {"title": "Algebra", "part": 1, "total": 3})
        self.assertEqual(r.data["current_id"], str(p1.id))
        self.assertEqual(r.data["next_id"], str(p2.id))
        self.assertEqual([x["is_current"] for x in r.data["parts"]], [True, False, False])

    def test_last_part_has_no_next(self):
        self.part(1)
        p2 = self.part(2)
        r = self.client.get(self.url_of(p2))
        self.assertIsNone(r.data["next_id"])

    def test_title_match_ignores_case_and_other_authors_are_not_mixed(self):
        a = self.part(1, title="Algebra")
        b = self.part(2, title="ALGEBRA")
        self.part(1, title="Algebra", user=self.friend)  # same title, other author
        self.part(1, title="Geometry")
        r = self.client.get(self.url_of(a))
        self.assertEqual({x["id"] for x in r.data["parts"]}, {str(a.id), str(b.id)})

    def test_reel_without_series_returns_empty(self):
        p = self.reel()
        r = self.client.get(self.url_of(p))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["series"])
        self.assertEqual(r.data["parts"], [])

    def test_hidden_parts_are_left_out(self):
        a, b, c, d = self.part(1), self.part(2), self.part(3), self.part(4, total=4)
        Post.objects.filter(pk=b.pk).update(is_deleted=True)
        Post.objects.filter(pk=c.pk).update(visibility="followers")
        Post.objects.filter(pk=d.pk).update(is_archived=True)
        r = self.client.get(self.url_of(a))
        self.assertEqual([x["id"] for x in r.data["parts"]], [str(a.id)])

    def test_part_without_video_row_is_left_out(self):
        a = self.part(1)
        self.part(2, media=False)
        r = self.client.get(self.url_of(a))
        self.assertEqual(len(r.data["parts"]), 1)

    def test_unwatchable_reel_is_404(self):
        p = self.part(1)
        Post.objects.filter(pk=p.pk).update(is_deleted=True)
        self.assertEqual(self.client.get(self.url_of(p)).status_code, 404)

    def test_login_required(self):
        p = self.part(1)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url_of(p)).status_code, (401, 403))

    def test_part_title_falls_back_to_caption_first_line_and_thumbnail_is_absolute(self):
        p = self.part(1)
        Post.objects.filter(pk=p.pk).update(title="", content="Line one\nline two")
        from .models import PostMedia
        PostMedia.objects.filter(post=p).update(thumbnail="posts/thumbnails/t.jpg")
        part = self.client.get(self.url_of(p)).data["parts"][0]
        self.assertEqual(part["title"], "Line one")
        self.assertTrue(part["thumbnail"].startswith("http") and part["thumbnail"].endswith("t.jpg"))

    def test_constant_query_count(self):
        a = self.part(1)
        self.client.get(self.url_of(a))
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as few:
            self.client.get(self.url_of(a))
        for n in range(2, 7):
            self.part(n, total=6)
        with CaptureQueriesContext(connection) as many:
            self.client.get(self.url_of(a))
        self.assertEqual(len(few), len(many))
