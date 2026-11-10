"""P4 - /u/<username>/ link preview (Open Graph). Written, NOT run."""
from datetime import date

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

User = get_user_model()


@override_settings(PUBLIC_WEB_BASE_URL="https://learnscroll.example")
class ProfileLandingCardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.pub = User.objects.create_user(
            username="asha", password="x", first_name="Asha", last_name="Verma",
            bio="JEE 2027 | Physics lover", date_of_birth=date(1998, 1, 1),
        )
        self.priv = User.objects.create_user(username="secret_one", password="x", first_name="Hidden", is_private=True)
        self.minor = User.objects.create_user(
            username="kiddo", password="x", first_name="Kid", date_of_birth=date(date.today().year - 12, 1, 1)
        )

    def test_public_profile_has_og_tags(self):
        html = self.client.get("/u/asha/").content.decode()
        self.assertIn('property="og:title"', html)
        self.assertIn("Asha Verma (@asha) on LearnScroll", html)
        self.assertIn("https://learnscroll.example/u/asha", html)
        self.assertIn("learnscroll://u/asha", html)

    def test_private_minor_and_unknown_are_generic_and_identical(self):
        pages = [self.client.get(f"/u/{n}/").content.decode() for n in ("secret_one", "kiddo", "nobody_here")]
        for html in pages:
            self.assertNotIn('property="og:title"', html)
            self.assertNotIn("Hidden", html)
            self.assertNotIn("Kid", html)
            self.assertIn("Open this profile in LearnScroll", html)

    def test_inactive_user_is_generic(self):
        User.objects.filter(pk=self.pub.pk).update(is_active=False)
        cache.clear()
        html = self.client.get("/u/asha/").content.decode()
        self.assertNotIn("Asha", html)

    def test_bad_username_404(self):
        self.assertEqual(self.client.get("/u/bad name/").status_code, 404)
