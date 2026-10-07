"""
STORIES UPGRADE - PART 3b (Highlights) tests.

Covers: the archive (what may go into a highlight), create / edit / delete /
add / remove, every validation rule and limit, who can see which highlight
(private accounts, blocks, Close Friends items), cover selection, item order,
music + stickers coming through on highlight stories, and that the purge task
keeps highlighted stories while the sweep still hides them from the live tray.
"""
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile.models import BlockUser, Follow

from .models import CloseFriend, Highlight, HighlightItem, Story, StorySticker
from .tasks import expire_old_stories, hard_delete_ancient_stories

User = get_user_model()

MUSIC = {
    "id": "1", "title": "Calm piano", "artist": "x", "url": "https://cdn.freesound.org/p/1.mp3",
    "duration": 30.0, "start": 0.0, "license": "CC0",
}


def make_user(username, **extra):
    return User.objects.create_user(username=username, password="testpass123", **extra)


def follow(follower, following):
    return Follow.objects.create(follower=follower, following=following, status=Follow.Status.ACCEPTED)


def make_story(user, name=None, *, media_type="image", audience=Story.AUDIENCE_EVERYONE, state="live", **extra):
    """state: live | expired (over 24 h, not swept) | swept (over 24 h, sweep flagged it)
    | removed (owner removed it BEFORE it expired - gone for good)."""
    name = name or f"{uuid.uuid4().hex[:8]}.jpg"
    now = timezone.now()
    story = Story.objects.create(
        user=user, media=f"stories/2026/01/01/{name}", media_type=media_type, audience=audience, **extra,
    )
    if state == "live":
        return story
    fields = {}
    if state in ("expired", "swept"):
        fields["expires_at"] = now - timedelta(hours=2)
    if state == "swept":
        fields.update(is_deleted=True, deleted_at=now - timedelta(hours=1))
    if state == "removed":
        fields.update(is_deleted=True, deleted_at=now, expires_at=now + timedelta(hours=10))
    Story.objects.filter(pk=story.pk).update(**fields)
    story.refresh_from_db()
    return story


def make_highlight(user, stories, title="Trip", cover=None):
    h = Highlight.objects.create(user=user, title=title)
    items = [HighlightItem.objects.create(highlight=h, story=s, position=i) for i, s in enumerate(stories)]
    if cover is not None:
        h.cover_item = next(i for i in items if i.story_id == cover.pk)
        h.save()
    return h


def rows_of(res):
    return res.data["results"] if isinstance(res.data, dict) and "results" in res.data else res.data


def ids(rows):
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
class ArchiveTests(APITestCase):
    def setUp(self):
        self.me = make_user("hl_me")
        self.other = make_user("hl_other")
        self.client.force_authenticate(self.me)
        self.url = reverse("story-archive")

    def test_archive_has_live_expired_and_swept_but_not_removed_or_foreign(self):
        live = make_story(self.me)
        expired = make_story(self.me, state="expired")
        swept = make_story(self.me, state="swept")
        make_story(self.me, state="removed")
        make_story(self.other)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertCountEqual(ids(rows_of(res)), [str(live.id), str(expired.id), str(swept.id)])

    def test_archive_is_newest_first_and_paginated(self):
        stories = [make_story(self.me) for _ in range(3)]
        res = self.client.get(self.url, {"page_size": 2})
        self.assertEqual(len(res.data["results"]), 2)
        self.assertIsNotNone(res.data["next"])
        self.assertEqual(res.data["results"][0]["id"], str(stories[-1].id))

    def test_archive_rows_carry_music_and_stickers(self):
        story = make_story(self.me, music=MUSIC)
        StorySticker.objects.create(story=story, kind="link", data={"url": "https://a.example", "label": "", "host": "a.example"})
        row = rows_of(self.client.get(self.url))[0]
        self.assertEqual(row["music"]["title"], "Calm piano")
        self.assertEqual(len(row["stickers"]), 1)

    def test_archive_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))


# ---------------------------------------------------------------------------
class HighlightCreateTests(APITestCase):
    def setUp(self):
        self.me = make_user("hc_me")
        self.other = make_user("hc_other")
        self.client.force_authenticate(self.me)
        self.url = reverse("highlight-list")
        self.s1 = make_story(self.me, "a.jpg")
        self.s2 = make_story(self.me, "b.jpg", state="expired")
        self.s3 = make_story(self.me, "c.jpg", state="swept")

    def _create(self, **body):
        return self.client.post(self.url, body, format="json")

    def test_create_returns_detail_with_stories_in_order(self):
        res = self._create(title="Goa 2026", story_ids=[str(self.s3.id), str(self.s1.id), str(self.s2.id)])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["title"], "Goa 2026")
        self.assertTrue(res.data["is_owner"])
        self.assertEqual(res.data["items_count"], 3)
        self.assertEqual([s["id"] for s in res.data["stories"]], [str(self.s3.id), str(self.s1.id), str(self.s2.id)])
        self.assertEqual(res.data["user"]["username"], "hc_me")
        h = Highlight.objects.get(pk=res.data["id"])
        self.assertEqual(list(h.items.values_list("story_id", "position")),
                         [(self.s3.id, 0), (self.s1.id, 1), (self.s2.id, 2)])

    def test_title_defaults_and_is_cleaned(self):
        self.assertEqual(self._create(story_ids=[str(self.s1.id)]).data["title"], "Highlights")
        self.assertEqual(self._create(title="   ", story_ids=[str(self.s1.id)]).data["title"], "Highlights")
        self.assertEqual(self._create(title="  My \n  trip\x07 ", story_ids=[str(self.s1.id)]).data["title"], "My trip")

    def test_title_length_limit(self):
        self.assertEqual(self._create(title="x" * 30, story_ids=[str(self.s1.id)]).status_code, 201)
        res = self._create(title="x" * 31, story_ids=[str(self.s1.id)])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "title_too_long")

    def test_needs_at_least_one_story(self):
        self.assertEqual(self._create(title="x").status_code, 400)
        self.assertEqual(self._create(title="x", story_ids=[]).status_code, 400)
        self.assertEqual(Highlight.objects.count(), 0)

    def test_bad_story_id_shapes(self):
        self.assertEqual(self._create(story_ids=["not-a-uuid"]).status_code, 400)
        self.assertEqual(self._create(story_ids="nope").status_code, 400)

    def test_foreign_unknown_and_removed_stories_are_rejected_and_nothing_is_saved(self):
        theirs = make_story(self.other)
        removed = make_story(self.me, state="removed")
        for bad in (theirs.id, uuid.uuid4(), removed.id):
            with self.subTest(bad=bad):
                res = self._create(story_ids=[str(self.s1.id), str(bad)])
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.data["code"], "story_not_found")
        self.assertEqual(Highlight.objects.count(), 0)
        self.assertEqual(HighlightItem.objects.count(), 0)

    def test_duplicate_ids_are_collapsed(self):
        res = self._create(story_ids=[str(self.s1.id), str(self.s1.id), str(self.s2.id)])
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["items_count"], 2)

    def test_cover_can_be_chosen(self):
        res = self._create(story_ids=[str(self.s1.id), str(self.s2.id)], cover_story_id=str(self.s2.id))
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["cover_story_id"], str(self.s2.id))
        self.assertTrue(res.data["cover_url"].endswith("b.jpg"))

    def test_cover_must_be_a_photo_in_the_highlight(self):
        video = make_story(self.me, "v.mp4", media_type="video")
        res = self._create(story_ids=[str(self.s1.id), str(video.id)], cover_story_id=str(video.id))
        self.assertEqual((res.status_code, res.data["code"]), (400, "cover_not_photo"))
        res = self._create(story_ids=[str(self.s1.id)], cover_story_id=str(self.s2.id))
        self.assertEqual((res.status_code, res.data["code"]), (400, "cover_invalid"))
        self.assertEqual(Highlight.objects.count(), 0)  # both rolled back

    @override_settings(STORY_MAX_HIGHLIGHTS=2)
    def test_highlight_count_limit(self):
        for _ in range(2):
            self.assertEqual(self._create(story_ids=[str(self.s1.id)]).status_code, 201)
        res = self._create(story_ids=[str(self.s1.id)])
        self.assertEqual((res.status_code, res.data["code"]), (400, "highlight_limit"))
        # another user is not affected
        self.client.force_authenticate(self.other)
        theirs = make_story(self.other)
        self.assertEqual(self._create(story_ids=[str(theirs.id)]).status_code, 201)

    @override_settings(STORY_MAX_HIGHLIGHT_ITEMS=2)
    def test_items_per_highlight_limit(self):
        res = self._create(story_ids=[str(self.s1.id), str(self.s2.id), str(self.s3.id)])
        self.assertEqual((res.status_code, res.data["code"]), (400, "too_many_stories"))

    def test_same_story_can_be_in_two_highlights(self):
        self.assertEqual(self._create(story_ids=[str(self.s1.id)]).status_code, 201)
        self.assertEqual(self._create(title="Again", story_ids=[str(self.s1.id)]).status_code, 201)
        self.assertEqual(HighlightItem.objects.filter(story=self.s1).count(), 2)

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self._create(story_ids=[str(self.s1.id)]).status_code, (401, 403))


# ---------------------------------------------------------------------------
class HighlightListTests(APITestCase):
    def setUp(self):
        self.owner = make_user("hl_owner")
        self.fan = make_user("hl_fan")
        self.stranger = make_user("hl_stranger")
        follow(self.fan, self.owner)
        self.url = reverse("highlight-list")

    def _list(self, viewer, user_id=None):
        self.client.force_authenticate(viewer)
        return self.client.get(self.url, {"user_id": user_id} if user_id else {})

    def test_default_is_my_own_list_newest_first(self):
        a = make_highlight(self.owner, [make_story(self.owner)], title="A")
        b = make_highlight(self.owner, [make_story(self.owner)], title="B")
        Highlight.objects.filter(pk=a.pk).update(updated_at=timezone.now() - timedelta(days=1))
        res = self._list(self.owner)
        self.assertEqual(res.data["count"], 2)
        self.assertEqual([r["title"] for r in res.data["results"]], ["B", "A"])
        self.assertEqual(set(res.data["results"][0]), {
            "id", "title", "cover_url", "cover_story_id", "cover_x", "cover_y", "cover_zoom",
            "items_count", "created_at", "updated_at", "user",
        })

    def test_public_profile_is_visible_to_followers_and_strangers(self):
        make_highlight(self.owner, [make_story(self.owner)])
        for viewer in (self.fan, self.stranger):
            with self.subTest(viewer=viewer.username):
                self.assertEqual(self._list(viewer, self.owner.id).data["count"], 1)

    def test_private_profile_only_for_accepted_followers(self):
        User.objects.filter(pk=self.owner.pk).update(is_private=True)
        make_highlight(self.owner, [make_story(self.owner)])
        self.assertEqual(self._list(self.fan, self.owner.id).data["count"], 1)
        self.assertEqual(self._list(self.stranger, self.owner.id).data["count"], 0)
        Follow.objects.create(follower=self.stranger, following=self.owner, status=Follow.Status.PENDING)
        self.assertEqual(self._list(self.stranger, self.owner.id).data["count"], 0)

    def test_blocks_hide_the_list_both_ways(self):
        make_highlight(self.owner, [make_story(self.owner)])
        block = BlockUser.objects.create(blocker=self.owner, blocked=self.fan)
        self.assertEqual(self._list(self.fan, self.owner.id).data["count"], 0)
        block.delete()
        BlockUser.objects.create(blocker=self.fan, blocked=self.owner)
        self.assertEqual(self._list(self.fan, self.owner.id).data["count"], 0)

    def test_inactive_owner_shows_nothing(self):
        make_highlight(self.owner, [make_story(self.owner)])
        User.objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assertEqual(self._list(self.fan, self.owner.id).data["count"], 0)

    def test_unknown_and_malformed_user_id(self):
        self.assertEqual(self._list(self.fan, 999999).status_code, 404)
        self.client.force_authenticate(self.fan)
        self.assertEqual(self.client.get(self.url, {"user_id": "abc"}).status_code, 400)

    def test_cover_prefers_chosen_photo_then_first_photo_then_none(self):
        video = make_story(self.owner, "v.mp4", media_type="video")
        p1 = make_story(self.owner, "p1.jpg")
        p2 = make_story(self.owner, "p2.jpg")
        make_highlight(self.owner, [video, p1, p2], title="chosen", cover=p2)
        make_highlight(self.owner, [video, p1, p2], title="first-photo")
        make_highlight(self.owner, [make_story(self.owner, "only.mp4", media_type="video")], title="videos")
        by_title = {r["title"]: r for r in self._list(self.owner).data["results"]}
        self.assertTrue(by_title["chosen"]["cover_url"].endswith("p2.jpg"))
        self.assertTrue(by_title["first-photo"]["cover_url"].endswith("p1.jpg"))
        self.assertIsNone(by_title["videos"]["cover_url"])
        self.assertIsNone(by_title["videos"]["cover_story_id"])

    def test_close_friends_items_are_counted_and_hidden_per_viewer(self):
        everyone = make_story(self.owner, "e.jpg")
        secret = make_story(self.owner, "s.jpg", audience=Story.AUDIENCE_CLOSE_FRIENDS)
        make_highlight(self.owner, [secret, everyone], title="mixed")
        make_highlight(self.owner, [make_story(self.owner, audience=Story.AUDIENCE_CLOSE_FRIENDS)], title="secret-only")

        fan_rows = self._list(self.fan, self.owner.id).data["results"]
        self.assertEqual([(r["title"], r["items_count"]) for r in fan_rows], [("mixed", 1)])  # secret-only is gone

        owner_rows = {r["title"]: r for r in self._list(self.owner).data["results"]}
        self.assertEqual(owner_rows["mixed"]["items_count"], 2)
        self.assertIn("secret-only", owner_rows)  # the owner still sees an "empty for others" one

        CloseFriend.objects.create(owner=self.owner, friend=self.fan)
        fan_rows = {r["title"]: r for r in self._list(self.fan, self.owner.id).data["results"]}
        self.assertEqual((fan_rows["mixed"]["items_count"], fan_rows["secret-only"]["items_count"]), (2, 1))

    def test_cover_never_leaks_a_hidden_close_friends_photo(self):
        secret = make_story(self.owner, "secret.jpg", audience=Story.AUDIENCE_CLOSE_FRIENDS)
        shown = make_story(self.owner, "shown.jpg")
        make_highlight(self.owner, [secret, shown], cover=secret)
        row = self._list(self.fan, self.owner.id).data["results"][0]
        self.assertTrue(row["cover_url"].endswith("shown.jpg"))

    def test_query_count_does_not_grow_with_the_number_of_highlights(self):
        make_highlight(self.owner, [make_story(self.owner)], title="one")
        self._list(self.fan, self.owner.id)  # warm-up
        with CaptureQueriesContext(connection) as one:
            self._list(self.fan, self.owner.id)
        for i in range(4):
            make_highlight(self.owner, [make_story(self.owner), make_story(self.owner)], title=f"h{i}")
        with CaptureQueriesContext(connection) as many:
            res = self._list(self.fan, self.owner.id)
        self.assertEqual(res.data["count"], 5)
        self.assertEqual(len(many), len(one))


# ---------------------------------------------------------------------------
class HighlightDetailTests(APITestCase):
    def setUp(self):
        self.owner = make_user("hd_owner")
        self.fan = make_user("hd_fan")
        self.stranger = make_user("hd_stranger")
        follow(self.fan, self.owner)

    def _get(self, viewer, highlight_id):
        self.client.force_authenticate(viewer)
        return self.client.get(reverse("highlight-detail", args=[highlight_id]))

    def test_expired_stories_are_shown_in_order_with_music_and_stickers(self):
        first = make_story(self.owner, "1.jpg", state="swept", music=MUSIC)
        second = make_story(self.owner, "2.jpg", state="expired")
        StorySticker.objects.create(story=first, kind="link", data={"url": "https://a.example", "label": "L", "host": "a.example"})
        h = make_highlight(self.owner, [second, first])
        res = self._get(self.fan, h.id)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(res.data["is_owner"])
        self.assertEqual([s["id"] for s in res.data["stories"]], [str(second.id), str(first.id)])
        by_id = {s["id"]: s for s in res.data["stories"]}
        self.assertEqual(by_id[str(first.id)]["music"]["title"], "Calm piano")
        self.assertEqual(by_id[str(first.id)]["stickers"][0]["kind"], "link")
        self.assertIsNone(by_id[str(second.id)]["music"])

    def test_removed_before_expiry_story_is_not_shown(self):
        keep = make_story(self.owner)
        gone = make_story(self.owner)
        h = make_highlight(self.owner, [keep, gone])
        Story.objects.filter(pk=gone.pk).update(
            is_deleted=True, deleted_at=timezone.now(), expires_at=timezone.now() + timedelta(hours=5),
        )
        res = self._get(self.owner, h.id)
        self.assertEqual([s["id"] for s in res.data["stories"]], [str(keep.id)])

    def test_public_profile_detail_for_a_stranger(self):
        h = make_highlight(self.owner, [make_story(self.owner)])
        self.assertEqual(self._get(self.stranger, h.id).status_code, 200)

    def test_private_profile_detail_is_a_404_for_non_followers(self):
        h = make_highlight(self.owner, [make_story(self.owner)])
        User.objects.filter(pk=self.owner.pk).update(is_private=True)
        self.assertEqual(self._get(self.stranger, h.id).status_code, 404)
        self.assertEqual(self._get(self.fan, h.id).status_code, 200)

    def test_blocked_viewer_gets_404(self):
        h = make_highlight(self.owner, [make_story(self.owner)])
        BlockUser.objects.create(blocker=self.owner, blocked=self.fan)
        self.assertEqual(self._get(self.fan, h.id).status_code, 404)

    def test_close_friends_items_are_filtered_and_all_hidden_means_404(self):
        shown = make_story(self.owner, "shown.jpg")
        secret = make_story(self.owner, "secret.jpg", audience=Story.AUDIENCE_CLOSE_FRIENDS)
        mixed = make_highlight(self.owner, [secret, shown], title="mixed")
        secret_only = make_highlight(self.owner, [secret], title="secret only")

        res = self._get(self.fan, mixed.id)
        self.assertEqual([s["id"] for s in res.data["stories"]], [str(shown.id)])
        self.assertEqual(res.data["items_count"], 1)
        self.assertEqual(self._get(self.fan, secret_only.id).status_code, 404)
        self.assertEqual(self._get(self.owner, secret_only.id).status_code, 200)  # owner still can

        CloseFriend.objects.create(owner=self.owner, friend=self.fan)
        self.assertEqual(self._get(self.fan, secret_only.id).status_code, 200)
        self.assertEqual(len(self._get(self.fan, mixed.id).data["stories"]), 2)

    def test_unknown_highlight_and_login_required(self):
        self.assertEqual(self._get(self.fan, uuid.uuid4()).status_code, 404)
        h = make_highlight(self.owner, [make_story(self.owner)])
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(reverse("highlight-detail", args=[h.id])).status_code, (401, 403))

    def test_detail_query_count_does_not_grow_with_the_number_of_stories(self):
        h = make_highlight(self.owner, [make_story(self.owner)])
        self._get(self.fan, h.id)  # warm-up
        with CaptureQueriesContext(connection) as one:
            self._get(self.fan, h.id)
        for _ in range(5):
            HighlightItem.objects.create(highlight=h, story=make_story(self.owner, state="expired"), position=9)
        with CaptureQueriesContext(connection) as many:
            res = self._get(self.fan, h.id)
        self.assertEqual(len(res.data["stories"]), 6)
        self.assertEqual(len(many), len(one))


# ---------------------------------------------------------------------------
class HighlightEditTests(APITestCase):
    def setUp(self):
        self.me = make_user("he_me")
        self.fan = make_user("he_fan")
        follow(self.fan, self.me)
        self.client.force_authenticate(self.me)
        self.a = make_story(self.me, "a.jpg")
        self.b = make_story(self.me, "b.jpg", state="expired")
        self.c = make_story(self.me, "c.jpg", state="swept")
        self.h = make_highlight(self.me, [self.a, self.b], title="Old")
        self.url = reverse("highlight-detail", args=[self.h.id])

    def _patch(self, **body):
        return self.client.patch(self.url, body, format="json")

    def test_rename(self):
        res = self._patch(title="  New  name ")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["title"], "New name")
        self.h.refresh_from_db()
        self.assertEqual(self.h.title, "New name")

    def test_blank_or_long_title_is_rejected(self):
        self.assertEqual(self._patch(title="   ").status_code, 400)
        self.assertEqual(self._patch(title="x" * 31).status_code, 400)
        self.h.refresh_from_db()
        self.assertEqual(self.h.title, "Old")

    def test_reorder_add_and_remove_in_one_call(self):
        res = self._patch(story_ids=[str(self.c.id), str(self.b.id)])
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual([s["id"] for s in res.data["stories"]], [str(self.c.id), str(self.b.id)])
        self.assertFalse(HighlightItem.objects.filter(highlight=self.h, story=self.a).exists())
        self.assertTrue(Story.objects.filter(pk=self.a.pk).exists())  # the story itself is untouched

    def test_empty_story_list_is_rejected_use_delete_instead(self):
        self.assertEqual(self._patch(story_ids=[]).status_code, 400)
        self.assertEqual(self.h.items.count(), 2)

    def test_added_stories_must_be_mine(self):
        theirs = make_story(self.fan)
        res = self._patch(story_ids=[str(self.a.id), str(theirs.id)])
        self.assertEqual((res.status_code, res.data["code"]), (400, "story_not_found"))
        self.assertEqual(self.h.items.count(), 2)

    def test_existing_items_stay_even_when_outside_the_archive_check(self):
        # Re-sending ids already in the highlight never re-runs the archive check on them.
        Story.objects.filter(pk=self.b.pk).update(is_deleted=True, deleted_at=timezone.now(),
                                                   expires_at=timezone.now() + timedelta(hours=1))
        res = self._patch(story_ids=[str(self.b.id), str(self.a.id)])
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(list(self.h.items.order_by("position").values_list("story_id", flat=True)), [self.b.id, self.a.id])

    @override_settings(STORY_MAX_HIGHLIGHT_ITEMS=2)
    def test_item_limit_applies_on_edit(self):
        res = self._patch(story_ids=[str(self.a.id), str(self.b.id), str(self.c.id)])
        self.assertEqual((res.status_code, res.data["code"]), (400, "too_many_stories"))

    def test_set_and_clear_cover(self):
        res = self._patch(cover_story_id=str(self.b.id))
        self.assertEqual(res.data["cover_story_id"], str(self.b.id))
        res = self._patch(cover_story_id=None)
        self.assertEqual(res.data["cover_story_id"], str(self.a.id))  # automatic = first photo
        self.h.refresh_from_db()
        self.assertIsNone(self.h.cover_item_id)

    def test_cover_must_be_in_the_highlight_and_a_photo(self):
        self.assertEqual(self._patch(cover_story_id=str(self.c.id)).data["code"], "cover_invalid")
        video = make_story(self.me, "v.mp4", media_type="video")
        HighlightItem.objects.create(highlight=self.h, story=video, position=5)
        self.assertEqual(self._patch(cover_story_id=str(video.id)).data["code"], "cover_not_photo")

    def test_removing_the_cover_story_falls_back_to_automatic(self):
        self.assertEqual(self._patch(cover_story_id=str(self.b.id)).status_code, 200)
        res = self._patch(story_ids=[str(self.a.id), str(self.c.id)])
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["cover_story_id"], str(self.a.id))
        self.h.refresh_from_db()
        self.assertIsNone(self.h.cover_item_id)

    def test_cover_crop_is_saved_and_returned(self):
        res = self._patch(cover_story_id=str(self.b.id), cover_x=-0.5, cover_y=0.25, cover_zoom=2)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data["cover_x"], res.data["cover_y"], res.data["cover_zoom"]), (-0.5, 0.25, 2.0))
        self.client.force_authenticate(self.me)
        row = self.client.get(reverse("highlight-list")).data["results"][0]
        self.assertEqual((row["cover_x"], row["cover_y"], row["cover_zoom"]), (-0.5, 0.25, 2.0))

    def test_crop_needs_an_explicit_cover(self):
        res = self._patch(cover_zoom=2)
        self.assertEqual((res.status_code, res.data["code"]), (400, "crop_needs_cover"))

    def test_crop_values_are_range_checked(self):
        self.assertEqual(self._patch(cover_story_id=str(self.b.id), cover_zoom=9).status_code, 400)
        self.assertEqual(self._patch(cover_story_id=str(self.b.id), cover_x=1.5).status_code, 400)
        res = self._patch(cover_story_id=str(self.b.id), cover_y="nan")
        self.assertEqual(res.status_code, 400)

    def test_changing_or_clearing_the_cover_resets_the_crop(self):
        self._patch(cover_story_id=str(self.b.id), cover_zoom=2.5, cover_x=0.5)
        res = self._patch(cover_story_id=str(self.a.id))
        self.assertEqual((res.data["cover_x"], res.data["cover_zoom"]), (0.0, 1.0))
        self._patch(cover_story_id=str(self.b.id), cover_zoom=2.0)
        res = self._patch(cover_story_id=None)
        self.assertEqual(res.data["cover_zoom"], 1.0)

    def test_removing_the_cropped_cover_story_resets_the_crop(self):
        self._patch(cover_story_id=str(self.b.id), cover_zoom=2.0)
        res = self._patch(story_ids=[str(self.a.id), str(self.c.id)])
        self.assertEqual(res.data["cover_zoom"], 1.0)
        self.h.refresh_from_db()
        self.assertEqual(self.h.cover_zoom, 1.0)

    def test_edit_bumps_updated_at(self):
        before = self.h.updated_at
        self._patch(title="Later")
        self.h.refresh_from_db()
        self.assertGreater(self.h.updated_at, before)

    def test_only_the_owner_can_edit_or_delete(self):
        self.client.force_authenticate(self.fan)
        self.assertEqual(self._patch(title="Hacked").status_code, 404)
        self.assertEqual(self.client.delete(self.url).status_code, 404)
        self.h.refresh_from_db()
        self.assertEqual(self.h.title, "Old")

    def test_delete_removes_the_highlight_but_not_the_stories(self):
        res = self.client.delete(self.url)
        self.assertEqual(res.status_code, 204)
        self.assertFalse(Highlight.objects.filter(pk=self.h.pk).exists())
        self.assertEqual(HighlightItem.objects.count(), 0)
        self.assertEqual(Story.objects.filter(pk__in=[self.a.pk, self.b.pk]).count(), 2)
        self.assertEqual(self.client.delete(self.url).status_code, 404)


# ---------------------------------------------------------------------------
class HighlightAddRemoveStoryTests(APITestCase):
    def setUp(self):
        self.me = make_user("ha_me")
        self.other = make_user("ha_other")
        self.client.force_authenticate(self.me)
        self.a = make_story(self.me, "a.jpg")
        self.b = make_story(self.me, "b.jpg")
        self.h = make_highlight(self.me, [self.a], title="Set")
        self.add_url = reverse("highlight-add-story", args=[self.h.id])

    def _remove(self, story, highlight=None):
        return self.client.delete(reverse("highlight-remove-story", args=[(highlight or self.h).id, story.id]))

    def test_add_appends_at_the_end(self):
        res = self.client.post(self.add_url, {"story_id": str(self.b.id)}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data["added"], res.data["items_count"]), (True, 2))
        self.assertEqual(list(self.h.items.order_by("position").values_list("story_id", flat=True)), [self.a.id, self.b.id])

    def test_adding_twice_is_a_200_and_changes_nothing(self):
        res = self.client.post(self.add_url, {"story_id": str(self.a.id)}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.data["added"], res.data["items_count"]), (False, 1))

    def test_add_expired_story_is_fine(self):
        old = make_story(self.me, state="swept")
        self.assertEqual(self.client.post(self.add_url, {"story_id": str(old.id)}, format="json").status_code, 201)

    def test_add_rejects_foreign_removed_and_unknown_stories(self):
        for story_id in (make_story(self.other).id, make_story(self.me, state="removed").id, uuid.uuid4()):
            with self.subTest(story_id=story_id):
                res = self.client.post(self.add_url, {"story_id": str(story_id)}, format="json")
                self.assertEqual(res.status_code, 404)
        self.assertEqual(self.h.items.count(), 1)

    def test_add_needs_a_valid_story_id(self):
        for body in ({}, {"story_id": "nope"}, {"story_id": None}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post(self.add_url, body, format="json").status_code, 400)

    @override_settings(STORY_MAX_HIGHLIGHT_ITEMS=1)
    def test_add_respects_the_item_limit(self):
        res = self.client.post(self.add_url, {"story_id": str(self.b.id)}, format="json")
        self.assertEqual((res.status_code, res.data["code"]), (400, "too_many_stories"))

    def test_add_to_someone_elses_highlight_is_a_404(self):
        self.client.force_authenticate(self.other)
        mine = make_story(self.other)
        self.assertEqual(self.client.post(self.add_url, {"story_id": str(mine.id)}, format="json").status_code, 404)

    def test_remove_one_keeps_the_highlight(self):
        HighlightItem.objects.create(highlight=self.h, story=self.b, position=1)
        res = self._remove(self.a)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {"highlight_deleted": False, "items_count": 1})
        self.assertTrue(Highlight.objects.filter(pk=self.h.pk).exists())

    def test_removing_the_last_story_deletes_the_highlight(self):
        res = self._remove(self.a)
        self.assertEqual(res.data, {"highlight_deleted": True, "items_count": 0})
        self.assertFalse(Highlight.objects.filter(pk=self.h.pk).exists())
        self.assertTrue(Story.objects.filter(pk=self.a.pk).exists())

    def test_remove_a_story_that_is_not_in_it_is_a_harmless_200(self):
        res = self._remove(self.b)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {"highlight_deleted": False, "items_count": 1})

    def test_only_the_owner_can_remove(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self._remove(self.a).status_code, 404)
        self.assertEqual(self.h.items.count(), 1)


# ---------------------------------------------------------------------------
class HighlightLifecycleTests(APITestCase):
    """The purge / sweep tasks and the DB constraints."""

    def setUp(self):
        self.me = make_user("hx_me")
        self.fan = make_user("hx_fan")
        follow(self.fan, self.me)

    def test_purge_keeps_highlighted_stories_and_deletes_the_rest(self):
        old = timezone.now() - timedelta(days=45)
        kept = make_story(self.me, "kept.jpg")
        lost = make_story(self.me, "lost.jpg")
        Story.objects.filter(pk__in=[kept.pk, lost.pk]).update(
            is_deleted=True, deleted_at=old, expires_at=old - timedelta(hours=1),
        )
        make_highlight(self.me, [kept])
        purged = hard_delete_ancient_stories()
        self.assertEqual(purged, 1)
        self.assertTrue(Story.objects.filter(pk=kept.pk).exists())
        self.assertFalse(Story.objects.filter(pk=lost.pk).exists())

    def test_purge_lets_go_once_the_story_leaves_its_last_highlight(self):
        old = timezone.now() - timedelta(days=45)
        story = make_story(self.me)
        Story.objects.filter(pk=story.pk).update(is_deleted=True, deleted_at=old, expires_at=old - timedelta(hours=1))
        h = make_highlight(self.me, [story])
        self.assertEqual(hard_delete_ancient_stories(), 0)
        h.delete()
        self.assertEqual(hard_delete_ancient_stories(), 1)

    def test_sweep_hides_from_the_tray_but_the_highlight_still_shows_it(self):
        story = make_story(self.me, "s.jpg", music=MUSIC)
        h = make_highlight(self.me, [story])
        Story.objects.filter(pk=story.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(expire_old_stories(), 1)
        story.refresh_from_db()
        self.assertTrue(story.is_deleted)

        self.client.force_authenticate(self.fan)
        tray = rows_of(self.client.get(reverse("story-list")))
        self.assertEqual(tray, [])  # gone from the live tray
        res = self.client.get(reverse("highlight-detail", args=[h.id]))
        self.assertEqual([s["id"] for s in res.data["stories"]], [str(story.id)])  # still in the highlight
        self.assertEqual(res.data["stories"][0]["music"]["title"], "Calm piano")

    def test_interactions_on_a_highlight_story_are_404(self):
        story = make_story(self.me, state="swept")
        make_highlight(self.me, [story])
        self.client.force_authenticate(self.fan)
        self.assertEqual(self.client.post(reverse("story-view", args=[story.id])).status_code, 404)
        self.assertEqual(self.client.post(reverse("story-react", args=[story.id]), {"emoji": "x"}).status_code, 404)

    def test_a_story_cannot_be_in_the_same_highlight_twice(self):
        from django.db import IntegrityError, transaction

        story = make_story(self.me)
        h = make_highlight(self.me, [story])
        with self.assertRaises(IntegrityError), transaction.atomic():
            HighlightItem.objects.create(highlight=h, story=story, position=3)

    def test_deleting_a_user_removes_their_highlights(self):
        make_highlight(self.me, [make_story(self.me)])
        self.me.delete()
        self.assertEqual(Highlight.objects.count(), 0)
        self.assertEqual(HighlightItem.objects.count(), 0)
