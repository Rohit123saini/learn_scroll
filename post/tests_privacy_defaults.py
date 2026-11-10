"""
S4b - account-level defaults for NEW posts / stories (PrivacySettings.default_*).

NOT RUN when written (read-only session). Run: python manage.py test post.tests_privacy_defaults

Covers: the pure fill helpers (explicit value wins, "left out" vs "sent false"), POST /post/stories/create/ and
POST /post/create/ picking up the author's defaults, a composer value beating the default, a multipart form that
leaves a switch out (must NOT read as "off"), existing posts / stories never rewritten, the minor floor on the DEFAULT,
repost / share-as-post starting from the default audience, and the reshare / save switches being enforced.
"""
import shutil
from datetime import date, timedelta

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from user_profile import privacy
from user_profile.models import PrivacySettings

from . import content_defaults
from .models import Post, Story
from .tests_story_stickers import _TMP_MEDIA, follow, image_upload, make_story, make_user


def _years_ago(n):
    today = timezone.localdate()
    try:
        return today.replace(year=today.year - n)
    except ValueError:  # 29 Feb
        return (today - timedelta(days=1)).replace(year=today.year - n)


def _set(user, **fields):
    row = PrivacySettings.for_user(user)
    for key, value in fields.items():
        setattr(row, key, value)
    row.save()  # save() drops the privacy cache
    return row


# ======================================================================================================
# pure helpers (no DB)
# ======================================================================================================
class FillHelperTests(SimpleTestCase):
    DEFAULTS = {"story_audience": "close_friends", "post_visibility": "connections", "comments_limit": "followers",
                "allow_reshare": False, "allow_save": False}

    def _patch(self):
        from unittest import mock

        return mock.patch.object(content_defaults, "_defaults", return_value=dict(self.DEFAULTS))

    def test_story_fill_fills_only_what_was_left_out(self):
        with self._patch():
            self.assertEqual(
                content_defaults.story_fill(None, {}),
                {"audience": "close_friends", "allow_reshare": False, "allow_save": False},
            )
            # sent keys are never touched, even when the value equals the "off" default
            self.assertEqual(content_defaults.story_fill(None, {"audience": "everyone", "allow_save": "true"}),
                             {"allow_reshare": False})
            self.assertEqual(content_defaults.story_fill(None, {"audience": "everyone", "allow_reshare": "true",
                                                                 "allow_save": "true"}), {})

    def test_post_fill_fills_visibility_comments_and_switches(self):
        with self._patch():
            self.assertEqual(
                content_defaults.post_fill(None, {}),
                {"visibility": "connections", "comments_limit": "followers", "allow_reshare": False,
                 "allow_save": False},
            )
            self.assertEqual(
                content_defaults.post_fill(None, {"visibility": "public", "comments_limit": "everyone"}),
                {"allow_reshare": False, "allow_save": False},
            )

    def test_post_extra_fill_has_no_visibility(self):
        with self._patch():
            self.assertNotIn("visibility", content_defaults.post_extra_fill(None, {}))

    def test_post_visibility_default(self):
        with self._patch():
            self.assertEqual(content_defaults.post_visibility_default(None, ""), "connections")
            self.assertEqual(content_defaults.post_visibility_default(None, None), "connections")
            self.assertEqual(content_defaults.post_visibility_default(None, "public"), "public")

    def test_unreadable_defaults_change_nothing_and_never_raise(self):
        from unittest import mock

        with mock.patch("user_profile.privacy.content_defaults_for", side_effect=RuntimeError("cache down")):
            self.assertEqual(content_defaults.story_fill(None, {}), {})
            self.assertEqual(content_defaults.post_fill(None, {}), {})
            self.assertEqual(content_defaults.post_visibility_default(None, ""), "public")

    def test_non_mapping_raw_data_counts_as_nothing_sent(self):
        with self._patch():
            self.assertIn("audience", content_defaults.story_fill(None, None))
            self.assertIn("audience", content_defaults.story_fill(None, 5))


# ======================================================================================================
# content_defaults_for + minor floor (DB)
# ======================================================================================================
class ContentDefaultsForTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_no_row_gives_the_open_defaults(self):
        user = make_user("cd_none")
        self.assertEqual(
            privacy.content_defaults_for(user),
            {"story_audience": "everyone", "post_visibility": "public", "comments_limit": "everyone",
             "allow_reshare": True, "allow_save": True},
        )
        self.assertFalse(PrivacySettings.objects.filter(user=user).exists())

    def test_saved_choice_is_returned(self):
        user = make_user("cd_saved")
        _set(user, default_story_audience="followers", default_post_visibility="close_friends",
             default_comments_limit="followers", default_allow_reshare=False, default_allow_save=False)
        self.assertEqual(
            privacy.content_defaults_for(user),
            {"story_audience": "followers", "post_visibility": "close_friends", "comments_limit": "followers",
             "allow_reshare": False, "allow_save": False},
        )

    def test_minor_default_is_raised_to_the_safe_floor_but_stored_value_is_kept(self):
        minor = make_user("cd_minor", date_of_birth=_years_ago(15))
        _set(minor, default_story_audience="everyone", default_post_visibility="public",
             default_comments_limit="everyone")
        got = privacy.content_defaults_for(minor)
        self.assertEqual((got["story_audience"], got["post_visibility"], got["comments_limit"]),
                         ("followers", "connections", "followers"))
        row = PrivacySettings.objects.get(user=minor)
        self.assertEqual(row.default_story_audience, "everyone")  # applies again at 18

    def test_minor_may_choose_something_safer_than_the_floor(self):
        minor = make_user("cd_minor2", date_of_birth=_years_ago(15))
        _set(minor, default_story_audience="close_friends", default_post_visibility="close_friends")
        got = privacy.content_defaults_for(minor)
        self.assertEqual((got["story_audience"], got["post_visibility"]), ("close_friends", "close_friends"))

    def test_adult_is_never_floored(self):
        adult = make_user("cd_adult", date_of_birth=_years_ago(30))
        _set(adult, default_story_audience="everyone", default_post_visibility="public")
        got = privacy.content_defaults_for(adult)
        self.assertEqual((got["story_audience"], got["post_visibility"]), ("everyone", "public"))

    @override_settings(PRIVACY_MINOR_LOCKS=False)
    def test_floor_switch_off_leaves_the_choice_alone(self):
        minor = make_user("cd_minor3", date_of_birth=_years_ago(15))
        _set(minor, default_story_audience="everyone")
        self.assertEqual(privacy.content_defaults_for(minor)["story_audience"], "everyone")

    def test_minor_cannot_patch_the_open_value_but_can_patch_a_safe_one(self):
        minor = make_user("cd_minor4", date_of_birth=_years_ago(15))
        self.client.force_authenticate(minor)
        url = reverse("privacy-settings")
        r = self.client.patch(url, {"default_story_audience": "everyone"}, format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data["code"], privacy.MINOR_LOCKED_CODE)
        self.assertIn("default_story_audience", r.data["errors"])
        r = self.client.patch(url, {"default_story_audience": "close_friends"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["data"]["default_story_audience"], "close_friends")
        # the privacy screen is told these defaults are locked
        self.assertIn("default_story_audience", r.data["data"]["locked_fields"])


# ======================================================================================================
# story create
# ======================================================================================================
@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class StoryDefaultsTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.author = make_user("sd_author")
        self.client.force_authenticate(self.author)

    def _create(self, **data):
        payload = {"media": image_upload(), "media_type": "image", **data}
        return self.client.post(reverse("story-create"), payload, format="multipart")

    def test_defaults_apply_when_the_composer_sends_nothing(self):
        _set(self.author, default_story_audience="close_friends", default_allow_reshare=False,
             default_allow_save=False)
        res = self._create()
        self.assertEqual(res.status_code, 201, res.data)
        story = Story.objects.get(id=res.data["id"])
        self.assertEqual(story.audience, Story.AUDIENCE_CLOSE_FRIENDS)
        self.assertFalse(story.allow_reshare)
        self.assertFalse(story.allow_save)

    def test_no_row_keeps_todays_behaviour(self):
        res = self._create()
        self.assertEqual(res.status_code, 201, res.data)
        story = Story.objects.get(id=res.data["id"])
        self.assertEqual(story.audience, Story.AUDIENCE_EVERYONE)
        self.assertTrue(story.allow_reshare)
        self.assertTrue(story.allow_save)

    def test_explicit_composer_value_beats_the_default(self):
        _set(self.author, default_story_audience="close_friends", default_allow_reshare=False)
        res = self._create(audience="everyone", allow_reshare="true")
        self.assertEqual(res.status_code, 201, res.data)
        story = Story.objects.get(id=res.data["id"])
        self.assertEqual(story.audience, Story.AUDIENCE_EVERYONE)
        self.assertTrue(story.allow_reshare)

    def test_explicit_false_is_kept_when_the_default_is_true(self):
        res = self._create(allow_save="false")
        self.assertEqual(res.status_code, 201, res.data)
        story = Story.objects.get(id=res.data["id"])
        self.assertFalse(story.allow_save)
        self.assertTrue(story.allow_reshare)  # left out -> default (True)

    def test_changing_the_default_never_rewrites_an_existing_story(self):
        story = make_story(self.author)
        _set(self.author, default_story_audience="close_friends", default_allow_reshare=False)
        story.refresh_from_db()
        self.assertEqual(story.audience, Story.AUDIENCE_EVERYONE)
        self.assertTrue(story.allow_reshare)

    def test_minor_default_story_is_not_everyone(self):
        minor = make_user("sd_minor", date_of_birth=_years_ago(15))
        _set(minor, default_story_audience="everyone")  # stored; the floor lifts the effective value
        self.client.force_authenticate(minor)
        res = self._create()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Story.objects.get(id=res.data["id"]).audience, Story.AUDIENCE_FOLLOWERS)

    def test_new_story_is_visible_only_to_the_default_audience(self):
        from .story_visibility import can_view_story

        fan = make_user("sd_fan")
        stranger = make_user("sd_stranger")
        follow(fan, self.author)
        _set(self.author, default_story_audience="followers")
        res = self._create()
        story = Story.objects.get(id=res.data["id"])
        self.assertTrue(can_view_story(story, fan))
        self.assertFalse(can_view_story(story, stranger))


# ======================================================================================================
# post create
# ======================================================================================================
class PostDefaultsTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.author = make_user("pd_author")
        self.client.force_authenticate(self.author)
        self.url = reverse("post-create")

    def _create(self, **extra):
        body = {"content": "hello defaults", "category": "tech", "post_type": "text", **extra}
        res = self.client.post(self.url, body, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        return Post.objects.get(id=res.data["data"]["id"])

    def test_defaults_apply_when_the_composer_sends_nothing(self):
        _set(self.author, default_post_visibility="connections", default_comments_limit="followers",
             default_allow_reshare=False, default_allow_save=False)
        post = self._create()
        self.assertEqual(post.visibility, "connections")
        self.assertEqual(post.comments_limit, "followers")
        self.assertFalse(post.allow_reshare)
        self.assertFalse(post.allow_save)

    def test_no_row_keeps_todays_behaviour(self):
        post = self._create()
        self.assertEqual((post.visibility, post.comments_limit), ("public", "everyone"))
        self.assertTrue(post.allow_reshare and post.allow_save)

    def test_explicit_values_beat_the_defaults(self):
        _set(self.author, default_post_visibility="close_friends", default_comments_limit="followers",
             default_allow_reshare=False)
        post = self._create(visibility="public", comments_limit="everyone", allow_reshare="true")
        self.assertEqual((post.visibility, post.comments_limit), ("public", "everyone"))
        self.assertTrue(post.allow_reshare)

    def test_multipart_form_that_leaves_a_switch_out_is_not_read_as_off(self):
        # DRF reads a missing BooleanField on a multipart form as False; the default (True) must win instead.
        post = self._create()
        self.assertTrue(post.allow_reshare)
        self.assertTrue(post.allow_save)

    def test_existing_posts_are_not_rewritten(self):
        post = self._create()
        _set(self.author, default_post_visibility="close_friends", default_allow_save=False)
        post.refresh_from_db()
        self.assertEqual(post.visibility, "public")
        self.assertTrue(post.allow_save)

    def test_minor_default_post_is_not_public(self):
        minor = make_user("pd_minor", date_of_birth=_years_ago(15))
        _set(minor, default_post_visibility="public", default_comments_limit="everyone")
        self.client.force_authenticate(minor)
        post = self._create()
        self.assertEqual((post.visibility, post.comments_limit), ("connections", "followers"))

    def test_repost_starts_from_the_default_audience(self):
        original = Post.objects.create(user=make_user("pd_orig"), content="orig", post_type="text",
                                       visibility="public", category="tech")
        _set(self.author, default_post_visibility="connections")
        res = self.client.post(reverse("post-repost", args=[original.id]), {"repost_caption": "nice"},
                               format="json")
        self.assertIn(res.status_code, (200, 201), res.data)
        repost = Post.objects.filter(user=self.author, post_type="repost").first()
        self.assertIsNotNone(repost)
        self.assertEqual(repost.visibility, "connections")


# ======================================================================================================
# the switches are enforced, not only stored
# ======================================================================================================
class ReshareSaveEnforcementTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.author = make_user("en_author")
        self.viewer = make_user("en_viewer")
        self.client.force_authenticate(self.viewer)

    def _post(self, **extra):
        return Post.objects.create(user=self.author, content="c", post_type="text", visibility="public",
                                   category="tech", **extra)

    def test_reshare_off_refuses_a_repost_with_the_code(self):
        post = self._post(allow_reshare=False)
        res = self.client.post(reverse("post-repost", args=[post.id]), {"repost_caption": "x"}, format="json")
        self.assertEqual(res.status_code, 403, res.data)
        self.assertEqual(res.data.get("code"), "post_reshare_off")
        self.assertFalse(Post.objects.filter(user=self.viewer, post_type="repost").exists())

    def test_reshare_on_allows_a_repost(self):
        post = self._post(allow_reshare=True)
        res = self.client.post(reverse("post-repost", args=[post.id]), {"repost_caption": "x"}, format="json")
        self.assertIn(res.status_code, (200, 201), res.data)

    def test_the_author_can_always_repost_their_own_post(self):
        post = self._post(allow_reshare=False)
        self.client.force_authenticate(self.author)
        res = self.client.post(reverse("post-repost", args=[post.id]), {"repost_caption": "x"}, format="json")
        self.assertIn(res.status_code, (200, 201), res.data)
