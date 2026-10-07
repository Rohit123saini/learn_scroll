"""
user_profile/tests_block_system.py

Instagram-style block / unblock, end to end:
  * blocked person -> profile 404 (doesn't leak who blocked whom)
  * blocker       -> profile 200 with a minimal card + is_blocked_by_me=True
  * both block tables (user_profile.BlockUser / message.BlockedUser) stay in sync
  * posts grid, post detail and commenting are closed off in both directions
  * unblock restores everything

Run:  python manage.py test user_profile.tests_block_system
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from message.models import BlockedUser
from post.models import Post
from user_profile.models import BlockUser, Follow

User = get_user_model()


def _user(name, **kw):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345", **kw)


def _post(user, **kw):
    fields = dict(user=user, post_type="text", content="hello", visibility="public", moderation_status="approved")
    fields.update(kw)
    return Post.objects.create(**fields)


def _results(resp):
    data = resp.json()
    return data["results"] if isinstance(data, dict) and "results" in data else data


class BlockProfileVisibilityTests(APITestCase):
    def setUp(self):
        self.alice = _user("blk_alice")
        self.bob = _user("blk_bob", bio="bob secret bio")
        self.profile_url = lambda u: reverse("user-profile-detail", args=[u.username])

    def test_normal_profile_reports_not_blocked(self):
        self.client.force_authenticate(self.alice)
        r = self.client.get(self.profile_url(self.bob))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["is_blocked_by_me"])

    def test_blocker_sees_minimal_card_with_flag(self):
        self.client.force_authenticate(self.alice)
        self.client.post(reverse("blocked-users"), {"blocked": self.bob.id})
        r = self.client.get(self.profile_url(self.bob))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["is_blocked_by_me"])
        self.assertTrue(r.data["is_restricted_view"])
        self.assertEqual(r.data["data"]["username"], self.bob.username)
        for leaked in ("bio", "followers_count", "following_count", "posts_count", "links"):
            self.assertNotIn(leaked, r.data["data"])
        self.assertIsNone(r.data["my_follow_status"])

    def test_blocked_person_gets_404(self):
        BlockUser.objects.create(blocker=self.bob, blocked=self.alice)
        self.client.force_authenticate(self.alice)
        self.assertEqual(self.client.get(self.profile_url(self.bob)).status_code, 404)

    def test_unblock_restores_full_profile(self):
        self.client.force_authenticate(self.alice)
        self.client.post(reverse("blocked-users"), {"blocked": self.bob.id})
        self.assertEqual(self.client.delete(reverse("unblock-user", args=[self.bob.id])).status_code, 200)
        r = self.client.get(self.profile_url(self.bob))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["is_blocked_by_me"])
        self.assertEqual(r.data["data"]["bio"], "bob secret bio")


class BlockTablesStayInSyncTests(APITestCase):
    def setUp(self):
        self.alice = _user("sync_alice")
        self.bob = _user("sync_bob")

    def test_profile_block_creates_chat_block_and_unblock_removes_it(self):
        self.client.force_authenticate(self.alice)
        self.client.post(reverse("blocked-users"), {"blocked": self.bob.id})
        self.assertTrue(BlockedUser.objects.filter(blocker=self.alice, blocked=self.bob).exists())

        self.client.delete(reverse("unblock-user", args=[self.bob.id]))
        self.assertFalse(BlockedUser.objects.filter(blocker=self.alice, blocked=self.bob).exists())
        self.assertFalse(BlockUser.objects.filter(blocker=self.alice, blocked=self.bob).exists())

    def test_chat_block_creates_profile_block_and_removes_follows(self):
        Follow.objects.create(follower=self.alice, following=self.bob)
        BlockedUser.objects.create(blocker=self.alice, blocked=self.bob)
        self.assertTrue(BlockUser.objects.filter(blocker=self.alice, blocked=self.bob).exists())
        self.assertFalse(Follow.objects.filter(follower=self.alice, following=self.bob).exists())

    def test_chat_unblock_removes_profile_block(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        BlockedUser.objects.get(blocker=self.alice, blocked=self.bob).delete()
        self.assertFalse(BlockUser.objects.filter(blocker=self.alice, blocked=self.bob).exists())

    def test_no_duplicates_after_round_trip(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        BlockUser.objects.filter(blocker=self.alice, blocked=self.bob).delete()
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        self.assertEqual(BlockedUser.objects.filter(blocker=self.alice, blocked=self.bob).count(), 1)
        self.assertEqual(BlockUser.objects.filter(blocker=self.alice, blocked=self.bob).count(), 1)


class BlockContentAccessTests(APITestCase):
    def setUp(self):
        self.alice = _user("cont_alice")
        self.bob = _user("cont_bob")
        self.bob_post = _post(self.bob)
        self.alice_post = _post(self.alice)

    def _grid(self, viewer, owner):
        self.client.force_authenticate(viewer)
        r = self.client.get(reverse("post-list"), {"target_user_id": str(owner.id)})
        self.assertEqual(r.status_code, 200)
        return _results(r)

    def _detail(self, viewer, post):
        self.client.force_authenticate(viewer)
        return self.client.get(reverse("post-detail", kwargs={"id": post.id}))

    def test_everything_visible_without_block(self):
        self.assertEqual(len(self._grid(self.alice, self.bob)), 1)
        self.assertEqual(self._detail(self.alice, self.bob_post).status_code, 200)

    def test_posts_grid_empty_in_both_directions(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        self.assertEqual(self._grid(self.alice, self.bob), [])
        self.assertEqual(self._grid(self.bob, self.alice), [])

    def test_own_posts_still_visible_to_author(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        self.assertEqual(len(self._grid(self.alice, self.alice)), 1)
        self.assertEqual(self._detail(self.alice, self.alice_post).status_code, 200)

    def test_post_detail_404_in_both_directions(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        self.assertEqual(self._detail(self.alice, self.bob_post).status_code, 404)
        self.assertEqual(self._detail(self.bob, self.alice_post).status_code, 404)

    def test_cannot_comment_on_blocked_users_post(self):
        BlockUser.objects.create(blocker=self.bob, blocked=self.alice)
        self.client.force_authenticate(self.alice)
        r = self.client.post(
            reverse("comment-create"),
            {"post_id": str(self.bob_post.id), "content": "hi"},
            format="json",
        )
        self.assertEqual(r.status_code, 404)

    def test_unblock_reopens_posts(self):
        block = BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        block.delete()
        self.assertEqual(len(self._grid(self.alice, self.bob)), 1)
        self.assertEqual(self._detail(self.alice, self.bob_post).status_code, 200)


# ======================================================================
# High-priority round 2: notifications, comments, lists, mentions,
# groups, calls, share / reactions
# ======================================================================
from core.models import Notification
from core.services import create_notification, unread_badge_count
from message.mentions import extract_mentioned_user_ids
from message.models import (
    CallParticipant, CallSession, Conversation, ConversationParticipant, ConversationType,
)
from message.services import add_members_to_group, create_group
from post.models import PostComment
from post.services import hidden_commenter_ids
from user_profile.services import blocked_user_ids, is_blocked_pair


class BlockHelperTests(APITestCase):
    def test_blocked_ids_and_pair_are_symmetric(self):
        a, b, c = _user("h_a"), _user("h_b"), _user("h_c")
        BlockUser.objects.create(blocker=a, blocked=b)
        self.assertEqual(blocked_user_ids(a), {b.id})
        self.assertEqual(blocked_user_ids(b), {a.id})
        self.assertEqual(blocked_user_ids(c), set())
        self.assertTrue(is_blocked_pair(a, b) and is_blocked_pair(b.id, a.id))
        self.assertFalse(is_blocked_pair(a, c))


class BlockNotificationTests(APITestCase):
    def setUp(self):
        self.alice, self.bob = _user("n_alice"), _user("n_bob")

    def _like(self, recipient, actor):
        return create_notification(recipient, "post_liked", "liked your post", actor=actor)

    def test_no_new_notification_in_either_direction_while_blocked(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        self.assertIsNone(self._like(self.alice, self.bob))
        self.assertIsNone(self._like(self.bob, self.alice))
        self.assertEqual(Notification.objects.count(), 0)

    def test_old_notifications_hidden_then_restored_on_unblock(self):
        row = self._like(self.alice, self.bob)
        self.assertIsNotNone(row)
        self.assertEqual(Notification.objects.for_user(self.alice).count(), 1)
        self.assertEqual(unread_badge_count(self.alice.id), 1)

        block = BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        self.assertEqual(Notification.objects.for_user(self.alice).count(), 0)
        self.assertEqual(unread_badge_count(self.alice.id), 0)
        self.assertEqual(Notification.objects.filter(pk=row.pk).count(), 1)  # hidden, not deleted

        block.delete()
        self.assertEqual(Notification.objects.for_user(self.alice).count(), 1)

    def test_system_notifications_without_actor_unaffected(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        row = create_notification(self.alice, "post_liked", "system", data={})
        self.assertIsNotNone(row)
        self.assertEqual(Notification.objects.for_user(self.alice).count(), 1)

    def test_bell_api_hides_blocked_actor(self):
        self._like(self.alice, self.bob)
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        self.client.force_authenticate(self.alice)
        r = self.client.get(reverse("notification-list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 0)


class BlockCommentsAndListsTests(APITestCase):
    def setUp(self):
        self.alice, self.bob, self.carol = _user("c_alice"), _user("c_bob"), _user("c_carol")
        self.post = _post(self.alice)
        PostComment.objects.create(post=self.post, user=self.bob, content="from bob")
        PostComment.objects.create(post=self.post, user=self.carol, content="from carol")

    def _comment_texts(self, viewer):
        self.client.force_authenticate(viewer)
        r = self.client.get(reverse("comment-list", kwargs={"post_id": self.post.id}))
        self.assertEqual(r.status_code, 200)
        return {c["content"] for c in r.json()}

    def test_blocked_users_comments_hidden_both_ways(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        # Post owner no longer sees the blocked person's comment...
        self.assertEqual(self._comment_texts(self.alice), {"from carol"})
        # ...third parties are unaffected...
        self.assertEqual(self._comment_texts(self.carol), {"from bob", "from carol"})
        # ...and the blocked person can't open the owner's comments at all.
        self.client.force_authenticate(self.bob)
        r = self.client.get(reverse("comment-list", kwargs={"post_id": self.post.id}))
        self.assertEqual(r.status_code, 404)

    def test_unblock_brings_comments_back(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob).delete()
        self.assertEqual(self._comment_texts(self.alice), {"from bob", "from carol"})

    def test_hidden_commenter_ids_includes_blocked(self):
        BlockUser.objects.create(blocker=self.carol, blocked=self.bob)
        self.assertIn(self.bob.id, hidden_commenter_ids(self.alice.id, self.carol.id))
        self.assertNotIn(self.bob.id, hidden_commenter_ids(self.alice.id, self.alice.id))

    def test_followers_and_following_lists_skip_blocked(self):
        Follow.objects.create(follower=self.bob, following=self.alice, status=Follow.Status.ACCEPTED)
        Follow.objects.create(follower=self.carol, following=self.alice, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.alice)
        url = reverse("user-followers", args=[self.alice.username])
        names = {u["username"] for u in _results(self.client.get(url))}
        self.assertEqual(names, {self.bob.username, self.carol.username})
        # Bob is blocked by Carol -> Carol must not see Bob in the list.
        BlockUser.objects.create(blocker=self.carol, blocked=self.bob)
        self.client.force_authenticate(self.carol)
        names = {u["username"] for u in _results(self.client.get(url))}
        self.assertNotIn(self.bob.username, names)

    def test_cannot_react_to_blocked_users_post(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        self.client.force_authenticate(self.bob)
        r = self.client.post(reverse("post-reaction", kwargs={"post_id": self.post.id}), {"reaction": "like"}, format="json")
        self.assertEqual(r.status_code, 404)


class BlockMentionsAndGroupsTests(APITestCase):
    def setUp(self):
        self.alice, self.bob, self.carol = _user("m_alice"), _user("m_bob"), _user("m_carol")
        self.group = create_group(created_by=self.alice, name="Study", member_ids=[self.carol.id])
        self.conversation = self.group.conversation

    def test_chat_mention_skips_blocked_pair(self):
        add_members_to_group(group=self.group, actor=None, user_ids=[self.bob.id])
        text = f"hi @{self.bob.username} and @{self.carol.username}"
        self.assertEqual(
            set(extract_mentioned_user_ids(text, self.conversation, sender_id=self.alice.id)),
            {self.bob.id, self.carol.id},
        )
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        self.assertEqual(
            extract_mentioned_user_ids(text, self.conversation, sender_id=self.alice.id), [self.carol.id]
        )
        # the blocked person can't mention the blocker back either
        text2 = f"yo @{self.alice.username}"
        self.assertEqual(extract_mentioned_user_ids(text2, self.conversation, sender_id=self.bob.id), [])

    def test_cannot_add_blocked_user_to_group(self):
        BlockUser.objects.create(blocker=self.bob, blocked=self.alice)  # bob blocked the adder
        users = add_members_to_group(group=self.group, actor=self.alice, user_ids=[self.bob.id, self.carol.id])
        self.assertEqual(users, [])  # carol already in, bob refused
        self.assertFalse(self.group.group_members.filter(user=self.bob).exists())

    def test_system_caller_is_not_filtered(self):
        BlockUser.objects.create(blocker=self.bob, blocked=self.alice)
        users = add_members_to_group(group=self.group, actor=None, user_ids=[self.bob.id])
        self.assertEqual([u.id for u in users], [self.bob.id])

    def test_create_group_skips_blocked_members(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        group = create_group(created_by=self.alice, name="Two", member_ids=[self.bob.id, self.carol.id])
        member_ids = set(group.group_members.values_list("user_id", flat=True))
        self.assertIn(self.carol.id, member_ids)
        self.assertNotIn(self.bob.id, member_ids)

    def test_post_tag_of_blocked_user_is_dropped(self):
        from post.serializers import PostCreateSerializer

        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)

        class _Req:
            user = self.alice

        s = PostCreateSerializer(context={"request": _Req()})
        self.assertEqual(s.validate_mentioned_user_ids([self.bob.id, self.carol.id]), [self.carol.id])


class BlockEndsCallsTests(APITestCase):
    def test_block_ends_active_one_to_one_call_only(self):
        alice, bob, carol = _user("call_alice"), _user("call_bob"), _user("call_carol")
        conv = Conversation.objects.create(type=ConversationType.PRIVATE)
        ConversationParticipant.objects.create(conversation=conv, user=alice)
        ConversationParticipant.objects.create(conversation=conv, user=bob)

        def _call(caller, other, name):
            call = CallSession.objects.create(
                type="audio", status="ongoing", conversation=conv, caller=caller, channel_name=name,
            )
            CallParticipant.objects.create(call=call, user=caller, status="ongoing")
            CallParticipant.objects.create(call=call, user=other, status="ongoing")
            return call

        call_ab = _call(alice, bob, "room-ab")
        call_ac = _call(alice, carol, "room-ac")

        with self.captureOnCommitCallbacks(execute=True):
            BlockUser.objects.create(blocker=alice, blocked=bob)

        call_ab.refresh_from_db()
        call_ac.refresh_from_db()
        self.assertIn(call_ab.status, ("ended", "missed"))
        self.assertIsNotNone(call_ab.ended_at)
        self.assertEqual(call_ac.status, "ongoing")  # unrelated call untouched
        self.assertFalse(CallParticipant.objects.filter(call=call_ab, left_at__isnull=True).exists())


class BlockShareTests(APITestCase):
    def test_cannot_share_blocked_users_post_to_chat(self):
        alice, bob = _user("s_alice"), _user("s_bob")
        post = _post(bob)
        BlockUser.objects.create(blocker=bob, blocked=alice)
        self.client.force_authenticate(alice)
        r = self.client.post(reverse("post-share", kwargs={"id": post.id}), {"conversation_id": "00000000-0000-0000-0000-000000000000"}, format="json")
        self.assertEqual(r.status_code, 404)


# ======================================================================
# Medium round: report, block+report, block-list search/paging,
# "also block new accounts", mute (feed + stories), restrict flag
# ======================================================================
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from message.models import DeviceToken
from post.models import MutedAccount, Story
from user_profile.models import ContentReport, RestrictUser
from user_profile.services import block_new_accounts_of


class ReportTests(APITestCase):
    def setUp(self):
        self.alice, self.bob = _user("r_alice"), _user("r_bob")
        self.post = _post(self.bob)
        self.comment = PostComment.objects.create(post=self.post, user=self.bob, content="rude")
        self.client.force_authenticate(self.alice)
        self.url = reverse("content-report")

    def _report(self, target_type, target_id, reason="spam", **extra):
        return self.client.post(
            self.url, {"target_type": target_type, "target_id": str(target_id), "reason": reason, **extra}, format="json"
        )

    def test_report_account_post_and_comment(self):
        for tt, tid in (("user", self.bob.id), ("post", self.post.id), ("comment", self.comment.id)):
            r = self._report(tt, tid)
            self.assertEqual(r.status_code, 201, (tt, r.content))
        self.assertEqual(ContentReport.objects.filter(reporter=self.alice, reported_user=self.bob).count(), 3)

    def test_duplicate_report_is_idempotent(self):
        self.assertEqual(self._report("user", self.bob.id).status_code, 201)
        self.assertEqual(self._report("user", self.bob.id, reason="scam").status_code, 200)
        self.assertEqual(ContentReport.objects.count(), 1)

    def test_cannot_report_yourself_or_missing_or_bad_reason(self):
        self.assertEqual(self._report("user", self.alice.id).status_code, 400)
        self.assertEqual(self._report("user", 999999).status_code, 404)
        self.assertEqual(self._report("post", "not-a-uuid").status_code, 404)
        self.assertEqual(self._report("user", self.bob.id, reason="because").status_code, 400)

    def test_rate_limit(self):
        carol = _user("r_carol")
        with mock.patch("user_profile.services.REPORTS_PER_HOUR", 1):
            self.assertEqual(self._report("user", self.bob.id).status_code, 201)
            self.assertEqual(self._report("user", carol.id).status_code, 429)

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self._report("user", self.bob.id).status_code, (401, 403))


class BlockAndReportTests(APITestCase):
    def setUp(self):
        self.alice, self.bob = _user("br_alice"), _user("br_bob")
        self.client.force_authenticate(self.alice)

    def test_block_with_report_does_both(self):
        r = self.client.post(
            reverse("blocked-users"),
            {"blocked": self.bob.id, "report_reason": "harassment", "report_details": "dms"},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()["report_filed"])
        self.assertTrue(BlockUser.objects.filter(blocker=self.alice, blocked=self.bob).exists())
        self.assertTrue(ContentReport.objects.filter(reporter=self.alice, reported_user=self.bob, reason="harassment").exists())

    def test_plain_block_files_no_report(self):
        r = self.client.post(reverse("blocked-users"), {"blocked": self.bob.id}, format="json")
        self.assertFalse(r.json()["report_filed"])
        self.assertEqual(ContentReport.objects.count(), 0)

    def test_reported_user_is_not_told_anything(self):
        self.client.post(reverse("blocked-users"), {"blocked": self.bob.id, "report_reason": "spam"}, format="json")
        self.assertEqual(Notification.objects.filter(recipient=self.bob).count(), 0)


class BlockedListSearchAndPagingTests(APITestCase):
    def setUp(self):
        self.alice = _user("bl_alice")
        self.others = [_user(f"bl_user{i}") for i in range(5)]
        for u in self.others:
            BlockUser.objects.create(blocker=self.alice, blocked=u)
        self.client.force_authenticate(self.alice)
        self.url = reverse("blocked-users")

    def test_without_limit_returns_everything_like_before(self):
        d = self.client.get(self.url).json()
        self.assertEqual(len(d["data"]), 5)
        self.assertFalse(d["has_more"])

    def test_search(self):
        d = self.client.get(self.url, {"q": "user3"}).json()
        self.assertEqual([x["blocked_detail"]["username"] for x in d["data"]], ["bl_user3"])

    def test_paging(self):
        first = self.client.get(self.url, {"limit": 2}).json()
        self.assertEqual(len(first["data"]), 2)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 2)
        last = self.client.get(self.url, {"limit": 2, "offset": 4}).json()
        self.assertEqual(len(last["data"]), 1)
        self.assertFalse(last["has_more"])
        self.assertIsNone(last["next_offset"])


class BlockNewAccountsTests(APITestCase):
    def setUp(self):
        self.alice, self.bob = _user("na_alice"), _user("na_bob")

    def test_flag_is_saved_and_can_be_turned_on_later(self):
        self.client.force_authenticate(self.alice)
        self.client.post(reverse("blocked-users"), {"blocked": self.bob.id}, format="json")
        self.assertFalse(BlockUser.objects.get(blocker=self.alice, blocked=self.bob).block_new_accounts)
        self.client.post(reverse("blocked-users"), {"blocked": self.bob.id, "block_new_accounts": True}, format="json")
        self.assertTrue(BlockUser.objects.get(blocker=self.alice, blocked=self.bob).block_new_accounts)

    def test_new_account_is_blocked_chain_safe(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob, block_new_accounts=True)
        bob2 = _user("na_bob2")
        self.assertEqual(block_new_accounts_of(self.bob, bob2), 1)
        self.assertTrue(BlockUser.objects.filter(blocker=self.alice, blocked=bob2, block_new_accounts=True).exists())
        bob3 = _user("na_bob3")  # third account is caught through the chain
        self.assertEqual(block_new_accounts_of(bob2, bob3), 1)

    def test_not_applied_without_flag_or_for_old_accounts(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)  # flag off
        self.assertEqual(block_new_accounts_of(self.bob, _user("na_x")), 0)
        BlockUser.objects.filter(blocker=self.alice).update(block_new_accounts=True)
        old = _user("na_old")
        User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=90))
        old.refresh_from_db()
        self.assertEqual(block_new_accounts_of(self.bob, old), 0)

    def test_moving_push_token_triggers_it(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob, block_new_accounts=True)
        DeviceToken.objects.create(user=self.bob, token="fcm-token-1")
        bob2 = _user("na_bob2b")
        self.client.force_authenticate(bob2)
        r = self.client.post(reverse("device-token"), {"token": "fcm-token-1"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(BlockUser.objects.filter(blocker=self.alice, blocked=bob2).exists())


class MuteAndRestrictTests(APITestCase):
    def setUp(self):
        self.alice, self.bob = _user("mu_alice"), _user("mu_bob")
        self.client.force_authenticate(self.alice)
        self.profile_url = reverse("user-profile-detail", args=[self.bob.username])

    def test_mute_with_integer_id_then_unmute(self):
        r = self.client.post(reverse("post-muted-accounts"), {"user_id": self.bob.id}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(self.client.get(self.profile_url).data["is_muted_by_me"])
        d = self.client.delete(reverse("post-unmute-account", kwargs={"user_id": self.bob.id}))
        self.assertEqual(d.status_code, 200)
        self.assertFalse(self.client.get(self.profile_url).data["is_muted_by_me"])

    def test_muted_accounts_stories_leave_the_tray_but_follow_stays(self):
        Follow.objects.create(follower=self.alice, following=self.bob, status=Follow.Status.ACCEPTED)
        Story.objects.create(user=self.bob, media="stories/x.jpg", expires_at=timezone.now() + timedelta(hours=2))

        def _tray_owner_ids():
            rows = _results(self.client.get(reverse("story-list")))
            return {str(r.get("user", {}).get("id", r.get("user_id", ""))) for r in rows} if rows else set()

        MutedAccount.objects.create(user=self.alice, muted_user=self.bob)
        tray = self.client.get(reverse("story-list"))
        self.assertEqual(tray.status_code, 200)
        self.assertNotIn(str(self.bob.id), _tray_owner_ids())
        self.assertTrue(Follow.objects.filter(follower=self.alice, following=self.bob).exists())

    def test_restrict_flag_on_profile(self):
        self.assertFalse(self.client.get(self.profile_url).data["am_i_restricting"])
        self.client.post(reverse("restricted-users"), {"restricted": self.bob.id}, format="json")
        self.assertTrue(RestrictUser.objects.filter(user=self.alice, restricted=self.bob).exists())
        self.assertTrue(self.client.get(self.profile_url).data["am_i_restricting"])
        self.client.delete(reverse("unrestrict-user", args=[self.bob.id]))
        self.assertFalse(self.client.get(self.profile_url).data["am_i_restricting"])


# ======================================================================
# Safety & polish: throttles, feed-snapshot freshness, admin abuse
# signals, and one end-to-end lifecycle across every layer
# ======================================================================
from django.contrib.admin.sites import AdminSite
from django.core.cache import cache
from django.test import RequestFactory

from login.admin import UserAdmin
from post.services import exclude_hidden_and_muted


class BlockThrottleTests(APITestCase):
    """20/min (shared by block + unblock), reports 10/min, list GET never throttled."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.alice, self.bob = _user("th_alice"), _user("th_bob")
        self.client.force_authenticate(self.alice)

    def test_block_is_rate_limited_and_unblock_shares_the_bucket(self):
        url = reverse("blocked-users")
        for _ in range(20):
            self.assertIn(self.client.post(url, {"blocked": self.bob.id}, format="json").status_code, (200, 201))
        self.assertEqual(self.client.post(url, {"blocked": self.bob.id}, format="json").status_code, 429)
        # Alternating block/unblock can't dodge it: unblock draws from the same quota.
        self.assertEqual(self.client.delete(reverse("unblock-user", args=[self.bob.id])).status_code, 429)

    def test_listing_blocked_accounts_is_never_throttled_by_block_limits(self):
        url = reverse("blocked-users")
        for _ in range(20):
            self.client.post(url, {"blocked": self.bob.id}, format="json")
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_report_burst_limit(self):
        carol = _user("th_carol")
        url = reverse("content-report")
        body = {"target_type": "user", "target_id": str(carol.id), "reason": "spam"}
        codes = [self.client.post(url, body, format="json").status_code for _ in range(11)]
        self.assertEqual(codes[:10].count(429), 0)
        self.assertEqual(codes[10], 429)


class BlockFeedFreshnessTests(APITestCase):
    """A block must hit the NEXT feed page — not after the frozen snapshot expires."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.alice, self.bob, self.carol = _user("f_alice"), _user("f_bob"), _user("f_carol")
        self.bob_post = _post(self.bob)
        self.carol_post = _post(self.carol)

    def test_exclude_hidden_and_muted_drops_blocked_authors_both_ways(self):
        ids = lambda user: set(exclude_hidden_and_muted(Post.objects.all(), user).values_list("id", flat=True))
        self.assertIn(self.bob_post.id, ids(self.alice))
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        self.assertNotIn(self.bob_post.id, ids(self.alice))   # I blocked them
        self.assertIn(self.carol_post.id, ids(self.alice))
        alice_post = _post(self.alice)
        self.assertNotIn(alice_post.id, ids(self.bob))        # they blocked me

    def test_reposts_of_a_blocked_authors_post_are_dropped(self):
        repost = _post(self.carol, post_type="repost", original_post=self.bob_post, content="")
        ids = lambda: set(exclude_hidden_and_muted(Post.objects.all(), self.alice).values_list("id", flat=True))
        self.assertIn(repost.id, ids())
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        self.assertNotIn(repost.id, ids())

    def test_blocked_posts_leave_a_frozen_snapshot_page(self):
        Follow.objects.create(follower=self.alice, following=self.bob, status=Follow.Status.ACCEPTED)
        Follow.objects.create(follower=self.alice, following=self.carol, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.alice)
        first = self.client.get(reverse("home-feed"), {"page_size": 1})
        self.assertEqual(first.status_code, 200)
        nxt = first.json().get("next")
        # Block AFTER page 1 froze the ranking, then ask for the next page.
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        if nxt:
            page2 = self.client.get(nxt)
            self.assertEqual(page2.status_code, 200)
            authors = {str(p.get("user", {}).get("id")) for p in page2.json()["results"]}
            self.assertNotIn(str(self.bob.id), authors)
        fresh = self.client.get(reverse("home-feed"))
        authors = {str(p.get("user", {}).get("id")) for p in fresh.json()["results"]}
        self.assertNotIn(str(self.bob.id), authors)


class BlockAdminSignalsTests(APITestCase):
    def test_user_admin_counts_blocks_and_reports(self):
        target = _user("ad_target")
        blockers = [_user(f"ad_b{i}") for i in range(3)]
        for b in blockers:
            BlockUser.objects.create(blocker=b, blocked=target)
        ContentReport.objects.create(reporter=blockers[0], reported_user=target, target_type="user",
                                     target_id=str(target.id), reason="spam")
        ContentReport.objects.create(reporter=blockers[1], reported_user=target, target_type="user",
                                     target_id=str(target.id), reason="scam")

        model_admin = UserAdmin(User, AdminSite())
        request = RequestFactory().get("/admin/login/user/")
        request.user = _user("ad_staff", is_staff=True, is_superuser=True)
        row = model_admin.get_queryset(request).get(pk=target.pk)
        self.assertEqual(model_admin.blocked_by_count(row), 3)   # distinct, not multiplied by reports
        self.assertEqual(model_admin.reports_count(row), 2)
        clean = model_admin.get_queryset(request).get(pk=blockers[2].pk)
        self.assertEqual((model_admin.blocked_by_count(clean), model_admin.reports_count(clean)), (0, 0))

    def test_blocked_by_filter_threshold(self):
        from login.admin import BlockedByManyFilter

        popular, quiet = _user("ad_pop"), _user("ad_quiet")
        for i in range(5):
            BlockUser.objects.create(blocker=_user(f"ad_f{i}"), blocked=popular)
        BlockUser.objects.create(blocker=_user("ad_one"), blocked=quiet)

        model_admin = UserAdmin(User, AdminSite())
        request = RequestFactory().get("/admin/login/user/", {"blocked_by": "5"})
        request.user = _user("ad_staff2", is_staff=True, is_superuser=True)
        flt = BlockedByManyFilter(request, {"blocked_by": ["5"]}, User, model_admin)
        names = set(flt.queryset(request, model_admin.get_queryset(request)).values_list("username", flat=True))
        self.assertIn("ad_pop", names)
        self.assertNotIn("ad_quiet", names)


class BlockLifecycleIntegrationTests(APITestCase):
    """Block -> everything closes in every layer -> unblock -> everything is back."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.alice, self.bob = _user("life_alice"), _user("life_bob")
        self.bob_post = _post(self.bob)
        self.alice_post = _post(self.alice)
        PostComment.objects.create(post=self.alice_post, user=self.bob, content="bob says hi")
        Follow.objects.create(follower=self.alice, following=self.bob, status=Follow.Status.ACCEPTED)
        create_notification(self.alice, "post_liked", "bob liked your post", actor=self.bob)

    def _snapshot(self):
        self.client.force_authenticate(self.alice)
        profile = self.client.get(reverse("user-profile-detail", args=[self.bob.username]))
        grid = self.client.get(reverse("post-list"), {"target_user_id": str(self.bob.id)})
        detail = self.client.get(reverse("post-detail", kwargs={"id": self.bob_post.id}))
        comments = self.client.get(reverse("comment-list", kwargs={"post_id": self.alice_post.id}))
        return {
            "profile_blocked_flag": profile.data.get("is_blocked_by_me"),
            "grid_count": len(_results(grid)),
            "detail_status": detail.status_code,
            "comment_texts": {c["content"] for c in comments.json()},
            "bell": Notification.objects.for_user(self.alice).count(),
            "following": Follow.objects.filter(follower=self.alice, following=self.bob).exists(),
            "chat_block_row": BlockedUser.objects.filter(blocker=self.alice, blocked=self.bob).exists(),
            "bob_sees_alice": self.client_for(self.bob).get(
                reverse("user-profile-detail", args=[self.alice.username])).status_code,
        }

    def client_for(self, user):
        from rest_framework.test import APIClient

        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_full_cycle(self):
        before = self._snapshot()
        self.assertEqual(before["profile_blocked_flag"], False)
        self.assertEqual(before["grid_count"], 1)
        self.assertEqual(before["detail_status"], 200)
        self.assertEqual(before["comment_texts"], {"bob says hi"})
        self.assertEqual(before["bell"], 1)
        self.assertTrue(before["following"])
        self.assertFalse(before["chat_block_row"])
        self.assertEqual(before["bob_sees_alice"], 200)

        self.client.force_authenticate(self.alice)
        self.assertEqual(self.client.post(reverse("blocked-users"), {"blocked": self.bob.id}, format="json").status_code, 201)

        during = self._snapshot()
        self.assertEqual(during["profile_blocked_flag"], True)       # blocker gets the minimal card
        self.assertEqual(during["grid_count"], 0)                    # posts closed
        self.assertEqual(during["detail_status"], 404)
        self.assertEqual(during["comment_texts"], set())             # his comment hidden from me
        self.assertEqual(during["bell"], 0)                          # his notification hidden
        self.assertFalse(during["following"])                        # follow removed
        self.assertTrue(during["chat_block_row"])                    # chat layer sees the block
        self.assertEqual(during["bob_sees_alice"], 404)              # he can't find me

        self.client.force_authenticate(self.alice)
        self.assertEqual(self.client.delete(reverse("unblock-user", args=[self.bob.id])).status_code, 200)

        after = self._snapshot()
        self.assertEqual(after["profile_blocked_flag"], False)
        self.assertEqual(after["grid_count"], 1)
        self.assertEqual(after["detail_status"], 200)
        self.assertEqual(after["comment_texts"], {"bob says hi"})    # hidden, never deleted
        self.assertEqual(after["bell"], 1)                           # comes back
        self.assertFalse(after["following"])                         # follows do NOT auto-return
        self.assertFalse(after["chat_block_row"])
        self.assertEqual(after["bob_sees_alice"], 200)
