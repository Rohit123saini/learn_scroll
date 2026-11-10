from django.test import TestCase

POST_ID = "3f2b8c1e-9a47-4d55-b0c2-6a1e7d9f4c10"


class PostLinkLandingTests(TestCase):
    def test_landing_opens_app_deep_link_without_db_lookup(self):
        for path in (f"/p/{POST_ID}/", f"/p/{POST_ID}"):
            r = self.client.get(path, follow=True)
            self.assertEqual(r.status_code, 200)
            self.assertIn(f"learnscroll://p/{POST_ID}", r.content.decode())

    def test_non_uuid_is_404(self):
        self.assertEqual(self.client.get("/p/not-a-uuid/").status_code, 404)
