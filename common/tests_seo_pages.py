"""
TASK T2.6 — public SEO pages (`common/seo_pages.py`).

Pins the privacy gates (what is indexed / noindex / 404), the HTML-escaping and
JSON-LD safety, canonical URLs, and the sitemap / robots.txt contents.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from assignments.models import (
    assigments, assigmentsSource, assigmentsStatus, assigmentsVisibility,
)
from message.models import Conversation, ConversationType, DoubtQuestion, Group, GroupMember
from testseries.models import TestSeries

User = get_user_model()

SITE = "https://learnscroll.test"


@override_settings(PUBLIC_WEB_BASE_URL=SITE)
class SeoPagesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.creator = User.objects.create(username="teach", first_name="Asha", last_name="Rao")

    # ---------------- test series ----------------
    def _series(self, **kw):
        defaults = dict(
            source=TestSeries.Source.INDIVIDUAL, creator=self.creator, title="NEET Biology Mock",
            description="Chapter-wise <b>biology</b> practice.", status=TestSeries.Status.PUBLISHED,
            subject="Biology", total_marks=100, duration_minutes=60,
        )
        defaults.update(kw)
        s = TestSeries.objects.create(**defaults)
        s.ensure_share_slug()
        return s

    def test_published_series_is_indexed_with_canonical_and_jsonld(self):
        s = self._series()
        r = self.client.get(f"/test-series/{s.share_slug}/?ref=ABCD1234")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('content="index,follow', html)
        self.assertIn(f'<link rel="canonical" href="{SITE}/test-series/{s.share_slug}/">', html)
        self.assertNotIn("ABCD1234", html.split("canonical")[1].split(">")[0])  # canonical drops ?ref=
        self.assertIn('"@type": "Course"', html)
        self.assertIn("learnscroll://test-series/%s?ref=ABCD1234" % s.share_slug, html)
        self.assertNotIn("X-Robots-Tag", r.headers)

    def test_series_html_is_escaped(self):
        s = self._series(title='<script>alert(1)</script>')
        html = self.client.get(f"/test-series/{s.share_slug}/").content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_tuitionclass_series_is_noindex(self):
        s = self._series(source=TestSeries.Source.TUITIONCLASS)
        r = self.client.get(f"/test-series/{s.share_slug}/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('content="noindex,nofollow"', r.content.decode())
        self.assertEqual(r.headers["X-Robots-Tag"], "noindex, nofollow")

    def test_draft_and_campus_series_keep_the_db_free_landing(self):
        draft = self._series(status=TestSeries.Status.DRAFT, title="Secret Draft")
        campus = self._series(source=TestSeries.Source.CAMPUS, title="Campus Only")
        for s in (draft, campus):
            r = self.client.get(f"/test-series/{s.share_slug}/")
            self.assertEqual(r.status_code, 200)
            html = r.content.decode()
            self.assertNotIn(s.title, html)           # nothing about the series leaks
            self.assertIn("Open this test series in LearnScroll", html)

    # ---------------- assignments ----------------
    def _assignment(self, **kw):
        defaults = dict(
            source=assigmentsSource.PERSONAL, title="Build a Calculator", posted_by=self.creator,
            description="Make a CLI calculator.", status=assigmentsStatus.PUBLISHED,
            visibility=assigmentsVisibility.PUBLIC, tags=["python", "beginner"],
        )
        defaults.update(kw)
        a = assigments.objects.create(**defaults)
        a.ensure_public_slug()
        a.save(update_fields=["public_slug"])
        return a

    def test_public_assignment_indexed_link_assignment_noindex(self):
        pub = self._assignment()
        link = self._assignment(title="Unlisted", visibility=assigmentsVisibility.LINK)
        r = self.client.get(f"/assignments/{pub.public_slug}/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('content="index,follow', r.content.decode())
        self.assertIn('"@type": "LearningResource"', r.content.decode())
        r2 = self.client.get(f"/assignments/{link.public_slug}/")
        self.assertEqual(r2.status_code, 200)
        self.assertIn('content="noindex,nofollow"', r2.content.decode())

    def test_private_draft_campus_assignments_404(self):
        private = self._assignment(visibility=assigmentsVisibility.PRIVATE)
        draft = self._assignment(status=assigmentsStatus.DRAFT)
        campus = self._assignment(source=assigmentsSource.CAMPUS)
        for a in (private, draft, campus):
            self.assertEqual(self.client.get(f"/assignments/{a.public_slug}/").status_code, 404)
        self.assertEqual(self.client.get("/assignments/nope/").status_code, 404)
        self.assertEqual(self.client.get("/assignments/bad$slug/").status_code, 404)

    # ---------------- doubts ----------------
    def _doubt(self, **kw):
        conv = Conversation.objects.create(type=ConversationType.GROUP)
        group = Group.objects.create(conversation=conv, name="Physics Batch A", created_by=self.creator)
        asker = User.objects.create(username="asker_%s" % User.objects.count())
        defaults = dict(
            group=group, conversation=conv, author=asker, text="Why is the sky blue?\nExplain simply.",
            is_answered=True, answer_text="Rayleigh scattering.",
        )
        defaults.update(kw)
        d = DoubtQuestion.objects.create(**defaults)
        return d

    def test_published_doubt_renders_qa_page_without_identity(self):
        d = self._doubt()
        d.make_public()
        r = self.client.get(f"/doubts/{d.public_slug}/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('"@type": "QAPage"', html)
        self.assertIn("Rayleigh scattering.", html)
        self.assertIn("Why is the sky blue?", html)
        self.assertNotIn(d.author.username, html)       # asker never exposed
        self.assertNotIn("Physics Batch A", html)       # private class name never exposed

    def test_unpublished_unanswered_anonymous_doubts_404(self):
        d = self._doubt()
        d.make_public()
        d.make_private()
        self.assertEqual(self.client.get(f"/doubts/{d.public_slug}/").status_code, 404)
        with self.assertRaises(ValueError):
            self._doubt(is_answered=False, answer_text="").make_public()
        with self.assertRaises(ValueError):
            self._doubt(is_anonymous=True).make_public()

    def test_republish_keeps_same_slug(self):
        d = self._doubt()
        first = d.make_public()
        d.make_private()
        self.assertEqual(d.make_public(), first)

    def test_publish_endpoint_is_teacher_only(self):
        from rest_framework.test import APIClient

        d = self._doubt()
        student = User.objects.create(username="stu")
        GroupMember.objects.create(group=d.group, user=student, role=GroupMember.Role.MEMBER)
        GroupMember.objects.create(group=d.group, user=self.creator, role=GroupMember.Role.ADMIN)
        url = f"/message/groups/{d.group_id}/doubts/{d.id}/public/"

        c = APIClient()
        c.force_authenticate(student)
        self.assertEqual(c.post(url).status_code, 403)

        c.force_authenticate(self.creator)
        r = c.post(url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["is_public"])
        self.assertTrue(r.data["public_url"].startswith(f"{SITE}/doubts/"))
        r = c.delete(url)
        self.assertFalse(r.data["is_public"])
        self.assertIsNone(r.data["public_url"])

    # ---------------- sitemap / robots ----------------
    def test_sitemap_lists_only_indexable_urls(self):
        good_series = self._series()
        self._series(source=TestSeries.Source.TUITIONCLASS, title="Class only")
        pub = self._assignment()
        self._assignment(title="Unlisted", visibility=assigmentsVisibility.LINK)
        d = self._doubt()
        d.make_public()

        r = self.client.get("/sitemap.xml")
        self.assertEqual(r.status_code, 200)
        xml = r.content.decode()
        self.assertIn(f"{SITE}/test-series/{good_series.share_slug}/", xml)
        self.assertIn(f"{SITE}/assignments/{pub.public_slug}/", xml)
        self.assertIn(f"{SITE}/doubts/{d.public_slug}/", xml)
        self.assertEqual(xml.count("<url>"), 3)

    def test_robots_txt(self):
        r = self.client.get("/robots.txt")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn(f"Sitemap: {SITE}/sitemap.xml", body)
        self.assertIn("Disallow: /message/", body)
        self.assertIn("Allow: /doubts/", body)
