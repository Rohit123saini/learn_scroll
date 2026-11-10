"""
P11 - Profile QA + safety.  Written, NOT run:
    python manage.py test user_profile.tests_profile_safety

  PrivacyMatrixTests     viewer (self / stranger / pending / accepted / blocked-by-me / blocked-me) x target (public / private)
  MinorMaskingTests      under-18 target: location + links hidden from EVERYBODY else (even accepted followers), birthday never
  LinkSafetyTests        javascript: / data: / user@host / localhost / private IP refused; read-time guard for old rows
  BioModerationTests     automod hook on bio, on the other profile text, and the minor contact-info rule
  UsernameHardeningTests trailing newline, look-alike characters, case-insensitive reserved names
"""
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from common.moderation import has_contact_info
from login.models import clean_profile_links, is_public_http_url

from . import profile_header as ph
from .models import AutoModerationFlag, BlockUser, Follow

User = get_user_model()


def mk(username, **extra):
    return User.objects.create_user(username=username, email=f"{username}@example.com", password="pass12345", **extra)


def minor_dob():
    t = date.today()
    return date(t.year - 15, 6, 15)


def get_profile(viewer, name):
    c = APIClient()
    c.force_authenticate(viewer)
    return c.get(f"/profile/profile/{name}/")


# ------------------------------------------------------------------------------------------------ privacy matrix
class PrivacyMatrixTests(TestCase):
    """Every viewer relationship against a public and a private account."""

    def setUp(self):
        self.viewer = mk("viewer_q")
        self.public = mk("public_q", bio="hello public", location="Kota")
        self.private = mk("private_q", bio="secret bio", is_private=True)

    def _rel(self, target, kind):
        if kind == "pending":
            Follow.objects.create(follower=self.viewer, following=target, status=Follow.Status.PENDING)
        elif kind == "accepted":
            Follow.objects.create(follower=self.viewer, following=target, status=Follow.Status.ACCEPTED)
        elif kind == "blocked_by_me":
            BlockUser.objects.create(blocker=self.viewer, blocked=target)
        elif kind == "blocked_me":
            BlockUser.objects.create(blocker=target, blocked=self.viewer)

    def _assert_full(self, r, bio):
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["profile_state"], "ok")
        self.assertFalse(r.data["is_restricted_view"])
        self.assertEqual(r.data["data"]["bio"], bio)
        self.assertIn("followers_count", r.data["data"])

    def _assert_card_only(self, r, state):
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["profile_state"], state)
        self.assertTrue(r.data["is_restricted_view"])
        for leaked in ("bio", "followers_count", "following_count", "posts_count", "links", "location"):
            self.assertNotIn(leaked, r.data["data"], f"{leaked} leaked in a {state} card")

    def test_self_sees_everything_even_when_private(self):
        self._assert_full(get_profile(self.private, "private_q"), "secret bio")

    def test_public_account_for_every_relationship(self):
        self._assert_full(get_profile(self.viewer, "public_q"), "hello public")  # stranger
        for kind in ("pending", "accepted"):
            Follow.objects.filter(follower=self.viewer).delete()
            self._rel(self.public, kind)
            with self.subTest(kind=kind):
                self._assert_full(get_profile(self.viewer, "public_q"), "hello public")

    def test_private_account_stranger_and_pending_get_the_card_only(self):
        self._assert_card_only(get_profile(self.viewer, "private_q"), "private")
        self._rel(self.private, "pending")
        self._assert_card_only(get_profile(self.viewer, "private_q"), "private")

    def test_private_account_accepted_follower_sees_everything(self):
        self._rel(self.private, "accepted")
        self._assert_full(get_profile(self.viewer, "private_q"), "secret bio")

    def test_blocked_by_me_is_a_card_even_for_a_public_account(self):
        self._rel(self.public, "blocked_by_me")
        self._assert_card_only(get_profile(self.viewer, "public_q"), "blocked_by_me")

    def test_they_blocked_me_is_404_for_public_and_private(self):
        self._rel(self.public, "blocked_me")
        self._rel(self.private, "blocked_me")
        self.assertEqual(get_profile(self.viewer, "public_q").status_code, 404)
        self.assertEqual(get_profile(self.viewer, "private_q").status_code, 404)

    def test_block_beats_an_accepted_follow(self):
        self._rel(self.private, "accepted")
        self._rel(self.private, "blocked_me")
        self.assertEqual(get_profile(self.viewer, "private_q").status_code, 404)

    def test_unknown_user_is_the_same_404(self):
        self.assertEqual(get_profile(self.viewer, "no_such_person").status_code, 404)

    def test_suspended_account_is_a_card_for_everyone_but_the_owner(self):
        User.objects.filter(pk=self.public.pk).update(is_active=False)
        self._assert_card_only(get_profile(self.viewer, "public_q"), "suspended")


# ------------------------------------------------------------------------------------------------ minors
class MinorMaskingTests(TestCase):
    def setUp(self):
        self.minor = mk(
            "minor_q", date_of_birth=minor_dob(), location="Kota", show_birthday=True,
            links=[{"title": "x", "url": "https://example.com"}],
        )
        self.viewer = mk("viewer_m")

    def _data(self, viewer):
        return get_profile(viewer, "minor_q").data["data"]

    def test_hidden_from_a_stranger(self):
        d = self._data(self.viewer)
        self.assertEqual(d["location"], "")
        self.assertEqual(d["links"], [])
        self.assertIsNone(d["birthday"])

    def test_hidden_even_from_an_accepted_follower(self):
        Follow.objects.create(follower=self.viewer, following=self.minor, status=Follow.Status.ACCEPTED)
        d = self._data(self.viewer)
        self.assertEqual(d["location"], "")
        self.assertEqual(d["links"], [])
        self.assertIsNone(d["birthday"])

    def test_the_minor_still_sees_their_own_details(self):
        d = self._data(self.minor)
        self.assertEqual(d["location"], "Kota")
        self.assertEqual(len(d["links"]), 1)
        self.assertIsNone(d["birthday"])  # a minor's birthday is never shown, even to themselves

    def test_owner_only_extras_are_not_sent_to_others(self):
        d = self._data(self.viewer)
        for owner_only in ("completeness", "username_change", "edit_info"):
            self.assertNotIn(owner_only, d)


# ------------------------------------------------------------------------------------------------ links
class LinkSafetyTests(SimpleTestCase):
    BAD = [
        "javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,hi", "file:///etc/passwd", "ftp://example.com",
        "vbscript:x", "//example.com", "example.com", "https://", "",
        "https://google.com@evil.com", "https://user:pw@example.com", "http://localhost/admin",
        "http://app.localhost", "http://127.0.0.1", "http://10.0.0.5/x", "http://192.168.1.1", "http://169.254.169.254/latest",
        "http://[::1]/", "http://intranet", "https://example.com/a b", "https://exa\tmple.com",
        "https://printer.local", "https://db.internal/x",
    ]
    GOOD = ["https://example.com", "http://x.org/b?c=1#d", "https://sub.domain.co.in/path", "https://8.8.8.8/"]

    def test_is_public_http_url(self):
        for u in self.BAD:
            with self.subTest(bad=u):
                self.assertFalse(is_public_http_url(u))
        for u in self.GOOD:
            with self.subTest(good=u):
                self.assertTrue(is_public_http_url(u))

    def test_never_raises_on_odd_input(self):
        for odd in (None, 5, [], {}, b"https://example.com", "https://[bad"):
            self.assertFalse(is_public_http_url(odd))

    def test_clean_profile_links_refuses_every_bad_address(self):
        for u in self.BAD:
            if not u:
                continue
            with self.subTest(bad=u), self.assertRaises(ValidationError):
                clean_profile_links([{"title": "t", "url": u}])

    def test_clean_profile_links_still_accepts_good_ones_and_limits(self):
        out = clean_profile_links([{"title": " Site ", "url": u, "extra": 1} for u in self.GOOD])
        self.assertEqual([x["title"] for x in out], ["Site"] * 4)
        self.assertEqual(set(out[0]), {"title", "url"})
        with self.assertRaises(ValidationError):
            clean_profile_links([{"title": "t", "url": "https://example.com"}] * 6)
        with self.assertRaises(ValidationError):
            clean_profile_links([{"title": "x" * 41, "url": "https://example.com"}])

    def test_safe_links_drops_old_unsafe_rows_at_read_time(self):
        stored = [
            {"title": "ok", "url": "https://example.com"},
            {"title": "bad", "url": "javascript:alert(1)"},
            {"title": "disguise", "url": "https://google.com@evil.com"},
            "not a dict",
            {"url": 5},
            {"title": "t" * 100, "url": "https://example.org"},
        ]
        out = ph.safe_links(stored)
        self.assertEqual([l["url"] for l in out], ["https://example.com", "https://example.org"])
        self.assertEqual(len(out[1]["title"]), 40)
        self.assertEqual(ph.safe_links(None), [])
        self.assertEqual(ph.safe_links("x"), [])

    def test_bio_urls_use_the_same_rule(self):
        out = ph.bio_entities("see https://example.com/a and http://localhost/x and https://google.com@evil.com", resolve_mentions=False)
        self.assertEqual(out["urls"], ["https://example.com/a"])


class LinkReadTimeApiTests(TestCase):
    def test_an_old_unsafe_stored_link_never_reaches_another_person(self):
        owner = mk("linkowner")
        # written behind the validators (what an old row / admin edit / shell write looks like)
        User.objects.filter(pk=owner.pk).update(links=[
            {"title": "good", "url": "https://example.com"},
            {"title": "evil", "url": "javascript:alert(1)"},
        ])
        viewer = mk("linkviewer")
        d = get_profile(viewer, "linkowner").data["data"]
        self.assertEqual([l["url"] for l in d["links"]], ["https://example.com"])


# ------------------------------------------------------------------------------------------------ bio moderation
class BioModerationTests(TestCase):
    def _flags(self, user, target_type):
        return AutoModerationFlag.objects.filter(target_type=target_type, target_id=str(user.pk))

    def test_a_clean_bio_makes_no_flag(self):
        u = mk("clean_bio", bio="JEE 2027 aspirant, love physics")
        self.assertEqual(AutoModerationFlag.objects.filter(user=u).count(), 0)

    def test_a_profane_bio_is_flagged_for_review_but_not_changed(self):
        u = mk("bad_bio", bio="you are a fucking idiot")
        flags = self._flags(u, "bio")
        self.assertEqual(flags.count(), 1)
        self.assertEqual(flags.first().reason, "profanity")
        u.refresh_from_db()
        self.assertEqual(u.bio, "you are a fucking idiot")  # flag-only: nothing is hidden or deleted

    def test_editing_the_bio_later_is_screened_too(self):
        u = mk("edit_bio", bio="fine")
        self.assertEqual(self._flags(u, "bio").count(), 0)
        u.bio = "now a shit bio"
        u.save(update_fields=["bio"])
        self.assertEqual(self._flags(u, "bio").count(), 1)

    def test_saving_only_last_login_does_not_screen(self):
        u = mk("login_only")
        with mock.patch("user_profile.automod.screen_and_flag") as screen, mock.patch("user_profile.automod.screen_text") as st:
            u.save(update_fields=["last_login"])
        screen.assert_not_called()
        st.assert_not_called()

    def test_the_other_profile_text_is_screened(self):
        u = mk("extras_q", category_label="Student")
        u.pronouns = "fuck/you"
        u.save(update_fields=["pronouns"])
        self.assertEqual(self._flags(u, "user").count(), 1)
        u.location = "Kota"
        u.pronouns = "she/her"
        u.save(update_fields=["location", "pronouns"])
        self.assertEqual(self._flags(u, "user").count(), 1)  # the old flag stays, no new one for clean text

    def test_link_titles_are_screened(self):
        u = mk("title_q")
        u.links = [{"title": "my shit page", "url": "https://example.com"}]
        u.save(update_fields=["links"])
        self.assertEqual(self._flags(u, "user").count(), 1)

    def test_extras_never_queue_the_ai_pass(self):
        u = mk("noai_q")
        with self.settings(AUTOMOD_AI_ENABLED=True), mock.patch("user_profile.tasks.automod_ai_screen") as task:
            u.location = "Kota"
            u.save(update_fields=["location"])
        task.delay.assert_not_called()

    def test_an_adult_may_put_a_phone_number_in_the_bio(self):
        u = mk("adult_phone", bio="call me 98765 43210")
        self.assertEqual(self._flags(u, "user").filter(reason="minor_contact_info").count(), 0)

    def test_a_minor_phone_number_or_chat_handle_is_flagged_high(self):
        u = mk("minor_phone", date_of_birth=minor_dob(), bio="whatsapp me 98765 43210")
        f = self._flags(u, "user").filter(reason="minor_contact_info")
        self.assertEqual(f.count(), 1)
        self.assertEqual(f.first().severity, "high")
        v = mk("minor_insta", date_of_birth=minor_dob(), bio="my insta is cool_kid")
        self.assertEqual(self._flags(v, "user").filter(reason="minor_contact_info").count(), 1)

    def test_a_minor_with_a_normal_bio_is_not_flagged(self):
        u = mk("minor_ok", date_of_birth=minor_dob(), bio="Class 10, loves maths")
        self.assertEqual(AutoModerationFlag.objects.filter(user=u).count(), 0)


class ContactInfoHelperTests(SimpleTestCase):
    def test_has_contact_info(self):
        self.assertTrue(has_contact_info("call 9876543210"))
        self.assertTrue(has_contact_info("+91 98765-43210"))
        self.assertTrue(has_contact_info("ping me on Telegram"))
        self.assertFalse(has_contact_info("JEE 2027, rank 1500"))
        self.assertFalse(has_contact_info(""))
        self.assertFalse(has_contact_info(None))


# ------------------------------------------------------------------------------------------------ username
class UsernameHardeningTests(SimpleTestCase):
    def test_trailing_newline_is_not_a_valid_name(self):
        self.assertEqual(ph.username_format_error("ravi\n"), "bad_chars")
        self.assertEqual(ph.username_format_error("ravi\r\n"), "bad_chars")

    def test_look_alike_and_full_width_characters_are_refused(self):
        for v in ("аdmin", "ａdmin", "ravi​", "ravïi"):  # cyrillic a, fullwidth a, zero-width space, i-diaeresis
            with self.subTest(v=v):
                self.assertEqual(ph.username_format_error(v), "bad_chars")

    def test_reserved_names_are_case_insensitive(self):
        for v in ("ADMIN", "Support", "LearnScroll", "Deleted"):
            with self.subTest(v=v):
                self.assertEqual(ph.username_format_error(v), "reserved")
