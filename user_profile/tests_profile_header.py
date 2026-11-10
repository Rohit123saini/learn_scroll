"""
P1 - Profile header tests. NOT RUN (author had no run permission):
    python manage.py test user_profile.tests_profile_header

  UsernameRulesTests     format / reserved / case-insensitive taken / 14-day limit / case-only change / availability
  UsernameApiTests       PATCH /profile/update/ + GET /profile/username/check/
  HeaderFieldsApiTests   location, pronouns, links (max 5), category-label impersonation guard, cover, remove photo
  BioEntitiesTests       @mentions only for real accounts, #tags, http(s) links only
  VerifiedTests          tick always carries a reason; reason is staff-only
  CompletenessTests      percent adds up to 100, next hint, owner-only
  MaskingTests           under-18 accounts hide location + links from others, joined_at visible
"""
import io
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from . import profile_header as ph

User = get_user_model()
UPDATE = "/profile/update/"
CHECK = "/profile/username/check/"
ME = "/profile/"


def png_file(name="x.png", size=(40, 40)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def mk(username, **extra):
    return User.objects.create_user(username=username, email=f"{username}@example.com", password="pass12345", **extra)


class UsernameRulesTests(APITestCase):
    def setUp(self):
        self.user = mk("sam_user")
        mk("taken_one")

    def test_format_errors(self):
        cases = {
            "": "empty", "ab": "too_short", "x" * 31: "too_long", "has space": "bad_chars", "a-b-c": "bad_chars",
            "nice@name": "bad_chars", ".dotstart": "bad_edges", "dotend.": "bad_edges", "a..b": "bad_edges",
            "123456": "all_digits", "admin": "reserved", "LearnScroll": "reserved", "Support": "reserved",
        }
        for value, code in cases.items():
            with self.subTest(value=value):
                self.assertEqual(ph.username_format_error(value), code)

    def test_good_names(self):
        for value in ("abc", "a.b_c", "Sam_2007", "x" * 30, "topper.jee"):
            with self.subTest(value=value):
                self.assertIsNone(ph.username_format_error(value))

    def test_check_taken_is_case_insensitive(self):
        r = ph.check_username(self.user, "TAKEN_ONE")
        self.assertEqual((r["code"], r["available"], r["valid"]), ("taken", False, True))

    def test_check_free_name(self):
        r = ph.check_username(self.user, "  fresh_name ")
        self.assertEqual((r["code"], r["available"], r["username"]), ("ok", True, "fresh_name"))

    def test_check_current_name_is_same(self):
        r = ph.check_username(self.user, "sam_user")
        self.assertEqual((r["code"], r["available"]), ("same", True))

    def test_never_changed_can_change_now(self):
        s = ph.username_limit_state(self.user)
        self.assertTrue(s["can_change"])
        self.assertIsNone(s["next_change_at"])

    def test_limit_blocks_for_14_days_then_opens(self):
        now = timezone.now()
        self.user.username_changed_at = now - timedelta(days=3)
        s = ph.username_limit_state(self.user, now=now)
        self.assertFalse(s["can_change"])
        self.assertEqual(s["days_left"], 11)
        self.assertEqual(ph.check_username(self.user, "brand_new")["code"], "limit")
        self.user.username_changed_at = now - timedelta(days=14, seconds=1)
        self.assertTrue(ph.username_limit_state(self.user, now=now)["can_change"])

    @override_settings(PROFILE_USERNAME_CHANGE_DAYS=0)
    def test_zero_days_means_no_limit(self):
        self.user.username_changed_at = timezone.now()
        self.assertTrue(ph.username_limit_state(self.user)["can_change"])

    def test_case_only_change_is_free_even_inside_the_window(self):
        self.user.username_changed_at = timezone.now()
        r = ph.check_username(self.user, "Sam_User")
        self.assertEqual((r["code"], r["available"]), ("ok", True))
        self.assertTrue(ph.is_case_only_change(self.user, "Sam_User"))
        self.assertFalse(ph.is_case_only_change(self.user, "sam_user"))


class UsernameApiTests(APITestCase):
    def setUp(self):
        self.user = mk("api_user")
        mk("someone_else")
        self.client.force_authenticate(self.user)

    def patch(self, data):
        return self.client.patch(UPDATE, data, format="json")

    def test_change_saves_and_starts_the_14_day_clock(self):
        r = self.patch({"username": "new_name"})
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "new_name")
        self.assertIsNotNone(self.user.username_changed_at)
        self.assertFalse(r.json()["data"]["username_change"]["can_change"])

    def test_second_change_inside_the_window_is_400_and_keeps_the_name(self):
        self.patch({"username": "first_name"})
        r = self.patch({"username": "second_name"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("username", r.json()["errors"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "first_name")

    def test_taken_name_is_400_with_the_known_message(self):
        for spelling in ("someone_else", "SOMEONE_ELSE"):  # exact and different capitals: same answer
            with self.subTest(spelling=spelling):
                r = self.patch({"username": spelling})
                self.assertEqual(r.status_code, 400)
                self.assertIn("taken", " ".join(r.json()["errors"]["username"]).lower())
        self.user.refresh_from_db()
        self.assertIsNone(self.user.username_changed_at)

    def test_bad_names_are_400(self):
        for bad in ("ab", "x" * 31, "has space", "admin", "12345", "a..b"):
            with self.subTest(bad=bad):
                self.assertEqual(self.patch({"username": bad}).status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "api_user")

    def test_same_name_is_a_no_op_and_does_not_start_the_clock(self):
        self.assertEqual(self.patch({"username": "api_user", "bio": "hi"}).status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.username_changed_at)

    def test_capital_letters_only_change_does_not_start_the_clock(self):
        self.assertEqual(self.patch({"username": "Api_User"}).status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "Api_User")
        self.assertIsNone(self.user.username_changed_at)

    def test_legacy_username_that_breaks_the_new_rules_can_still_save_other_fields(self):
        User.objects.filter(pk=self.user.pk).update(username="old-style+name@x")
        self.assertEqual(self.patch({"username": "old-style+name@x", "bio": "still fine"}).status_code, 200)

    def test_check_endpoint_shapes(self):
        cases = {"fresh_one": "ok", "someone_else": "taken", "ab": "too_short", "admin": "reserved", "api_user": "same"}
        for name, code in cases.items():
            with self.subTest(name=name):
                r = self.client.get(CHECK, {"username": name})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["data"]["code"], code)
        self.assertTrue(r.json()["data"]["can_change"])

    def test_check_endpoint_reports_the_limit(self):
        self.patch({"username": "changed_now"})
        d = self.client.get(CHECK, {"username": "another_one"}).json()["data"]
        self.assertEqual(d["code"], "limit")
        self.assertFalse(d["can_change"])
        self.assertGreaterEqual(d["days_left"], 13)

    def test_check_endpoint_needs_the_parameter_and_a_login(self):
        self.assertEqual(self.client.get(CHECK).status_code, 400)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(CHECK, {"username": "abc"}).status_code, (401, 403))


class HeaderFieldsApiTests(APITestCase):
    def setUp(self):
        self.user = mk("head_user")
        self.client.force_authenticate(self.user)

    def patch(self, data, fmt="json"):
        return self.client.patch(UPDATE, data, format=fmt)

    def test_location_saves_trims_and_clears(self):
        r = self.patch({"location": "  Kota,   Rajasthan "})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["data"]["location"], "Kota, Rajasthan")
        self.assertEqual(self.patch({"location": ""}).status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.location, "")

    def test_location_refuses_links_markup_and_long_text(self):
        for bad in ("https://evil.example", "www.evil.example", "<b>x</b>", "x" * 61):
            with self.subTest(bad=bad):
                self.assertEqual(self.patch({"location": bad}).status_code, 400)

    def test_up_to_five_links_and_a_sixth_is_refused(self):
        link = lambda i: {"title": f"L{i}", "url": f"https://example.com/{i}"}  # noqa: E731
        self.assertEqual(self.patch({"links": [link(i) for i in range(5)]}).status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(len(self.user.links), 5)
        r = self.patch({"links": [link(i) for i in range(6)]})
        self.assertEqual(r.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(len(self.user.links), 5)

    def test_unsafe_link_schemes_are_refused(self):
        for url in ("javascript:alert(1)", "data:text/html,x", "ftp://example.com/a", "//example.com"):
            with self.subTest(url=url):
                self.assertEqual(self.patch({"links": [{"title": "x", "url": url}]}).status_code, 400)

    def test_category_label_cannot_pose_as_a_badge_unless_verified(self):
        for bad in ("Verified teacher", "OFFICIAL", "LearnScroll Team", "staff"):
            with self.subTest(bad=bad):
                self.assertEqual(self.patch({"category_label": bad}).status_code, 400)
        self.assertEqual(self.patch({"category_label": "JEE Aspirant"}).status_code, 200)
        User.objects.filter(pk=self.user.pk).update(is_verified=True)
        self.user.refresh_from_db()
        self.assertEqual(self.patch({"category_label": "Verified teacher"}).status_code, 200)

    def test_pronouns_whitespace_is_collapsed(self):
        self.assertEqual(self.patch({"pronouns": "  she /  her "}).json()["data"]["pronouns"], "she / her")

    def test_cannot_write_verified_fields_or_counters_through_the_api(self):
        r = self.patch({"is_verified": True, "verified_reason": "official", "followers_count": 999, "username_changed_at": None})
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_verified)
        self.assertEqual(self.user.verified_reason, "")
        self.assertEqual(self.user.followers_count, 0)

    def test_avatar_upload_then_remove(self):
        r = self.client.patch(UPDATE, {"profile_photo": png_file()}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.profile_photo)
        r = self.client.patch(UPDATE, {"remove_profile_photo": "true"}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertFalse(self.user.profile_photo)

    def test_cover_upload_then_remove(self):
        r = self.client.patch(UPDATE, {"cover_photo": png_file("cover.png", (300, 100))}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.cover_photo)
        self.assertTrue(r.json()["data"]["cover_photo"])
        r = self.client.patch(UPDATE, {"remove_cover_photo": "true"}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertFalse(self.user.cover_photo)

    def test_cover_rejects_non_images_and_wrong_types(self):
        bad = SimpleUploadedFile("cover.png", b"not an image", content_type="image/png")
        self.assertEqual(self.client.patch(UPDATE, {"cover_photo": bad}, format="multipart").status_code, 400)
        gif = SimpleUploadedFile("cover.gif", b"GIF89a", content_type="image/gif")
        self.assertEqual(self.client.patch(UPDATE, {"cover_photo": gif}, format="multipart").status_code, 400)

    @override_settings(PROFILE_COVER_MAX_BYTES=100)
    def test_cover_size_cap_has_its_own_setting(self):
        r = self.client.patch(UPDATE, {"cover_photo": png_file("cover.png", (600, 600))}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_remove_does_not_override_a_new_upload_in_the_same_request(self):
        r = self.client.patch(UPDATE, {"profile_photo": png_file(), "remove_profile_photo": "true"}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.profile_photo)

    def test_one_bad_field_saves_nothing(self):
        r = self.patch({"location": "Delhi", "links": [{"title": "x", "url": "javascript:1"}]})
        self.assertEqual(r.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.location, "")


class BioEntitiesTests(APITestCase):
    def setUp(self):
        mk("real_friend")
        mk("Another.One")
        inactive = mk("ghost_user")
        User.objects.filter(pk=inactive.pk).update(is_active=False)

    def test_mentions_only_for_real_active_accounts_with_canonical_spelling(self):
        out = ph.bio_entities("hi @REAL_FRIEND and @another.one, also @nobody_here and @ghost_user.")
        self.assertEqual(out["mentions"], ["real_friend", "Another.One"])

    def test_mentions_without_resolution_returns_every_candidate(self):
        out = ph.bio_entities("@a_b_c @abc", resolve_mentions=False)
        self.assertEqual(out["mentions"], ["a_b_c", "abc"])

    def test_email_addresses_are_not_mentions(self):
        self.assertEqual(ph.bio_entities("write to me@real_friend.com", resolve_mentions=False)["mentions"], [])

    def test_hashtags(self):
        out = ph.bio_entities("#JEE2027 #jee2027 #NEET #123 # x #hindi_medium")
        self.assertEqual(out["hashtags"], ["JEE2027", "NEET", "hindi_medium"])

    def test_urls_http_https_only_and_trailing_punctuation_trimmed(self):
        out = ph.bio_entities("see https://example.com/a. and http://x.org/b), javascript:alert(1) ftp://nope.com www.nope.com")
        self.assertEqual(out["urls"], ["https://example.com/a", "http://x.org/b"])

    def test_empty_and_none(self):
        for v in ("", None):
            self.assertEqual(ph.bio_entities(v), {"mentions": [], "hashtags": [], "urls": []})

    def test_lists_are_capped(self):
        text = " ".join(f"#tag{i}x" for i in range(60))
        self.assertEqual(len(ph.bio_entities(text)["hashtags"]), ph.MAX_ENTITIES)

    def test_api_returns_entities_for_the_profile(self):
        me = mk("bio_owner", bio="Hello @real_friend #NEET https://example.com")
        self.client.force_authenticate(me)
        d = self.client.get(ME).json()["data"]
        self.assertEqual(d["bio_entities"], {"mentions": ["real_friend"], "hashtags": ["NEET"], "urls": ["https://example.com"]})


class VerifiedTests(APITestCase):
    def test_not_verified_has_no_reason_or_label(self):
        info = ph.verified_info(mk("plain_one"))
        self.assertEqual(info, {"is_verified": False, "reason": "", "label": "", "explain": ""})

    def test_each_reason_has_label_and_explanation(self):
        for reason in ("identity", "teacher", "campus", "creator", "official"):
            with self.subTest(reason=reason):
                u = mk(f"v_{reason}", is_verified=True, verified_reason=reason)
                info = ph.verified_info(u)
                self.assertEqual(info["reason"], reason)
                self.assertTrue(info["label"] and info["explain"])

    def test_legacy_tick_without_reason_reads_as_verified_account(self):
        info = ph.verified_info(mk("legacy_tick", is_verified=True))
        self.assertEqual((info["reason"], info["label"]), ("identity", "Verified account"))

    def test_reason_without_the_tick_is_not_shown(self):
        self.assertEqual(ph.verified_info(mk("odd_one", verified_reason="teacher"))["reason"], "")

    def test_profile_api_shows_the_reason_to_others(self):
        teacher = mk("the_teacher", is_verified=True, verified_reason="teacher")
        viewer = mk("a_viewer")
        self.client.force_authenticate(viewer)
        d = self.client.get("/profile/profile/the_teacher/").json()["data"]
        self.assertEqual((d["is_verified"], d["verified_reason"], d["verified_label"]), (True, "teacher", "Verified teacher"))
        self.assertTrue(teacher.is_verified)

    def test_private_card_still_shows_why_the_tick_is_there(self):
        mk("priv_teacher", is_verified=True, verified_reason="campus", is_private=True)
        self.client.force_authenticate(mk("stranger_x"))
        body = self.client.get("/profile/profile/priv_teacher/").json()
        self.assertTrue(body["is_restricted_view"])
        self.assertEqual(body["data"]["verified_reason"], "campus")
        self.assertNotIn("location", body["data"])


class CompletenessTests(APITestCase):
    def test_weights_add_up_to_100(self):
        self.assertEqual(sum(w for _k, w, _t in ph.COMPLETENESS_ITEMS), 100)
        keys = [k for k, _w, _t in ph.COMPLETENESS_ITEMS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), set(ph.COMPLETENESS_HINTS))

    def test_empty_profile_is_zero_and_asks_for_the_heaviest_missing_item_first(self):
        c = ph.profile_completeness(mk("empty_one"))
        self.assertEqual(c["percent"], 0)
        self.assertEqual(c["next"], "photo")
        self.assertEqual(len(c["missing"]), len(ph.COMPLETENESS_ITEMS))

    def test_percent_follows_what_is_filled(self):
        u = mk("filling_up", first_name="Sam", bio="hello", target_exam="jee", study_class="12")
        c = ph.profile_completeness(u)
        self.assertEqual(c["percent"], 10 + 15 + 15 + 10)
        self.assertEqual(c["next"], "photo")
        self.assertEqual(set(c["done"]), {"name", "bio", "exam", "class"})

    def test_full_profile_is_100_with_nothing_next(self):
        u = mk("all_done", first_name="A", bio="b", target_exam="jee", study_class="12", category_label="Student",
               links=[{"title": "x", "url": "https://example.com"}], location="Kota", date_of_birth=date(2000, 1, 1))
        u.profile_photo.name = "profile/a.png"
        u.cover_photo.name = "profile_covers/a.png"
        c = ph.profile_completeness(u)
        self.assertEqual((c["percent"], c["next"], c["next_hint"], c["missing"]), (100, None, "", []))

    def test_next_hint_text(self):
        u = mk("hint_user", first_name="A", bio="b", study_class="12", category_label="S")
        u.profile_photo.name = "profile/a.png"
        c = ph.profile_completeness(u)
        self.assertEqual((c["next"], c["next_hint"]), ("exam", "Add your exam target"))

    def test_only_the_owner_gets_completeness_and_username_change(self):
        owner = mk("owner_one")
        mk("other_one")
        self.client.force_authenticate(owner)
        mine = self.client.get(ME).json()["data"]
        self.assertIn("completeness", mine)
        self.assertIn("username_change", mine)
        own_detail = self.client.get("/profile/profile/owner_one/").json()["data"]
        self.assertIn("completeness", own_detail)
        theirs = self.client.get("/profile/profile/other_one/").json()["data"]
        self.assertNotIn("completeness", theirs)
        self.assertNotIn("username_change", theirs)

    def test_update_response_carries_the_new_percent(self):
        u = mk("live_meter")
        self.client.force_authenticate(u)
        before = self.client.get(ME).json()["data"]["completeness"]["percent"]
        r = self.client.patch(UPDATE, {"bio": "now I have a bio"}, format="json")
        self.assertEqual(r.json()["data"]["completeness"]["percent"], before + 15)


class MaskingTests(APITestCase):
    def setUp(self):
        self.minor = mk(
            "young_one",
            date_of_birth=date.today().replace(year=date.today().year - 15),
            location="Kota",
            links=[{"title": "x", "url": "https://example.com"}],
            is_private=False,
        )
        self.adult = mk("grown_up", location="Delhi", links=[{"title": "x", "url": "https://example.com"}])
        self.viewer = mk("some_viewer")

    def test_minor_location_and_links_are_hidden_from_others(self):
        User.objects.filter(pk=self.minor.pk).update(is_private=False)
        self.client.force_authenticate(self.viewer)
        d = self.client.get("/profile/profile/young_one/").json()["data"]
        self.assertEqual(d.get("location"), "")
        self.assertEqual(d.get("links"), [])
        self.assertTrue(d["joined_at"])

    def test_minor_sees_own_location_and_links(self):
        self.client.force_authenticate(self.minor)
        d = self.client.get(ME).json()["data"]
        self.assertEqual(d["location"], "Kota")
        self.assertEqual(len(d["links"]), 1)

    def test_adult_location_and_links_stay_visible(self):
        self.client.force_authenticate(self.viewer)
        d = self.client.get("/profile/profile/grown_up/").json()["data"]
        self.assertEqual(d["location"], "Delhi")
        self.assertEqual(len(d["links"]), 1)

    def test_joined_at_is_a_date(self):
        self.client.force_authenticate(self.viewer)
        d = self.client.get("/profile/profile/grown_up/").json()["data"]
        self.assertEqual(d["joined_at"], self.adult.date_joined.date().isoformat())


class HideRuleUnitTests(SimpleTestCase):
    def test_hide_rule(self):
        class U:
            def __init__(self, pk, minor):
                self.pk, self.is_minor = pk, minor

        self.assertTrue(ph.hide_private_header_fields(U(1, True), U(2, False)))
        self.assertFalse(ph.hide_private_header_fields(U(1, True), U(1, True)))
        self.assertFalse(ph.hide_private_header_fields(U(1, False), U(2, False)))
        self.assertTrue(ph.hide_private_header_fields(U(1, True), None))


class ProfileExportHeaderFieldsTests(APITestCase):
    """P1 — the DPDP data export carries the new header fields. NOT RUN."""

    def test_profile_section_has_location_cover_and_username_rule_fields(self):
        from django.contrib.auth import get_user_model
        from login import data_rights

        user = get_user_model().objects.create_user(username="export_p1", password="x-Pass-12345")
        user.location = "Kota, Rajasthan"
        user.verified_reason = ""
        user.save()
        data = data_rights._section_profile(user)
        self.assertEqual(data["location"], "Kota, Rajasthan")
        self.assertIn("cover_photo", data)
        self.assertIn("verified_reason", data)
        self.assertIn("username_changed_at", data)
