"""P-A: research-paper filters of GET /post/resources/ (year / year_from / year_to / author / doi / paper / ?q= / in_files).
Written, NOT RUN. Run: python manage.py test post.tests_resource_paper_filters
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import Post, PostAttachment, ResourcePaper

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345")


def _resource(user, title, paper=None, file_text=""):
    post = Post.objects.create(
        user=user, post_type="text", kind=Post.KIND_RESOURCE, title=title, content="notes", subject="Physics",
        visibility="public", moderation_status="approved")
    if paper is not None:
        ResourcePaper.objects.create(post=post, **paper)
    if file_text:
        PostAttachment.objects.create(
            post=post, kind="pdf", file="resources/x.pdf", original_name="x.pdf", mime="application/pdf", size=1,
            search_text=file_text)
    return post


class ResourcePaperFilterTests(APITestCase):
    def setUp(self):
        self.me = _user("rp_me")
        self.client.force_authenticate(self.me)
        self.url = reverse("post-resources")
        self.p2019 = _resource(self.me, "Quantum notes", dict(
            authors=["Ada Lovelace"], authors_text="Ada Lovelace", doi="10.1000/abc", year=2019, venue="Nature"))
        self.p2021 = _resource(self.me, "Optics paper", dict(
            authors=["Alan Turing"], authors_text="Alan Turing; Grace Hopper", doi="10.2000/xyz", year=2021, venue="Science"))
        self.p2023 = _resource(self.me, "Plain notes with scan", paper=None, file_text="the refractive index of glass")

    def ids(self, **params):
        data = self.client.get(self.url, params).json()
        rows = data["results"] if isinstance(data, dict) and "results" in data else data
        return {str(r["id"]) for r in rows}

    def want(self, *posts):
        return {str(p.id) for p in posts}

    def test_paper_flag_only_posts_with_metadata(self):
        self.assertEqual(self.ids(paper="1"), self.want(self.p2019, self.p2021))

    def test_exact_year_and_range(self):
        self.assertEqual(self.ids(year=2019), self.want(self.p2019))
        self.assertEqual(self.ids(year_from=2020), self.want(self.p2021))
        self.assertEqual(self.ids(year_to=2019), self.want(self.p2019))
        self.assertEqual(self.ids(year_from=2019, year_to=2021), self.want(self.p2019, self.p2021))

    def test_backwards_year_range_is_swapped_not_empty(self):
        self.assertEqual(self.ids(year_from=2021, year_to=2019), self.want(self.p2019, self.p2021))

    def test_bad_year_value_is_ignored(self):
        self.assertEqual(self.ids(paper="1", year="abc"), self.want(self.p2019, self.p2021))

    def test_author_filter_is_case_insensitive_and_matches_any_author(self):
        self.assertEqual(self.ids(author="lovelace"), self.want(self.p2019))
        self.assertEqual(self.ids(author="grace"), self.want(self.p2021))

    def test_doi_filter_accepts_plain_url_and_doi_prefix_forms(self):
        for raw in ("10.1000/ABC", "https://doi.org/10.1000/abc", "doi:10.1000/abc"):
            self.assertEqual(self.ids(doi=raw), self.want(self.p2019), raw)

    def test_q_matches_a_pasted_doi_link(self):
        self.assertEqual(self.ids(q="https://doi.org/10.1000/abc"), self.want(self.p2019))
        self.assertEqual(self.ids(q="doi:10.2000/XYZ"), self.want(self.p2021))
        self.assertEqual(self.ids(q="http://dx.doi.org/10.2000/xyz"), self.want(self.p2021))

    def test_q_still_matches_a_partial_doi_title_venue_and_author(self):
        self.assertEqual(self.ids(q="10.1000"), self.want(self.p2019))
        self.assertEqual(self.ids(q="optics"), self.want(self.p2021))
        self.assertEqual(self.ids(q="nature"), self.want(self.p2019))
        self.assertEqual(self.ids(q="turing"), self.want(self.p2021))

    def test_q_matches_text_inside_files_unless_in_files_is_off(self):
        self.assertEqual(self.ids(q="refractive"), self.want(self.p2023))
        self.assertEqual(self.ids(q="refractive", in_files="0"), set())
        self.assertEqual(self.ids(q="refractive", in_files="false"), set())

    def test_filters_combine(self):
        self.assertEqual(self.ids(paper="1", year_from=2019, author="turing"), self.want(self.p2021))
        self.assertEqual(self.ids(paper="1", year=2019, author="turing"), set())
