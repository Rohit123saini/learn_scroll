"""
STORIES UPGRADE - PART 2a (Stickers foundation + Story mentions + Link sticker) tests.

Covers: creating a story with mention/link stickers (multipart JSON field),
every validation rule (self/blocked/duplicate/limit/close-friends mentions, URL
safety, placement ranges, payload shape), the notification a mention sends, the
serialised shape on the list / create / detail endpoints (incl. no N+1), the
detail endpoint's visibility rules, mention-candidate search, the block
clean-up signal and the DB constraints.
"""
import io
import json
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.utils import DatabaseError
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from core.models import Notification
from user_profile.models import BlockUser, Follow

from .models import CloseFriend, Story, StorySticker
from .story_stickers import StickerError, normalize_story_link

User = get_user_model()

_TMP_MEDIA = tempfile.mkdtemp(prefix="ls_story_sticker_tests_")


def make_user(username, **extra):
    return User.objects.create_user(username=username, password="testpass123", **extra)


def follow(follower, following):
    return Follow.objects.create(follower=follower, following=following, status=Follow.Status.ACCEPTED)


def image_upload(name="s.jpg"):
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (30, 30, 200)).save(buf, format="JPEG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


def make_story(user, audience=Story.AUDIENCE_EVERYONE):
    return Story.objects.create(
        user=user, media="stories/2026/01/01/test.jpg", media_type="image", audience=audience,
    )


def mention_sticker(story, user, **extra):
    return StorySticker.objects.create(
        story=story, kind=StorySticker.KIND_MENTION, mentioned_user=user, **extra,
    )


def rows_of(res):
    return res.data["results"] if isinstance(res.data, dict) and "results" in res.data else res.data


# ---------------------------------------------------------------------------
# Pure URL rules - no database needed.
# ---------------------------------------------------------------------------
class NormalizeStoryLinkTests(SimpleTestCase):
    def test_https_url_is_kept_and_host_lowercased(self):
        self.assertEqual(
            normalize_story_link("HTTPS://Example.COM/Path?a=1#frag"),
            "https://example.com/Path?a=1#frag",
        )

    def test_bare_domain_gets_https(self):
        self.assertEqual(normalize_story_link("learnscroll.com/course/9"), "https://learnscroll.com/course/9")

    def test_scheme_relative_gets_https(self):
        self.assertEqual(normalize_story_link("//learnscroll.com/x"), "https://learnscroll.com/x")

    def test_http_is_allowed(self):
        self.assertEqual(normalize_story_link("http://example.org"), "http://example.org")

    def test_port_is_kept(self):
        self.assertEqual(normalize_story_link("https://example.com:8443/a"), "https://example.com:8443/a")

    def test_surrounding_whitespace_is_trimmed(self):
        self.assertEqual(normalize_story_link("  https://example.com  "), "https://example.com")

    def test_dangerous_or_unsupported_schemes_are_rejected(self):
        for bad in (
            "javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html;base64,AAAA",
            "file:///etc/passwd", "ftp://example.com/x", "mailto:a@b.com", "intent://x#Intent;end",
            "localhost:8000/x", "tel:+911234567890",
        ):
            with self.subTest(url=bad):
                with self.assertRaises(StickerError):
                    normalize_story_link(bad)

    def test_credentials_in_url_are_rejected(self):
        with self.assertRaises(StickerError):
            normalize_story_link("https://google.com@evil.example/login")
        with self.assertRaises(StickerError):
            normalize_story_link("https://user:pass@example.com")

    def test_ip_and_single_label_hosts_are_rejected(self):
        for bad in ("http://127.0.0.1/x", "https://192.168.1.5", "http://[::1]/x", "https://localhost/x", "https://intranet"):
            with self.subTest(url=bad):
                with self.assertRaises(StickerError):
                    normalize_story_link(bad)

    def test_garbage_is_rejected(self):
        for bad in ("", "   ", None, 123, "https://", "https://exa mple.com", "https://a..b.com", "https://-bad.com",
                    "https://example.com:99999", "https://exa\x00mple.com", "https://1.2.3"):
            with self.subTest(url=bad):
                with self.assertRaises(StickerError):
                    normalize_story_link(bad)

    def test_too_long_is_rejected(self):
        with self.assertRaises(StickerError):
            normalize_story_link("https://example.com/" + "a" * 2100)

    @override_settings(STORY_LINK_BLOCKED_DOMAINS=("evil.example",))
    def test_blocked_domain_and_its_subdomains_are_rejected(self):
        for bad in ("https://evil.example/x", "https://www.evil.example", "https://A.B.EVIL.example"):
            with self.subTest(url=bad):
                with self.assertRaises(StickerError) as ctx:
                    normalize_story_link(bad)
                self.assertEqual(ctx.exception.code, "link_blocked")
        # a different domain that merely ends with the same letters is fine
        self.assertEqual(normalize_story_link("https://notevil.example"), "https://notevil.example")


# ---------------------------------------------------------------------------
# Creating a story with stickers.
# ---------------------------------------------------------------------------
@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class StoryStickerCreateTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.owner = make_user("st_owner")
        self.bob = make_user("st_bob")
        self.cara = make_user("st_cara")
        self.client.force_authenticate(self.owner)

    def _create(self, stickers=None, raw=None, **data):
        payload = {"media": image_upload(), "media_type": "image", **data}
        if raw is not None:
            payload["stickers"] = raw
        elif stickers is not None:
            payload["stickers"] = json.dumps(stickers)
        return self.client.post(reverse("story-create"), payload, format="multipart")

    def _assert_rejected(self, res, story_count_before=0):
        self.assertEqual(res.status_code, 400, getattr(res, "data", None))
        self.assertIn("stickers", res.data["errors"])
        self.assertEqual(Story.objects.count(), story_count_before)
        self.assertEqual(StorySticker.objects.count(), 0)

    # ---- happy paths ----
    def test_story_without_stickers_still_works(self):
        res = self._create()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["stickers"], [])

    def test_empty_stickers_field_is_fine(self):
        res = self._create(raw="")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["stickers"], [])

    def test_create_with_mention_and_link(self):
        res = self._create([
            {"kind": "mention", "x": 0.25, "y": 0.4, "rotation": -8, "scale": 1.5, "user_id": self.bob.id},
            {"kind": "link", "x": 0.5, "y": 0.85, "url": "Learnscroll.com/course/9", "label": "  Join the course "},
        ])
        self.assertEqual(res.status_code, 201, res.data)
        stickers = res.data["stickers"]
        self.assertEqual([s["kind"] for s in stickers], ["mention", "link"])

        mention, link = stickers
        self.assertEqual((mention["x"], mention["y"], mention["rotation"], mention["scale"]), (0.25, 0.4, -8.0, 1.5))
        self.assertEqual(mention["data"]["user"]["username"], "st_bob")
        self.assertEqual(mention["data"]["user"]["id"], str(self.bob.id))
        self.assertEqual(link["data"], {
            "url": "https://learnscroll.com/course/9", "label": "Join the course", "host": "learnscroll.com",
        })
        self.assertEqual(StorySticker.objects.filter(story_id=res.data["id"]).count(), 2)

    def test_placement_defaults_and_stacking_order(self):
        res = self._create([
            {"kind": "link", "url": "https://a.example.com"},
            {"kind": "mention", "user_id": self.bob.id},
        ])
        self.assertEqual(res.status_code, 201, res.data)
        first, second = res.data["stickers"]
        self.assertEqual((first["x"], first["y"], first["rotation"], first["scale"]), (0.5, 0.5, 0.0, 1.0))
        self.assertLess(first["z_index"], second["z_index"])  # later in the list = on top

    def test_link_label_is_optional(self):
        res = self._create([{"kind": "link", "url": "https://example.com"}])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["stickers"][0]["data"]["label"], "")

    def test_response_shape_matches_list_and_detail(self):
        res = self._create([{"kind": "mention", "user_id": self.bob.id}])
        self.assertEqual(res.status_code, 201, res.data)
        detail = self.client.get(reverse("story-detail", args=[res.data["id"]]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["stickers"], res.data["stickers"])

    # ---- notification ----
    def test_mentioned_person_gets_a_story_mention_notification(self):
        res = self._create([{"kind": "mention", "user_id": self.bob.id}], caption="hello")
        self.assertEqual(res.status_code, 201, res.data)
        notif = Notification.objects.get(recipient=self.bob, notif_type="story_mention")
        self.assertEqual(notif.data["story_id"], str(res.data["id"]))
        self.assertEqual(str(notif.data["actor_id"]), str(self.owner.id))
        self.assertIn("mentioned you", notif.title)
        self.assertEqual(notif.message, "hello")

    def test_only_mentioned_people_are_notified_and_once_each(self):
        self._create([{"kind": "mention", "user_id": self.bob.id}])
        self.assertEqual(Notification.objects.filter(notif_type="story_mention").count(), 1)
        self.assertFalse(Notification.objects.filter(recipient=self.cara).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.owner).exists())

    def test_link_only_story_sends_no_notification(self):
        self._create([{"kind": "link", "url": "https://example.com"}])
        self.assertFalse(Notification.objects.filter(notif_type="story_mention").exists())

    def test_rejected_story_sends_no_notification(self):
        self._create([
            {"kind": "mention", "user_id": self.bob.id},
            {"kind": "mention", "user_id": 999999},
        ])
        self.assertFalse(Notification.objects.filter(notif_type="story_mention").exists())

    # ---- mention rules ----
    def test_cannot_mention_yourself(self):
        self._assert_rejected(self._create([{"kind": "mention", "user_id": self.owner.id}]))

    def test_cannot_mention_unknown_or_inactive_user(self):
        self._assert_rejected(self._create([{"kind": "mention", "user_id": 999999}]))
        self.cara.is_active = False
        self.cara.save(update_fields=["is_active"])
        self._assert_rejected(self._create([{"kind": "mention", "user_id": self.cara.id}]))

    def test_cannot_mention_a_blocked_person_either_direction(self):
        BlockUser.objects.create(blocker=self.owner, blocked=self.bob)
        BlockUser.objects.create(blocker=self.cara, blocked=self.owner)
        self._assert_rejected(self._create([{"kind": "mention", "user_id": self.bob.id}]))
        self._assert_rejected(self._create([{"kind": "mention", "user_id": self.cara.id}]))

    def test_blocked_and_unknown_get_the_same_message(self):
        BlockUser.objects.create(blocker=self.owner, blocked=self.bob)
        blocked = self._create([{"kind": "mention", "user_id": self.bob.id}])
        unknown = self._create([{"kind": "mention", "user_id": 999999}])
        self.assertEqual(blocked.data["errors"]["stickers"], unknown.data["errors"]["stickers"])

    def test_cannot_mention_the_same_person_twice(self):
        self._assert_rejected(self._create([
            {"kind": "mention", "user_id": self.bob.id},
            {"kind": "mention", "user_id": self.bob.id},
        ]))

    def test_user_id_must_be_a_positive_integer(self):
        for bad in ("12", 1.5, None, True, -3, 0):
            with self.subTest(user_id=bad):
                self._assert_rejected(self._create([{"kind": "mention", "user_id": bad}]))

    @override_settings(STORY_MAX_MENTIONS=2)
    def test_mention_limit(self):
        dan = make_user("st_dan")
        self._assert_rejected(self._create([
            {"kind": "mention", "user_id": self.bob.id},
            {"kind": "mention", "user_id": self.cara.id},
            {"kind": "mention", "user_id": dan.id},
        ]))
        ok = self._create([
            {"kind": "mention", "user_id": self.bob.id},
            {"kind": "mention", "user_id": self.cara.id},
        ])
        self.assertEqual(ok.status_code, 201, ok.data)

    def test_close_friends_story_can_only_mention_close_friends(self):
        CloseFriend.objects.create(owner=self.owner, friend=self.bob)
        rejected = self._create(
            [{"kind": "mention", "user_id": self.cara.id}], audience="close_friends",
        )
        self._assert_rejected(rejected)
        ok = self._create([{"kind": "mention", "user_id": self.bob.id}], audience="close_friends")
        self.assertEqual(ok.status_code, 201, ok.data)
        self.assertEqual(ok.data["audience"], "close_friends")

    def test_everyone_story_can_mention_a_non_follower(self):
        # Neither bob nor cara follow the owner and the owner follows nobody.
        res = self._create([{"kind": "mention", "user_id": self.cara.id}])
        self.assertEqual(res.status_code, 201, res.data)

    # ---- link rules ----
    def test_unsafe_links_are_rejected_on_the_api_too(self):
        for bad in ("javascript:alert(1)", "https://google.com@evil.example", "http://10.0.0.1/x", "ftp://example.com", ""):
            with self.subTest(url=bad):
                self._assert_rejected(self._create([{"kind": "link", "url": bad}]))

    def test_link_url_is_required(self):
        self._assert_rejected(self._create([{"kind": "link"}]))
        self._assert_rejected(self._create([{"kind": "link", "label": "no url"}]))

    def test_label_rules(self):
        self._assert_rejected(self._create([{"kind": "link", "url": "https://example.com", "label": "x" * 41}]))
        self._assert_rejected(self._create([{"kind": "link", "url": "https://example.com", "label": 5}]))
        ok = self._create([{"kind": "link", "url": "https://example.com", "label": "x" * 40}])
        self.assertEqual(ok.status_code, 201, ok.data)

    def test_only_one_link_sticker(self):
        self._assert_rejected(self._create([
            {"kind": "link", "url": "https://a.example.com"},
            {"kind": "link", "url": "https://b.example.com"},
        ]))

    @override_settings(STORY_LINK_BLOCKED_DOMAINS=("evil.example",))
    def test_blocked_domain_setting_applies_to_the_api(self):
        self._assert_rejected(self._create([{"kind": "link", "url": "https://evil.example/x"}]))

    # ---- payload shape / placement ----
    def test_payload_must_be_a_json_list_of_objects(self):
        for raw in ("not json", '{"kind": "link"}', "[1, 2]", '["link"]', "{"):
            with self.subTest(raw=raw):
                self._assert_rejected(self._create(raw=raw))

    def test_unknown_kinds_are_rejected(self):
        # mention / link / poll / question are the supported kinds (poll and
        # question are covered in tests_story_poll_question.py).
        for kind in ("gif", "music", "", None):
            with self.subTest(kind=kind):
                self._assert_rejected(self._create([{"kind": kind}]))

    def test_too_many_stickers(self):
        self._assert_rejected(self._create([{"kind": "link", "url": "https://example.com"}] * 11))

    def test_payload_size_limit(self):
        self._assert_rejected(self._create(raw="[" + " " * 9000 + "]"))

    def test_placement_ranges(self):
        base = {"kind": "link", "url": "https://example.com"}
        for extra in (
            {"x": -0.01}, {"x": 1.01}, {"y": 2}, {"y": "0.5"}, {"rotation": 181}, {"rotation": -181},
            {"scale": 0.39}, {"scale": 4.01}, {"scale": 0}, {"x": True}, {"z_index": -1}, {"z_index": 1.5},
            {"z_index": 101},
        ):
            with self.subTest(extra=extra):
                self._assert_rejected(self._create([{**base, **extra}]))

    def test_non_finite_numbers_are_rejected(self):
        # Python's json.loads accepts NaN / Infinity - the validator must not.
        self._assert_rejected(self._create(raw='[{"kind":"link","url":"https://example.com","x":NaN}]'))
        self._assert_rejected(self._create(raw='[{"kind":"link","url":"https://example.com","scale":Infinity}]'))

    def test_boundary_placement_values_are_accepted(self):
        res = self._create([{
            "kind": "link", "url": "https://example.com", "x": 0, "y": 1, "rotation": 180, "scale": 4, "z_index": 100,
        }])
        self.assertEqual(res.status_code, 201, res.data)

    def test_error_names_the_offending_sticker(self):
        res = self._create([
            {"kind": "link", "url": "https://example.com"},
            {"kind": "mention", "user_id": 999999},
        ])
        self.assertIn("Sticker 2", res.data["errors"]["stickers"][0])

    def test_nothing_is_saved_when_any_sticker_is_invalid(self):
        res = self._create([
            {"kind": "mention", "user_id": self.bob.id},
            {"kind": "link", "url": "javascript:alert(1)"},
        ])
        self._assert_rejected(res)


# ---------------------------------------------------------------------------
# Reading stickers back: list / detail.
# ---------------------------------------------------------------------------
@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class StoryStickerReadTests(APITestCase):
    def setUp(self):
        self.owner = make_user("rd_owner")
        self.fan = make_user("rd_fan")
        self.tagged = make_user("rd_tagged")
        self.stranger = make_user("rd_stranger")
        follow(self.fan, self.owner)

    def test_list_includes_stickers(self):
        story = make_story(self.owner)
        mention_sticker(story, self.tagged, x=0.2, y=0.3)
        StorySticker.objects.create(
            story=story, kind="link", z_index=1, data={"url": "https://example.com", "label": "", "host": "example.com"},
        )
        self.client.force_authenticate(self.fan)
        rows = rows_of(self.client.get(reverse("story-list")))
        row = next(r for r in rows if str(r["id"]) == str(story.id))
        self.assertEqual([s["kind"] for s in row["stickers"]], ["mention", "link"])
        self.assertEqual(row["stickers"][0]["data"]["user"]["username"], "rd_tagged")

    def test_story_without_stickers_has_an_empty_list(self):
        story = make_story(self.owner)
        self.client.force_authenticate(self.fan)
        rows = rows_of(self.client.get(reverse("story-list")))
        self.assertEqual(next(r for r in rows if str(r["id"]) == str(story.id))["stickers"], [])

    def test_list_query_count_does_not_grow_with_stickers(self):
        # Same single story both times, so only the sticker count differs.
        users = [make_user(f"rd_u{i}") for i in range(5)]
        story = make_story(self.owner)
        mention_sticker(story, users[0])
        self.client.force_authenticate(self.fan)
        self.client.get(reverse("story-list"))  # warm-up (one-off lazy caches)

        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse("story-list"))

        for u in users[1:]:
            mention_sticker(story, u)

        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse("story-list"))
        self.assertEqual(len(few), len(many))

    def test_detail_for_a_follower(self):
        story = make_story(self.owner)
        mention_sticker(story, self.tagged)
        self.client.force_authenticate(self.fan)
        res = self.client.get(reverse("story-detail", args=[story.id]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["id"], str(story.id))
        self.assertEqual(res.data["stickers"][0]["data"]["user"]["username"], "rd_tagged")

    def test_a_mentioned_non_follower_can_open_the_story(self):
        story = make_story(self.owner)
        mention_sticker(story, self.tagged)
        self.client.force_authenticate(self.tagged)  # does not follow the owner
        self.assertEqual(self.client.get(reverse("story-detail", args=[story.id])).status_code, 200)

    def test_detail_404_for_expired_deleted_and_unknown(self):
        expired = make_story(self.owner)
        Story.objects.filter(id=expired.id).update(expires_at=timezone.now() - timedelta(minutes=1))
        deleted = make_story(self.owner)
        deleted.soft_delete()
        self.client.force_authenticate(self.fan)
        self.assertEqual(self.client.get(reverse("story-detail", args=[expired.id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("story-detail", args=[deleted.id])).status_code, 404)
        import uuid
        self.assertEqual(self.client.get(reverse("story-detail", args=[uuid.uuid4()])).status_code, 404)

    def test_detail_respects_close_friends_audience(self):
        story = make_story(self.owner, audience=Story.AUDIENCE_CLOSE_FRIENDS)
        CloseFriend.objects.create(owner=self.owner, friend=self.tagged)
        self.client.force_authenticate(self.fan)
        self.assertEqual(self.client.get(reverse("story-detail", args=[story.id])).status_code, 404)
        self.client.force_authenticate(self.tagged)
        self.assertEqual(self.client.get(reverse("story-detail", args=[story.id])).status_code, 200)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(reverse("story-detail", args=[story.id])).status_code, 200)

    def test_detail_404_when_blocked(self):
        story = make_story(self.owner)
        BlockUser.objects.create(blocker=self.owner, blocked=self.stranger)
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get(reverse("story-detail", args=[story.id])).status_code, 404)

    def test_detail_requires_login(self):
        story = make_story(self.owner)
        self.assertIn(self.client.get(reverse("story-detail", args=[story.id])).status_code, (401, 403))


# ---------------------------------------------------------------------------
# @mention picker.
# ---------------------------------------------------------------------------
class StoryMentionCandidatesTests(APITestCase):
    def setUp(self):
        self.me = make_user("mc_me")
        self.following = make_user("mc_following", first_name="Riya", last_name="Sharma")
        self.follower = make_user("mc_follower")
        self.other = make_user("mc_other_riya")
        self.blocked = make_user("mc_blocked_riya")
        self.blocked_me = make_user("mc_blockedme_riya")
        self.inactive = make_user("mc_inactive_riya")
        self.inactive.is_active = False
        self.inactive.save(update_fields=["is_active"])
        follow(self.me, self.following)
        follow(self.follower, self.me)
        BlockUser.objects.create(blocker=self.me, blocked=self.blocked)
        BlockUser.objects.create(blocker=self.blocked_me, blocked=self.me)
        self.client.force_authenticate(self.me)
        self.url = reverse("story-mention-candidates")

    def _names(self, **params):
        res = self.client.get(self.url, params)
        self.assertEqual(res.status_code, 200, res.data)
        return [r["username"] for r in rows_of(res)]

    def test_without_query_only_my_people(self):
        self.assertEqual(set(self._names()), {"mc_following", "mc_follower"})

    def test_query_searches_everyone_but_never_self_blocked_or_inactive(self):
        names = self._names(q="riya")
        self.assertIn("mc_other_riya", names)
        self.assertIn("mc_following", names)  # matched on first name
        for hidden in ("mc_blocked_riya", "mc_blockedme_riya", "mc_inactive_riya", "mc_me"):
            self.assertNotIn(hidden, names)

    def test_connected_people_sort_first(self):
        names = self._names(q="riya")
        self.assertLess(names.index("mc_following"), names.index("mc_other_riya"))

    def test_query_is_case_insensitive_and_matches_username_or_name(self):
        self.assertIn("mc_following", self._names(q="SHARMA"))
        self.assertIn("mc_other_riya", self._names(q="OTHER_RIYA"))

    def test_close_friends_audience_narrows_to_the_list(self):
        CloseFriend.objects.create(owner=self.me, friend=self.following)
        self.assertEqual(self._names(audience="close_friends"), ["mc_following"])
        self.assertEqual(self._names(audience="close_friends", q="riya"), ["mc_following"])

    def test_row_shape(self):
        row = rows_of(self.client.get(self.url))[0]
        self.assertEqual(set(row), {"id", "username", "name", "profile_picture"})

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))


# ---------------------------------------------------------------------------
# Block clean-up + DB constraints.
# ---------------------------------------------------------------------------
class StoryStickerBlockAndConstraintTests(APITestCase):
    def setUp(self):
        self.a = make_user("bc_a")
        self.b = make_user("bc_b")
        self.c = make_user("bc_c")

    def test_block_removes_mention_tags_in_both_directions(self):
        a_story, b_story = make_story(self.a), make_story(self.b)
        a_tags_b = mention_sticker(a_story, self.b)
        b_tags_a = mention_sticker(b_story, self.a)
        a_tags_c = mention_sticker(a_story, self.c)
        StorySticker.objects.create(story=a_story, kind="link", data={"url": "https://example.com", "label": "", "host": "example.com"})

        BlockUser.objects.create(blocker=self.a, blocked=self.b)

        self.assertFalse(StorySticker.objects.filter(id__in=[a_tags_b.id, b_tags_a.id]).exists())
        self.assertTrue(StorySticker.objects.filter(id=a_tags_c.id).exists())  # unrelated tag stays
        self.assertTrue(StorySticker.objects.filter(story=a_story, kind="link").exists())
        self.assertTrue(Story.objects.filter(id=a_story.id).exists())  # the story itself stays

    def test_block_between_two_other_people_touches_nothing(self):
        story = make_story(self.a)
        tag = mention_sticker(story, self.b)
        BlockUser.objects.create(blocker=self.b, blocked=self.c)
        self.assertTrue(StorySticker.objects.filter(id=tag.id).exists())

    def test_same_person_cannot_be_tagged_twice_on_a_story(self):
        story = make_story(self.a)
        mention_sticker(story, self.b)
        with self.assertRaises(IntegrityError), transaction.atomic():
            mention_sticker(story, self.b)

    def test_same_person_can_be_tagged_on_different_stories(self):
        mention_sticker(make_story(self.a), self.b)
        mention_sticker(make_story(self.a), self.b)
        self.assertEqual(StorySticker.objects.filter(mentioned_user=self.b).count(), 2)

    def test_a_mention_must_name_someone(self):
        story = make_story(self.a)
        with self.assertRaises(DatabaseError), transaction.atomic():
            StorySticker.objects.create(story=story, kind="mention")

    def test_several_links_are_allowed_at_the_db_level(self):
        # The one-link rule is an API rule (settings.STORY_MAX_LINKS), not a DB constraint.
        story = make_story(self.a)
        for i in range(2):
            StorySticker.objects.create(story=story, kind="link", data={"url": f"https://e{i}.example.com"})
        self.assertEqual(story.stickers.count(), 2)

    def test_deleting_the_story_deletes_its_stickers(self):
        story = make_story(self.a)
        mention_sticker(story, self.b)
        story.delete()
        self.assertEqual(StorySticker.objects.count(), 0)
