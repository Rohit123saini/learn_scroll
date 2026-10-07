# post/tests_reels.py
# Reels feed: GET /post/reels/ (post/reels.py + post/reels_views.py).
#   * payload: lean ReelSerializer (video / caption / hashtags / author / counts /
#     viewer state / feed_source), constant query count, per-viewer state
#   * candidates: video only, approved, public, not sensitive / deleted / own,
#     blocked / muted / hidden out, known duration <= cap, vertical-friendly
#   * ranking: Home pieces + small following bonus - show-fewer penalty
#   * seen videos excluded (top-up when the pool is tiny)
#   * cursor + frozen snapshot (namespace "reels"), ?start=<post_id>
import datetime
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile.models import BlockUser, Follow

from . import feed_mix, feed_snapshot, reels
from .models import (
    FeedFeedback, MutedAccount, Post, PostHide, PostLike, PostMedia, PostSave, PostView, UserInterest,
)
from .serializers import ReelSerializer

User = get_user_model()
OLD = timezone.now() - datetime.timedelta(days=10)  # outside every velocity tier


class ConfigTests(SimpleTestCase):
    def test_defaults(self):
        cfg = reels.get_config()
        self.assertEqual(cfg["min_aspect"], 1.2)
        self.assertEqual(cfg["max_duration_seconds"], 180)
        self.assertEqual(cfg["following_bonus"], 10.0)
        self.assertEqual(cfg["page_size"], 10)
        self.assertTrue(cfg["enabled"])

    def test_project_settings_define_feed_reels_with_the_five_documented_keys(self):
        from django.conf import settings

        for key in ("enabled", "min_aspect", "max_duration_seconds", "following_bonus", "page_size"):
            self.assertIn(key, settings.FEED_REELS)
        self.assertFalse(hasattr(settings, "REELS"))  # old name is gone

    @override_settings(FEED_REELS={"following_bonus": 25, "page_size": 3})
    def test_feed_reels_overrides_are_merged_over_defaults(self):
        cfg = reels.get_config()
        self.assertEqual((cfg["following_bonus"], cfg["page_size"]), (25.0, 3))
        self.assertEqual(cfg["max_duration_seconds"], 180)  # untouched keys keep the default

    @override_settings(FEED_REELS={"min_aspect": "abc", "pool_cap": 0, "page_size": 999, "max_page_size": 20})
    def test_garbage_falls_back_and_is_clamped(self):
        cfg = reels.get_config()
        self.assertEqual(cfg["min_aspect"], 1.2)
        self.assertEqual(cfg["pool_cap"], 1)
        self.assertEqual(cfg["page_size"], 20)

    def test_with_start_first(self):
        a, b, c = "a", "b", "c"
        self.assertEqual(reels.with_start_first([a, b, c], b), [b, a, c])
        self.assertEqual(reels.with_start_first([a, b], c), [c, a, b])
        self.assertEqual(reels.with_start_first([a, b], None), [a, b])

    def test_parse_start(self):
        self.assertIsNone(reels.parse_start(None))
        self.assertIsNone(reels.parse_start("nope"))
        self.assertIsNotNone(reels.parse_start("6f1a4c1e-8b7e-4a11-9c55-0d5f0e2c9a11"))

    def test_list_cursor_is_namespaced(self):
        import base64
        import json
        from rest_framework.exceptions import NotFound

        sid = feed_snapshot.new_snapshot_id()
        token = feed_snapshot.encode_list_cursor("reels", sid, 12, "2026-09-30T00:00:00.000000Z")
        state = feed_snapshot.decode_list_cursor(token, "reels")
        self.assertEqual((state["snapshot_id"], state["offset"], state["seen_cutoff"]),
                         (sid, 12, "2026-09-30T00:00:00.000000Z"))
        with self.assertRaises(NotFound):
            feed_snapshot.decode_list_cursor(token, "other")
        with self.assertRaises(NotFound):  # a Home cursor is not a Reels cursor
            feed_snapshot.decode_list_cursor(feed_snapshot.encode_cursor(sid, {}, None), "reels")
        with self.assertRaises(NotFound):  # ... and the other way round
            feed_snapshot.decode_cursor(token)

        def enc(obj):
            return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()

        for bad in ("!!!", enc([]), enc({"v": 1, "n": "reels", "s": "x", "o": 1, "c": None}),
                    enc({"v": 1, "n": "reels", "s": sid, "o": -1, "c": None}),
                    enc({"v": 1, "n": "reels", "s": sid, "o": True, "c": None}),
                    enc({"v": 1, "n": "reels", "s": sid, "o": "3", "c": None}),
                    enc({"v": 1, "n": "reels", "s": sid, "o": 1, "c": 5})):
            with self.assertRaises(NotFound, msg=bad):
                feed_snapshot.decode_list_cursor(bad, "reels")

    def test_snapshot_keys_do_not_collide(self):
        self.assertNotEqual(feed_snapshot.cache_key("u", "s"), feed_snapshot.cache_key("u", "s", "reels"))
        self.assertEqual(feed_snapshot.cache_key("u", "s"), "feed:snap:u:s")  # Home key unchanged


class ReelsBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.me)
        self.url = reverse("reels-feed")
        self._n = 0

    def reel(self, user=None, w=1080, h=1920, duration=30, media=True, likes=0, **kw):
        """A watchable reel (post + one video media row). bulk_create skips the
        post_save hooks, so no thumbnail / ffmpeg task is triggered."""
        self._n += 1
        kw.setdefault("post_type", "video")
        kw.setdefault("visibility", "public")
        kw.setdefault("moderation_status", "approved")
        kw.setdefault("category", "tech")
        post = Post.objects.create(user=user or self.stranger, content=f"r{self._n}", likes_count=likes, **kw)
        Post.objects.filter(pk=post.pk).update(created_at=OLD + datetime.timedelta(minutes=self._n))
        if media:
            PostMedia.objects.bulk_create([PostMedia(
                post=post, media_type="video", duration_seconds=duration, width=w, height=h,
                file=f"posts/x{self._n}.mp4", file_name=f"x{self._n}.mp4", file_size_bytes=1,
                mime_type="video/mp4",
            )])
        return post

    def get(self, url=None, **params):
        r = self.client.get(url or self.url, params) if params else self.client.get(url or self.url)
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        return r

    @staticmethod
    def ids(r):
        return [x["id"] for x in r.data["results"]]

    def walk(self, first, on_page=None):
        ids, r, n = self.ids(first), first, 0
        while r.data["next"]:
            n += 1
            self.assertLess(n, 60)
            if on_page:
                on_page(n)
            r = self.get(r.data["next"])
            ids += self.ids(r)
        return ids

    def feed_ids(self, **params):
        return set(self.ids(self.get(**params)))


class CandidateTests(ReelsBase):
    def test_eligible_reel_is_listed_with_feed_source(self):
        a = self.reel(self.stranger)
        b = self.reel(self.friend)
        r = self.get()
        self.assertEqual(set(r.data.keys()), {"count", "next", "previous", "results"})
        by_id = {x["id"]: x for x in r.data["results"]}
        self.assertEqual(set(by_id), {str(a.id), str(b.id)})
        self.assertEqual(by_id[str(a.id)]["feed_source"], "recommended")
        self.assertEqual(by_id[str(b.id)]["feed_source"], "following")

    def test_only_plain_public_approved_videos(self):
        ok = self.reel()
        excluded = [
            self.reel(post_type="image"),
            self.reel(post_type="text"),
            self.reel(moderation_status="pending"),
            self.reel(moderation_status="rejected"),
            self.reel(visibility="private"),
            self.reel(visibility="connections"),
            self.reel(is_sensitive=True),
            self.reel(is_deleted=True),
            self.reel(self.me),  # own
        ]
        repost = Post.objects.create(user=self.stranger, post_type="repost", original_post=ok,
                                     visibility="public", moderation_status="approved")
        got = self.feed_ids()
        self.assertEqual(got, {str(ok.id)})
        for p in excluded + [repost]:
            self.assertNotIn(str(p.id), got)

    def test_blocked_either_direction_hidden_and_muted_are_out(self):
        blocked = User.objects.create_user(username="blocked", password="x")
        blocker = User.objects.create_user(username="blocker", password="x")
        muted = User.objects.create_user(username="muted", password="x")
        BlockUser.objects.create(blocker=self.me, blocked=blocked)
        BlockUser.objects.create(blocker=blocker, blocked=self.me)
        MutedAccount.objects.create(user=self.me, muted_user=muted)
        hidden = self.reel()
        PostHide.objects.create(user=self.me, post=hidden)
        ok = self.reel()
        for u in (blocked, blocker, muted):
            self.reel(u)
        self.assertEqual(self.feed_ids(), {str(ok.id)})

    def test_private_account_only_for_followers(self):
        priv = User.objects.create_user(username="priv", password="x")
        User.objects.filter(pk=priv.pk).update(is_private=True)
        p = self.reel(priv)
        self.assertNotIn(str(p.id), self.feed_ids())
        Follow.objects.create(follower=self.me, following=priv, status=Follow.Status.ACCEPTED)
        self.assertIn(str(p.id), self.feed_ids())

    @override_settings(FEED_REELS={"fallback": False})
    def test_duration_must_be_known_and_within_cap(self):
        ok = self.reel(duration=180)  # the cap itself is fine
        no_media = self.reel(media=False)
        unknown = self.reel(duration=None)
        zero = self.reel(duration=0)
        too_long = self.reel(duration=181)
        got = self.feed_ids()
        self.assertEqual(got, {str(ok.id)})
        for p in (no_media, unknown, zero, too_long):
            self.assertNotIn(str(p.id), got)

    @override_settings(FEED_REELS={"max_duration_seconds": 60, "fallback": False})
    def test_duration_cap_is_configurable(self):
        short, long_ = self.reel(duration=60), self.reel(duration=61)
        self.assertEqual(self.feed_ids(), {str(short.id)})
        self.assertNotIn(str(long_.id), self.feed_ids())

    @override_settings(FEED_REELS={"fallback": False})
    def test_aspect_rule_and_unknown_dimensions(self):
        vertical = self.reel(w=1080, h=1920)
        boundary = self.reel(w=1000, h=1200)  # exactly 1.2
        square = self.reel(w=1000, h=1000)
        landscape = self.reel(w=1920, h=1080)
        almost = self.reel(w=1000, h=1199)
        no_dims = self.reel(w=None, h=None)
        no_height = self.reel(w=1080, h=None)
        zero_width = self.reel(w=0, h=500)
        got = self.feed_ids()
        self.assertEqual(got, {str(p.id) for p in (vertical, boundary, no_dims, no_height, zero_width)})
        for p in (square, landscape, almost):
            self.assertNotIn(str(p.id), got)

    @override_settings(FEED_REELS={"min_aspect": 0})
    def test_aspect_check_can_be_switched_off(self):
        landscape = self.reel(w=1920, h=1080)
        self.assertIn(str(landscape.id), self.feed_ids())

    @override_settings(FEED_REELS={"fallback": False})
    def test_a_non_video_media_row_does_not_make_a_reel(self):
        post = self.reel(media=False)
        PostMedia.objects.bulk_create([PostMedia(
            post=post, media_type="image", duration_seconds=10, width=1080, height=1920,
            file="posts/i.jpg", file_name="i.jpg", file_size_bytes=1, mime_type="image/jpeg")])
        self.assertNotIn(str(post.id), self.feed_ids())

    @override_settings(FEED_REELS={"fallback": False})
    def test_all_conditions_must_hold_on_the_same_media_row(self):
        # one landscape clip WITH duration + one vertical clip WITHOUT duration != a reel
        post = self.reel(w=1920, h=1080, duration=30)
        PostMedia.objects.bulk_create([PostMedia(
            post=post, media_type="video", duration_seconds=None, width=1080, height=1920,
            file="posts/y.mp4", file_name="y.mp4", file_size_bytes=1, mime_type="video/mp4")])
        self.assertNotIn(str(post.id), self.feed_ids())

    @override_settings(FEED_REELS={"enabled": False})
    def test_switch_off_returns_an_empty_page(self):
        self.reel()
        r = self.get()
        self.assertEqual((r.data["count"], r.data["results"], r.data["next"]), (0, [], None))

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))


class FallbackTests(ReelsBase):
    """Never-empty: strict rules are tier 0, looser tiers only fill a small pool."""

    def test_defaults_and_settings_keys(self):
        from django.conf import settings

        cfg = reels.get_config()
        self.assertTrue(cfg["fallback"])
        self.assertEqual(cfg["min_pool"], 10)
        for key in ("fallback", "min_pool"):
            self.assertIn(key, settings.FEED_REELS)

    def test_landscape_video_is_served_when_nothing_strict_exists(self):
        landscape = self.reel(w=1920, h=1080)
        self.assertEqual(self.ids(self.get()), [str(landscape.id)])

    def test_unknown_duration_and_long_videos_are_served_as_last_resort(self):
        unknown = self.reel(duration=None)
        long_ = self.reel(duration=900)
        self.assertEqual(self.feed_ids(), {str(unknown.id), str(long_.id)})

    def test_better_tiers_come_first_even_with_lower_score(self):
        strict = self.reel(likes=0)
        landscape = self.reel(w=1920, h=1080, likes=500)
        unknown = self.reel(duration=None, likes=900)
        self.assertEqual(self.ids(self.get()), [str(strict.id), str(landscape.id), str(unknown.id)])

    @override_settings(FEED_REELS={"min_pool": 2})
    def test_looser_tier_is_not_used_when_the_pool_is_big_enough(self):
        a, b = self.reel(), self.reel()
        landscape = self.reel(w=1920, h=1080)
        got = self.feed_ids()
        self.assertEqual(got, {str(a.id), str(b.id)})
        self.assertNotIn(str(landscape.id), got)

    @override_settings(FEED_REELS={"fallback": False})
    def test_fallback_can_be_switched_off(self):
        self.reel(w=1920, h=1080)
        self.reel(duration=None)
        self.assertEqual(self.get().data["results"], [])

    def test_safety_rules_are_never_loosened(self):
        blocked_author = User.objects.create_user(username="blk", password="x")
        BlockUser.objects.create(blocker=self.me, blocked=blocked_author)
        sensitive = self.reel(w=1920, h=1080, is_sensitive=True)
        pending = self.reel(w=1920, h=1080, moderation_status="pending")
        private_post = self.reel(w=1920, h=1080, visibility="private")
        blocked = self.reel(blocked_author, w=1920, h=1080)
        own = self.reel(self.me, w=1920, h=1080)
        no_media = self.reel(media=False)
        got = self.feed_ids()
        for p in (sensitive, pending, private_post, blocked, own, no_media):
            self.assertNotIn(str(p.id), got)

    @override_settings(FEED_SEEN_LIMITS={"fill_min": 0})
    def test_unseen_looser_video_beats_seen_strict_video(self):
        seen = self.reel(likes=99)
        PostView.objects.create(post=seen, user=self.me, is_counted=False)
        loose = self.reel(w=1920, h=1080)
        self.assertEqual(self.ids(self.get()), [str(loose.id)])

    def test_everything_seen_still_gives_a_page(self):
        a = self.reel(w=1920, h=1080)
        PostView.objects.create(post=a, user=self.me, is_counted=False)
        self.assertEqual(self.ids(self.get()), [str(a.id)])

    def test_diagnose_reports_every_stage(self):
        from .views import _home_base_qs

        self.reel()
        self.reel(w=1920, h=1080)
        rows = dict(reels.diagnose(self.me, _home_base_qs(self.me), reels.get_config(), seen_ids=set()))
        self.assertEqual(rows["video posts (all)"], 2)
        self.assertEqual(rows["+ aspect ratio OK  (= strict tier 0)"], 1)
        self.assertEqual(rows["TIER 1 (any_aspect): candidates / unseen"], "2 / 2")

    def test_diagnose_command_runs(self):
        from io import StringIO

        from django.core.management import call_command

        self.reel()
        out = StringIO()
        call_command("reels_diagnose", "--user", "me", stdout=out)
        self.assertIn("Final pool served to the user: 1 reels", out.getvalue())


class RankingTests(ReelsBase):
    def test_engagement_orders_the_pool(self):
        low, mid, high = self.reel(likes=1), self.reel(likes=10), self.reel(likes=50)
        self.assertEqual(self.ids(self.get()), [str(high.id), str(mid.id), str(low.id)])

    def test_following_bonus_lifts_followed_authors_a_little(self):
        # equal engagement: the followed author's video wins (score +10)
        stranger = self.reel(self.stranger, likes=5)
        friend = self.reel(self.friend, likes=5)
        self.assertEqual(self.ids(self.get()), [str(friend.id), str(stranger.id)])

    def test_following_bonus_does_not_own_the_feed(self):
        # a clearly stronger stranger video (+30 points of likes) still beats a weak followed one
        friend = self.reel(self.friend, likes=1)   # 3 + 10 bonus = 13
        stranger = self.reel(self.stranger, likes=10)  # 30
        self.assertEqual(self.ids(self.get()), [str(stranger.id), str(friend.id)])

    @override_settings(FEED_REELS={"following_bonus": 0})
    def test_following_bonus_is_configurable(self):
        # bonus off -> a tie is decided by recency only: the newest video wins
        friend = self.reel(self.friend, likes=5)
        stranger = self.reel(self.stranger, likes=5)  # created last
        self.assertEqual(self.ids(self.get()), [str(stranger.id), str(friend.id)])

    def test_interest_taste_and_friend_of_follow_signals(self):
        UserInterest.objects.create(user=self.me, category="sports")
        other = self.reel(self.stranger, likes=5, category="business")
        interest = self.reel(self.stranger, likes=5, category="sports")  # +15
        self.assertEqual(self.ids(self.get())[0], str(interest.id))
        # friend-of-follow: someone my friend follows gets +12
        fof = User.objects.create_user(username="fof", password="x")
        Follow.objects.create(follower=self.friend, following=fof, status=Follow.Status.ACCEPTED)
        fof_reel = self.reel(fof, likes=5, category="business")
        ids = self.ids(self.get())
        self.assertLess(ids.index(str(fof_reel.id)), ids.index(str(other.id)))

    def test_video_watch_boost_is_part_of_the_score(self):
        plain = self.reel(likes=5)
        watched = self.reel(likes=5)
        Post.objects.filter(pk=watched.pk).update(
            video_completion_rate=1.0, video_watch_count=50, video_avg_watch_seconds=30.0)
        self.assertEqual(self.ids(self.get())[0], str(watched.id))
        self.assertNotEqual(self.ids(self.get())[0], str(plain.id))

    def test_author_affinity_lifts_authors_i_like(self):
        liked_author = User.objects.create_user(username="liked", password="x")
        other = self.reel(self.stranger, likes=5)
        theirs = self.reel(liked_author, likes=5)
        older = self.reel(liked_author, likes=5)
        PostLike.objects.create(post=older, user=self.me, reaction_type="like")
        Post.objects.filter(pk=older.pk).update(likes_count=5)
        ids = self.ids(self.get())
        self.assertLess(ids.index(str(theirs.id)), ids.index(str(other.id)))

    def test_show_fewer_author_pushes_down_and_cancels_affinity(self):
        annoying = User.objects.create_user(username="annoying", password="x")
        a = self.reel(annoying, likes=10)
        b = self.reel(self.stranger, likes=10)
        FeedFeedback.objects.create(user=self.me, kind="author", key=str(annoying.pk), weight=1.0)
        self.assertEqual(self.ids(self.get()), [str(b.id), str(a.id)])

    def test_show_fewer_category_pushes_down(self):
        a = self.reel(likes=10, category="sports")
        b = self.reel(likes=10, category="tech")
        FeedFeedback.objects.create(user=self.me, kind="category", key="sports", weight=1.0)
        self.assertEqual(self.ids(self.get()), [str(b.id), str(a.id)])

    def test_pool_cap(self):
        for _ in range(6):
            self.reel()
        with override_settings(FEED_REELS={"pool_cap": 4}):
            r = self.get()
            self.assertEqual((r.data["count"], len(r.data["results"])), (4, 4))


class SeenTests(ReelsBase):
    @override_settings(FEED_SEEN_LIMITS={"fill_min": 0})
    def test_seen_videos_are_excluded(self):
        seen, fresh = self.reel(likes=99), self.reel(likes=1)
        PostView.objects.create(post=seen, user=self.me, is_counted=False)
        self.assertEqual(self.ids(self.get()), [str(fresh.id)])

    def test_tiny_pool_is_topped_up_with_seen_videos_at_the_tail(self):
        seen, fresh = self.reel(likes=99), self.reel(likes=1)
        PostView.objects.create(post=seen, user=self.me, is_counted=False)
        self.assertEqual(self.ids(self.get()), [str(fresh.id), str(seen.id)])

    @override_settings(FEED_SEEN_LIMITS={"fill_min": 0})
    def test_marking_seen_mid_scroll_does_not_shift_pages(self):
        posts = [self.reel(likes=i) for i in range(12)]
        first = self.get(page_size=4)
        ids = list(self.ids(first))
        for pid in ids:  # the client reports what it showed
            PostView.objects.create(post_id=pid, user=self.me, is_counted=False)
        ids += self.walk(self.get(first.data["next"]))
        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 12)
        self.assertEqual(set(ids), {str(p.id) for p in posts})

    @override_settings(FEED_SEEN_LIMITS={"fill_min": 0})
    def test_next_session_hides_what_was_seen(self):
        a, b = self.reel(likes=9), self.reel(likes=1)
        PostView.objects.create(post=a, user=self.me, is_counted=False)
        self.assertEqual(self.ids(self.get()), [str(b.id)])


class PaginationTests(ReelsBase):
    def setUp(self):
        super().setUp()
        self.posts = [self.reel(likes=i) for i in range(25)]

    @staticmethod
    def q(url):
        return parse_qs(urlparse(url).query)

    def test_first_page_contract(self):
        r = self.get(page_size=10)
        self.assertEqual(r.data["count"], 25)
        self.assertEqual(len(r.data["results"]), 10)
        self.assertIsNone(r.data["previous"])
        q = self.q(r.data["next"])
        self.assertIn("cursor", q)
        self.assertIn("seen_cutoff", q)
        self.assertEqual(q["page_size"], ["10"])

    def test_default_and_max_page_size(self):
        self.assertEqual(len(self.get().data["results"]), 10)
        self.assertEqual(len(self.get(page_size=500).data["results"]), 25)  # capped at 30
        self.assertEqual(len(self.get(page_size="abc").data["results"]), 10)

    def test_full_walk_no_duplicates_no_gaps_and_ranked(self):
        ids = self.walk(self.get(page_size=7))
        self.assertEqual(len(ids), 25)
        self.assertEqual(len(set(ids)), 25)
        expected = [str(p.id) for p in sorted(self.posts, key=lambda p: -p.likes_count)]
        self.assertEqual(ids, expected)

    def test_last_page_has_no_next(self):
        r = self.get(page_size=30)
        self.assertIsNone(r.data["next"])

    def test_snapshot_is_stored_under_the_reels_namespace(self):
        r = self.get(page_size=10)
        state = feed_snapshot.decode_list_cursor(self.q(r.data["next"])["cursor"][0], "reels")
        saved = feed_snapshot.load_list("reels", self.me.pk, state["snapshot_id"])
        self.assertEqual(len(saved), 25)
        self.assertIsNone(feed_snapshot.load(self.me.pk, state["snapshot_id"]))  # not a Home snapshot

    def test_order_does_not_jump_when_scores_change_mid_scroll(self):
        first = self.get(page_size=10)
        # the lowest-ranked video suddenly becomes the most liked one
        lowest = self.posts[0]
        Post.objects.filter(pk=lowest.pk).update(likes_count=10_000)
        ids = self.walk(first)
        self.assertEqual(len(set(ids)), 25)
        self.assertEqual(ids[-1], str(lowest.id))  # frozen: still last
        # ... but a NEW session re-ranks
        self.assertEqual(self.ids(self.get(page_size=10))[0], str(lowest.id))

    def test_page_size_can_change_between_pages(self):
        first = self.get(page_size=5)
        from rest_framework.utils.urls import replace_query_param
        # (client.get(url, params) would REPLACE the url's query string, so edit the url itself)
        second = self.get(replace_query_param(first.data["next"], "page_size", 20))
        self.assertEqual(len(first.data["results"]) + len(second.data["results"]), 25)
        self.assertEqual(len(set(self.ids(first)) | set(self.ids(second))), 25)

    def test_invalid_cursor_is_404(self):
        self.assertEqual(self.client.get(self.url, {"cursor": "garbage"}).status_code, 404)
        home_cursor = feed_snapshot.encode_cursor(feed_snapshot.new_snapshot_id(),
                                                  {s: 0 for s in feed_mix.SOURCES}, None)
        self.assertEqual(self.client.get(self.url, {"cursor": home_cursor}).status_code, 404)

    def test_expired_snapshot_falls_back_and_continues(self):
        first = self.get(page_size=10)
        cache.clear()  # TTL passed / Redis flushed
        second = self.get(first.data["next"])
        self.assertEqual(len(second.data["results"]), 10)
        self.assertFalse(set(self.ids(first)) & set(self.ids(second)))
        # ... and the rebuilt snapshot was saved again under the same id
        state = feed_snapshot.decode_list_cursor(self.q(first.data["next"])["cursor"][0], "reels")
        self.assertIsNotNone(feed_snapshot.load_list("reels", self.me.pk, state["snapshot_id"]))

    def test_cursor_of_another_user_never_returns_my_list(self):
        first = self.get(page_size=10)
        state = feed_snapshot.decode_list_cursor(self.q(first.data["next"])["cursor"][0], "reels")
        other = User.objects.create_user(username="other", password="x")
        self.client.force_authenticate(other)
        self.assertIsNone(feed_snapshot.load_list("reels", other.pk, state["snapshot_id"]))  # keyed by user
        r = self.get(first.data["next"])  # miss -> ranked for THEM, never my stored list
        self.assertTrue(set(self.ids(r)) <= {str(p.id) for p in self.posts})

    def test_deleted_video_between_pages_is_skipped_not_500(self):
        first = self.get(page_size=10)
        second_ids = self.ids(self.get(first.data["next"]))
        Post.objects.filter(pk=second_ids[0]).update(is_deleted=True)
        again = self.get(first.data["next"])
        self.assertEqual(len(again.data["results"]), 9)
        self.assertNotIn(second_ids[0], self.ids(again))

    def test_video_made_private_or_author_blocked_between_pages_is_skipped(self):
        first = self.get(page_size=10)
        page2 = self.ids(self.get(first.data["next"]))
        Post.objects.filter(pk=page2[0]).update(visibility="private")
        BlockUser.objects.create(blocker=self.me, blocked=self.stranger)
        self.assertEqual(self.get(first.data["next"]).data["results"], [])


class StartTests(ReelsBase):
    def setUp(self):
        super().setUp()
        self.posts = [self.reel(likes=i) for i in range(12)]
        self.lowest = self.posts[0]

    def test_start_video_is_first_and_never_repeats(self):
        r = self.get(page_size=5, start=self.lowest.id)
        self.assertEqual(self.ids(r)[0], str(self.lowest.id))
        ids = self.walk(r)
        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 12)
        self.assertEqual(ids[0], str(self.lowest.id))
        self.assertEqual(r.data["count"], 12)  # not 13

    def test_start_survives_the_cursor_and_is_ignored_on_later_pages(self):
        r = self.get(page_size=5, start=self.lowest.id)
        second = self.get(r.data["next"])
        self.assertNotIn(str(self.lowest.id), self.ids(second))

    def test_start_also_works_after_a_cache_miss(self):
        r = self.get(page_size=5, start=self.lowest.id)
        cache.clear()
        ids = self.ids(r) + self.walk(self.get(r.data["next"]))
        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 12)

    def test_start_may_be_a_video_that_fails_the_reel_rules(self):
        landscape = self.reel(w=1920, h=1080, duration=600, media=True)  # too long AND horizontal
        r = self.get(start=landscape.id)
        self.assertEqual(self.ids(r)[0], str(landscape.id))
        self.assertNotIn(str(landscape.id), self.ids(self.get()))  # but not a candidate on its own

    def test_start_may_be_my_own_video(self):
        mine = self.reel(self.me)
        self.assertNotIn(str(mine.id), self.ids(self.get()))
        self.assertEqual(self.ids(self.get(start=mine.id))[0], str(mine.id))

    def test_start_never_plays_a_connections_only_video(self):
        conn = self.reel(self.friend, visibility="connections")
        # Reels only ever plays public videos, even when the caller could see it elsewhere
        self.assertNotEqual(self.ids(self.get(start=conn.id))[0], str(conn.id))

    def test_unwatchable_start_is_ignored_silently(self):
        hidden_author = User.objects.create_user(username="ha", password="x")
        BlockUser.objects.create(blocker=self.me, blocked=hidden_author)
        priv_author = User.objects.create_user(username="pa", password="x")
        User.objects.filter(pk=priv_author.pk).update(is_private=True)
        bad = [
            self.reel(visibility="private"),
            self.reel(is_deleted=True),
            self.reel(moderation_status="pending"),
            self.reel(is_sensitive=True),
            self.reel(post_type="image"),
            self.reel(hidden_author),
            self.reel(priv_author),
        ]
        hidden = self.reel()
        PostHide.objects.create(user=self.me, post=hidden)
        bad.append(hidden)
        baseline = self.ids(self.get())
        for p in bad:
            r = self.get(start=p.id)
            self.assertEqual(self.ids(r), baseline, p.id)
            self.assertNotIn(str(p.id), self.ids(r))

    def test_garbage_start_is_ignored(self):
        baseline = self.ids(self.get())
        self.assertEqual(self.ids(self.get(start="not-a-uuid")), baseline)
        self.assertEqual(self.ids(self.get(start="6f1a4c1e-8b7e-4a11-9c55-0d5f0e2c9a11")), baseline)


class PayloadTests(ReelsBase):
    """Lean ReelSerializer payload - NOT the full PostListSerializer."""

    TOP = {"id", "video", "caption", "hashtags", "author", "counts",
           "is_liked", "my_reaction", "is_saved", "feed_source"}

    def full_reel(self, user=None, **kw):
        kw.setdefault("hashtags", ["python", "dsa"])
        kw.setdefault("likes", 7)
        kw.setdefault("comments_count", 3)
        kw.setdefault("shares_count", 2)
        kw.setdefault("saves_count", 5)
        p = self.reel(user, **kw)
        PostMedia.objects.filter(post=p).update(
            blur_hash="LEHV6nWB2yk8pyo0adR*.7kCMdnj", thumbnail="posts/thumbnails/t.jpg",
        )
        return p

    def one(self, post):
        return next(x for x in self.get().data["results"] if x["id"] == str(post.id))

    def test_exact_top_level_keys_and_no_full_post_fields(self):
        item = self.one(self.full_reel())
        self.assertEqual(set(item), self.TOP)
        for heavy in ("poll", "media", "original_post", "user", "content", "title", "best_answer", "category"):
            self.assertNotIn(heavy, item)

    def test_video_block(self):
        p = self.full_reel(w=720, h=1280, duration=42)
        v = self.one(p)["video"]
        self.assertEqual(set(v), {"url", "thumbnail", "duration", "width", "height", "blur_hash"})
        self.assertTrue(v["url"].startswith("http") and v["url"].endswith("x%d.mp4" % self._n))
        self.assertTrue(v["thumbnail"].startswith("http") and v["thumbnail"].endswith("t.jpg"))
        self.assertEqual((v["duration"], v["width"], v["height"]), (42, 720, 1280))
        self.assertEqual(v["blur_hash"], "LEHV6nWB2yk8pyo0adR*.7kCMdnj")

    def test_video_block_nulls_when_thumbnail_and_blur_hash_are_missing(self):
        v = self.one(self.reel())["video"]
        self.assertIsNone(v["thumbnail"])
        self.assertIsNone(v["blur_hash"])
        self.assertIsNotNone(v["url"])

    def test_caption_hashtags_and_counts(self):
        p = self.full_reel()
        item = self.one(p)
        self.assertEqual(item["caption"], p.content)
        self.assertEqual(item["hashtags"], ["python", "dsa"])
        self.assertEqual(item["counts"], {"likes": 7, "comments": 3, "shares": 2, "saves": 5})

    def test_empty_caption_is_an_empty_string_not_null(self):
        p = self.reel()
        Post.objects.filter(pk=p.pk).update(content=None)
        self.assertEqual(self.one(p)["caption"], "")

    def test_author_block(self):
        User.objects.filter(pk=self.friend.pk).update(first_name="Fay", last_name="Friend")
        p = self.reel(self.friend)
        a = self.one(p)["author"]
        self.assertEqual(set(a), {"id", "username", "names", "profile_photo", "is_following"})
        self.assertEqual(a["id"], str(self.friend.id))
        self.assertEqual(a["username"], "friend")
        self.assertEqual(a["names"], "Fay Friend")
        self.assertIsNone(a["profile_photo"])
        self.assertTrue(a["is_following"])

    def test_author_names_is_an_empty_string_without_a_name(self):
        self.assertEqual(self.one(self.reel(self.stranger))["author"]["names"], "")

    def test_is_following_false_for_strangers_and_for_a_pending_request(self):
        self.assertFalse(self.one(self.reel(self.stranger))["author"]["is_following"])
        pending = User.objects.create_user(username="pending", password="x")
        Follow.objects.create(follower=self.me, following=pending, status=Follow.Status.PENDING)
        self.assertFalse(self.one(self.reel(pending))["author"]["is_following"])

    def test_profile_photo_is_an_absolute_url(self):
        User.objects.filter(pk=self.stranger.pk).update(profile_photo="profile/s.jpg")
        self.assertTrue(self.one(self.reel(self.stranger))["author"]["profile_photo"].startswith("http"))

    def test_viewer_state_defaults_and_values(self):
        plain, liked, saved, both = self.reel(), self.reel(), self.reel(), self.reel()
        PostLike.objects.create(post=liked, user=self.me, reaction_type="imp")
        PostSave.objects.create(post=saved, user=self.me)
        PostLike.objects.create(post=both, user=self.me, reaction_type="like")
        PostSave.objects.create(post=both, user=self.me)
        by_id = {x["id"]: x for x in self.get().data["results"]}
        state = lambda p: (by_id[str(p.id)]["is_liked"], by_id[str(p.id)]["my_reaction"], by_id[str(p.id)]["is_saved"])
        self.assertEqual(state(plain), (False, None, False))
        self.assertEqual(state(liked), (True, "imp", False))
        self.assertEqual(state(saved), (False, None, True))
        self.assertEqual(state(both), (True, "like", True))

    def test_feed_source_values(self):
        a, b = self.reel(self.stranger), self.reel(self.friend)
        self.assertEqual(self.one(a)["feed_source"], "recommended")
        self.assertEqual(self.one(b)["feed_source"], "following")

    def test_start_video_of_mine_has_is_following_false(self):
        mine = self.reel(self.me)
        first = self.get(start=mine.id).data["results"][0]
        self.assertEqual(first["id"], str(mine.id))
        self.assertFalse(first["author"]["is_following"])

    def test_video_post_without_a_video_file_is_never_served(self):
        ghost = self.reel(media=False)
        ok = self.reel()
        self.assertEqual(self.ids(self.get(start=ghost.id)), [str(ok.id)])

    def test_serializer_alone_falls_back_to_per_post_queries(self):
        # no view context (following_ids / reel_reactions / reel_saved_ids missing) -> still correct
        from rest_framework.test import APIRequestFactory

        friend_post, other = self.reel(self.friend), self.reel(self.stranger)
        PostLike.objects.create(post=friend_post, user=self.me, reaction_type="wrong")
        PostSave.objects.create(post=friend_post, user=self.me)
        req = APIRequestFactory().get("/post/reels/")
        req.user = self.me
        qs = Post.objects.select_related("user").prefetch_related("media")
        data = {d["id"]: d for d in ReelSerializer(qs.filter(pk__in=[friend_post.pk, other.pk]),
                                                    many=True, context={"request": req}).data}
        f = data[str(friend_post.id)]
        self.assertEqual((f["is_liked"], f["my_reaction"], f["is_saved"], f["author"]["is_following"]),
                         (True, "wrong", True, True))
        self.assertEqual(data[str(other.id)]["is_liked"], False)
        self.assertIsNone(data[str(other.id)]["feed_source"])

    def test_query_count_does_not_grow_with_the_page(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count(n):
            Post.objects.filter(post_type="video").delete()
            cache.clear()
            posts = [self.reel(self.friend if i % 2 else self.stranger, likes=i) for i in range(n)]
            for p in posts[::2]:
                PostLike.objects.create(post=p, user=self.me)
                PostSave.objects.create(post=p, user=self.me)
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(len(self.get(page_size=30).data["results"]), n)
            return len(ctx.captured_queries)

        few, many = count(3), count(24)
        self.assertEqual(few, many, "queries grew with the page size (N+1)")


class IsolationTests(ReelsBase):
    """Everything personal is per viewer: another user's state never leaks in."""

    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user(username="other", password="x")

    def as_user(self, user):
        cache.clear()
        self.client.force_authenticate(user)

    def test_likes_saves_and_follows_of_another_user_do_not_show_as_mine(self):
        p = self.reel(self.stranger)
        PostLike.objects.create(post=p, user=self.other, reaction_type="imp")
        PostSave.objects.create(post=p, user=self.other)
        Follow.objects.create(follower=self.other, following=self.stranger, status=Follow.Status.ACCEPTED)
        item = self.get().data["results"][0]
        self.assertEqual((item["is_liked"], item["my_reaction"], item["is_saved"]), (False, None, False))
        self.assertFalse(item["author"]["is_following"])
        self.assertEqual(item["feed_source"], "recommended")
        self.as_user(self.other)
        theirs = self.get().data["results"][0]
        self.assertEqual((theirs["is_liked"], theirs["my_reaction"], theirs["is_saved"]), (True, "imp", True))
        self.assertTrue(theirs["author"]["is_following"])
        self.assertEqual(theirs["feed_source"], "following")

    def test_my_hide_mute_and_seen_do_not_affect_another_user(self):
        hidden, muted_post, seen = self.reel(), self.reel(self.friend), self.reel()
        PostHide.objects.create(user=self.me, post=hidden)
        MutedAccount.objects.create(user=self.me, muted_user=self.friend)
        PostView.objects.create(post=seen, user=self.me, is_counted=False)
        self.assertNotIn(str(hidden.id), self.feed_ids())
        self.assertNotIn(str(muted_post.id), self.feed_ids())
        self.as_user(self.other)
        self.assertEqual(self.feed_ids(), {str(hidden.id), str(muted_post.id), str(seen.id)})

    def test_show_fewer_of_another_user_does_not_change_my_order(self):
        low, high = self.reel(likes=1, category="tech"), self.reel(likes=50, category="math")
        FeedFeedback.objects.create(user=self.other, kind="category", key="math", weight=3.0)
        self.assertEqual(self.ids(self.get()), [str(high.id), str(low.id)])

    def test_someone_elses_cursor_never_returns_their_list(self):
        for i in range(6):
            self.reel(likes=i)
        first = self.get(page_size=2)
        mine = set(self.ids(first))
        self.as_user(self.other)
        r = self.get(first.data["next"])  # my cursor, other user: rebuilt for THEM, no 404/500, no mine-only data
        self.assertEqual(len(r.data["results"]), 2)
        self.assertTrue(all(x["is_liked"] is False for x in r.data["results"]))
        self.assertTrue(mine)  # (the other user simply gets their own ranking)

    def test_own_videos_are_never_recommended_to_me_but_are_to_others(self):
        mine = self.reel(self.me)
        self.assertNotIn(str(mine.id), self.feed_ids())
        self.as_user(self.other)
        self.assertIn(str(mine.id), self.feed_ids())


class MidScrollAndSeenEndpointTests(ReelsBase):
    def test_hidden_or_muted_between_pages_is_skipped(self):
        posts = [self.reel(likes=i) for i in range(8)]
        first = self.get(page_size=3)
        shown = set(self.ids(first))
        rest = [p for p in posts if str(p.id) not in shown]
        PostHide.objects.create(user=self.me, post=rest[0])
        MutedAccount.objects.create(user=self.me, muted_user=self.stranger)  # everyone here is 'stranger'
        later = self.walk(first)
        self.assertEqual(len(later), len(set(later)))
        for p in rest:
            self.assertNotIn(str(p.id), later[len(shown):])

    @override_settings(FEED_SEEN_LIMITS={"fill_min": 0})
    def test_existing_feed_seen_endpoint_drives_next_session_no_reels_endpoint(self):
        a, b = self.reel(likes=9), self.reel(likes=1)
        r = self.client.post(reverse("feed-seen"), {"post_ids": [str(a.id)]}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.ids(self.get()), [str(b.id)])
        # nothing Reels-specific to POST to: the reels URL is GET-only
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 405)
        self.assertEqual(self.client.delete(self.url).status_code, 405)

    def test_seen_does_not_bump_views_count(self):
        a = self.reel()
        before = Post.objects.get(pk=a.pk).views_count
        self.client.post(reverse("feed-seen"), {"post_ids": [str(a.id)]}, format="json")
        self.assertEqual(Post.objects.get(pk=a.pk).views_count, before)


class CursorStabilityPayloadTests(ReelsBase):
    def test_payload_of_a_reel_is_identical_on_every_page_it_could_appear(self):
        posts = [self.reel(likes=i) for i in range(6)]
        first = self.get(page_size=2)
        seen_items = {x["id"]: x for x in first.data["results"]}
        r = first
        while r.data["next"]:
            r = self.get(r.data["next"])
            for x in r.data["results"]:
                self.assertNotIn(x["id"], seen_items)  # cursor stability: no repeats
                self.assertEqual(set(x), PayloadTests.TOP)  # every page has the lean shape
                seen_items[x["id"]] = x
        self.assertEqual(set(seen_items), {str(p.id) for p in posts})
