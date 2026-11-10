"""Run:  python manage.py test core.tests_notification_inbox

N4 - notification INBOX (core/notification_inbox.py + NotificationViewSet.list / bulk):
  1. InboxFilterTests  - ?q= ?unread= ?archived= ?category=money|classes, archived rows stay reachable by id.
  2. CursorTests       - ?before= keyset paging: no duplicate / no gap (ties, deletes between pages), 400 on junk.
  3. BulkTests         - POST notifications/bulk/ read | unread | delete | archive | unarchive (+ isolation).
  4. GroupTests        - ?group=1 read-time collapse ("X and 2 others liked your post").
  5. HelperTests       - cursor round-trip, group_key_for.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import notification_inbox as inbox
from .models import Notification

User = get_user_model()
NT = Notification.NotifType

LIST_URL = "/core/notifications/"
BULK_URL = "/core/notifications/bulk/"
CLEAR_URL = "/core/notifications/clear-read/"
UNREAD_URL = "/core/notifications/unread-count/"

_BASE = timezone.now().replace(microsecond=0)


def _user(name):
    return User.objects.create_user(username=name, password="pw12345!")


def _make(user, *, ntype=NT.GENERIC, title="t", message="", data=None, read=False, minutes_ago=0, archived=False):
    """created_at is auto_now_add -> back-dated with update() so the order is exactly what the test wants."""
    n = Notification.objects.create(
        recipient=user, notif_type=ntype, title=title, message=message, data=data or {}, is_read=read
    )
    fields = {"created_at": _BASE - timedelta(minutes=minutes_ago)}
    if archived:
        fields["archived_at"] = timezone.now()
    Notification.objects.filter(pk=n.pk).update(**fields)
    n.refresh_from_db()
    return n


class _ApiCase(TestCase):
    def setUp(self):
        self.me = _user("inbox_me")
        self.other = _user("inbox_other")
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def ids(self, res):
        return [r["id"] for r in res.data["results"]]


# =====================================================================
# 1. FILTERS
# =====================================================================
class InboxFilterTests(_ApiCase):
    def test_search_matches_title_message_and_actor_username_case_insensitive(self):
        a = _make(self.me, title="Physics test posted", minutes_ago=3)
        b = _make(self.me, title="Hello", message="Your PHYSICS result is out", minutes_ago=2)
        c = _make(self.me, title="Follow", data={"actor_username": "PhysicsGuru"}, minutes_ago=1)
        _make(self.me, title="Chemistry", minutes_ago=0)

        res = self.client.get(LIST_URL, {"q": "physics"})

        self.assertEqual(res.status_code, 200)
        self.assertEqual(sorted(self.ids(res)), sorted([a.id, b.id, c.id]))

    def test_search_never_returns_other_users_rows(self):
        _make(self.other, title="secret physics")
        mine = _make(self.me, title="physics mine")
        res = self.client.get(LIST_URL, {"q": "physics"})
        self.assertEqual(self.ids(res), [mine.id])

    def test_search_no_match_and_overlong_query_do_not_crash(self):
        _make(self.me, title="hello")
        self.assertEqual(self.client.get(LIST_URL, {"q": "zzzz"}).data["results"], [])
        self.assertEqual(self.client.get(LIST_URL, {"q": "x" * 500}).status_code, 200)
        self.assertEqual(len(self.client.get(LIST_URL, {"q": "   "}).data["results"]), 1)  # blank = no filter

    def test_unread_flag(self):
        unread = _make(self.me, read=False, minutes_ago=1)
        _make(self.me, read=True, minutes_ago=0)
        res = self.client.get(LIST_URL, {"unread": "1"})
        self.assertEqual(self.ids(res), [unread.id])

    def test_archived_rows_hidden_by_default_and_listed_with_flag(self):
        shown = _make(self.me, minutes_ago=1)
        hidden = _make(self.me, minutes_ago=0, archived=True, read=True)
        self.assertEqual(self.ids(self.client.get(LIST_URL)), [shown.id])
        self.assertEqual(self.ids(self.client.get(LIST_URL, {"archived": "1"})), [hidden.id])

    def test_archived_row_can_still_be_retrieved_and_deleted_by_id(self):
        row = _make(self.me, archived=True, read=True)
        self.assertEqual(self.client.get(f"{LIST_URL}{row.id}/").status_code, 200)
        self.assertEqual(self.client.delete(f"{LIST_URL}{row.id}/").status_code, 204)

    def test_money_category_is_exactly_the_payments_group(self):
        money = _make(self.me, ntype=NT.WITHDRAWAL_PAID, minutes_ago=2)
        money2 = _make(self.me, ntype=NT.FEE_DUE_REMINDER, minutes_ago=1)
        _make(self.me, ntype=NT.POST_LIKED, minutes_ago=0)
        res = self.client.get(LIST_URL, {"category": "money"})
        self.assertEqual(sorted(self.ids(res)), sorted([money.id, money2.id]))
        self.assertEqual(Notification.money_types(), frozenset(Notification.preference_groups()["payments"]))

    def test_money_category_works_for_unread_count_too(self):
        _make(self.me, ntype=NT.WITHDRAWAL_PAID, read=False)
        _make(self.me, ntype=NT.POST_LIKED, read=False)
        res = self.client.get(UNREAD_URL, {"category": "money"})
        self.assertEqual(res.data["unread_count"], 1)

    def test_classes_is_an_alias_of_classroom(self):
        _make(self.me, ntype=NT.SESSION_LIVE, minutes_ago=1)
        _make(self.me, ntype=NT.POST_LIKED, minutes_ago=0)
        a = self.client.get(LIST_URL, {"category": "classes"})
        b = self.client.get(LIST_URL, {"category": "classroom"})
        self.assertEqual(self.ids(a), self.ids(b))
        self.assertEqual(len(self.ids(a)), 1)

    def test_filters_stack(self):
        hit = _make(self.me, ntype=NT.WITHDRAWAL_PAID, title="payout done", read=False, minutes_ago=0)
        _make(self.me, ntype=NT.WITHDRAWAL_PAID, title="payout done", read=True, minutes_ago=1)
        _make(self.me, ntype=NT.WITHDRAWAL_PAID, title="other", read=False, minutes_ago=2)
        res = self.client.get(LIST_URL, {"category": "money", "unread": "1", "q": "payout"})
        self.assertEqual(self.ids(res), [hit.id])

    def test_legacy_list_shape_is_unchanged(self):
        _make(self.me)
        res = self.client.get(LIST_URL)
        self.assertEqual(res.data["count"], 1)
        self.assertIn("unread_count", res.data)
        self.assertNotIn("has_more", res.data)
        self.assertNotIn("group_key", res.data["results"][0])

    def test_clear_read_keeps_archived_rows(self):
        archived = _make(self.me, read=True, archived=True)
        gone = _make(self.me, read=True)
        res = self.client.delete(CLEAR_URL)
        self.assertEqual(res.data["deleted"], 1)
        self.assertTrue(Notification.objects.filter(pk=archived.pk).exists())
        self.assertFalse(Notification.objects.filter(pk=gone.pk).exists())


# =====================================================================
# 2. KEYSET CURSOR
# =====================================================================
class CursorTests(_ApiCase):
    def _walk(self, limit, extra=None, after_first=None):
        """Follow next_before until the end -> list of ids in the order served."""
        seen, token, first = [], "", True
        for _ in range(50):
            params = {"before": token, "limit": limit, **(extra or {})}
            res = self.client.get(LIST_URL, params)
            self.assertEqual(res.status_code, 200)
            seen.extend(self.ids(res))
            if first and after_first:
                after_first()
            first = False
            if not res.data["has_more"]:
                self.assertIsNone(res.data["next_before"])
                return seen
            token = res.data["next_before"]
            self.assertTrue(token)
        self.fail("cursor never ended")

    def test_pages_cover_everything_once_newest_first(self):
        rows = [_make(self.me, minutes_ago=i) for i in range(25)]
        expected = [r.id for r in rows]  # minutes_ago ascending == newest first
        self.assertEqual(self._walk(10), expected)

    def test_first_page_shape(self):
        for i in range(3):
            _make(self.me, minutes_ago=i)
        res = self.client.get(LIST_URL, {"before": "", "limit": 2})
        self.assertIsNone(res.data["count"])  # no COUNT(*)
        self.assertTrue(res.data["has_more"])
        self.assertEqual(len(res.data["results"]), 2)
        self.assertEqual(res.data["unread_count"], 3)

    def test_exact_multiple_of_limit_has_no_phantom_page(self):
        for i in range(10):
            _make(self.me, minutes_ago=i)
        res = self.client.get(LIST_URL, {"before": "", "limit": 10})
        self.assertFalse(res.data["has_more"])
        self.assertIsNone(res.data["next_before"])

    def test_identical_created_at_has_no_dup_or_gap(self):
        rows = [_make(self.me, minutes_ago=0) for _ in range(9)]
        served = self._walk(4)
        self.assertEqual(len(served), 9)
        self.assertEqual(set(served), {r.id for r in rows})

    def test_delete_between_pages_does_not_duplicate_or_skip(self):
        rows = [_make(self.me, minutes_ago=i) for i in range(12)]
        ids = [r.id for r in rows]

        def delete_one_seen_and_one_unseen():
            Notification.objects.filter(pk__in=[ids[2], ids[8]]).delete()

        served = self._walk(5, after_first=delete_one_seen_and_one_unseen)
        self.assertEqual(len(served), len(set(served)))  # no duplicates
        self.assertEqual(set(served), set(ids) - {ids[8]})  # unseen row 8 gone, seen row 2 was already served

    def test_cursor_stacks_with_filters(self):
        unread = [_make(self.me, read=False, minutes_ago=i * 2) for i in range(6)]
        for i in range(6):
            _make(self.me, read=True, minutes_ago=i * 2 + 1)
        served = self._walk(4, extra={"unread": "1"})
        self.assertEqual(served, [r.id for r in unread])

    def test_plain_notification_id_is_accepted_as_cursor(self):
        rows = [_make(self.me, minutes_ago=i) for i in range(5)]
        res = self.client.get(LIST_URL, {"before": str(rows[1].id), "limit": 10})
        self.assertEqual(self.ids(res), [r.id for r in rows[2:]])

    def test_junk_cursor_is_400_not_a_silent_restart(self):
        _make(self.me)
        res = self.client.get(LIST_URL, {"before": "not-a-cursor!!"})
        self.assertEqual(res.status_code, 400)

    def test_other_users_rows_never_leak_through_cursor(self):
        mine = _make(self.me, minutes_ago=5)
        theirs = _make(self.other, minutes_ago=0)
        res = self.client.get(LIST_URL, {"before": str(theirs.id)})  # someone else's id as cursor
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(theirs.id, self.ids(res))
        self.assertIn(mine.id, self._walk(10))

    def test_limit_is_clamped(self):
        for i in range(3):
            _make(self.me, minutes_ago=i)
        self.assertEqual(len(self.client.get(LIST_URL, {"before": "", "limit": 0}).data["results"]), 1)
        self.assertEqual(len(self.client.get(LIST_URL, {"before": "", "limit": "abc"}).data["results"]), 3)
        self.assertEqual(len(self.client.get(LIST_URL, {"before": "", "limit": 100000}).data["results"]), 3)

    def test_requires_auth(self):
        self.assertIn(APIClient().get(LIST_URL, {"before": ""}).status_code, (401, 403))


# =====================================================================
# 3. BULK
# =====================================================================
class BulkTests(_ApiCase):
    def post(self, ids, action):
        return self.client.post(BULK_URL, {"ids": ids, "action": action}, format="json")

    def test_read_and_unread(self):
        a = _make(self.me, read=False)
        b = _make(self.me, read=True)
        res = self.post([a.id, b.id], "read")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["affected"], 1)  # b was already read
        a.refresh_from_db()
        self.assertTrue(a.is_read)
        self.assertIsNotNone(a.read_at)

        res = self.post([a.id, b.id], "unread")
        self.assertEqual(res.data["affected"], 2)
        a.refresh_from_db()
        self.assertFalse(a.is_read)
        self.assertIsNone(a.read_at)
        self.assertEqual(res.data["unread_count"], 2)

    def test_delete(self):
        a = _make(self.me)
        b = _make(self.me)
        keep = _make(self.me)
        res = self.post([a.id, b.id], "delete")
        self.assertEqual(res.data["affected"], 2)
        self.assertEqual(list(Notification.objects.filter(recipient=self.me).values_list("pk", flat=True)), [keep.id])

    def test_archive_marks_read_hides_and_unarchive_restores(self):
        a = _make(self.me, read=False)
        res = self.post([a.id], "archive")
        self.assertEqual(res.data["affected"], 1)
        a.refresh_from_db()
        self.assertTrue(a.is_read)
        self.assertIsNotNone(a.archived_at)
        self.assertEqual(res.data["unread_count"], 0)  # archived rows never count as unread
        self.assertEqual(self.ids(self.client.get(LIST_URL)), [])
        self.assertEqual(self.ids(self.client.get(LIST_URL, {"archived": "1"})), [a.id])

        res = self.post([a.id], "unarchive")
        self.assertEqual(res.data["affected"], 1)
        self.assertEqual(self.ids(self.client.get(LIST_URL)), [a.id])

    def test_archive_twice_is_idempotent(self):
        a = _make(self.me)
        self.assertEqual(self.post([a.id], "archive").data["affected"], 1)
        self.assertEqual(self.post([a.id], "archive").data["affected"], 0)

    def test_other_users_ids_are_skipped_for_every_action(self):
        theirs = _make(self.other, read=False)
        for action in ("read", "unread", "delete", "archive", "unarchive"):
            with self.subTest(action=action):
                res = self.post([theirs.id], action)
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.data["affected"], 0)
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read)
        self.assertIsNone(theirs.archived_at)

    def test_validation(self):
        self.assertEqual(self.post([1], "explode").status_code, 400)
        self.assertEqual(self.post([1], "").status_code, 400)
        self.assertEqual(self.client.post(BULK_URL, {"action": "read"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(BULK_URL, {"ids": "1,2", "action": "read"}, format="json").status_code, 400)
        too_many = list(range(1, inbox_max() + 2))
        res = self.post(too_many, "read")
        self.assertEqual(res.status_code, 400)

    def test_junk_ids_are_skipped_and_empty_is_a_noop(self):
        a = _make(self.me)
        res = self.post([True, None, "x", {"a": 1}, str(a.id)], "read")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["affected"], 1)
        self.assertEqual(self.post([], "delete").data["affected"], 0)

    def test_requires_auth(self):
        res = APIClient().post(BULK_URL, {"ids": [1], "action": "read"}, format="json")
        self.assertIn(res.status_code, (401, 403))


def inbox_max():
    from .views import NotificationViewSet

    return NotificationViewSet.MARK_READ_BATCH_MAX


# =====================================================================
# 4. GROUPING
# =====================================================================
class GroupTests(_ApiCase):
    def setUp(self):
        super().setUp()
        self.actors = [_user(f"liker{i}") for i in range(4)]

    def _like(self, actor, *, post="7", minutes_ago=0, read=False, ntype=NT.POST_LIKED):
        return _make(
            self.me, ntype=ntype, title=f"{actor.username} liked your post", read=read, minutes_ago=minutes_ago,
            data={"post_id": post, "actor_ids": [actor.id], "actor_id": actor.id, "actor_username": actor.username},
        )

    def test_same_post_same_day_collapses_into_one_card(self):
        rows = [self._like(a, minutes_ago=i) for i, a in enumerate(self.actors[:3])]  # actors[0] is the newest
        res = self.client.get(LIST_URL, {"group": "1"})

        self.assertEqual(len(res.data["results"]), 1)
        card = res.data["results"][0]
        self.assertEqual(card["id"], rows[0].id)  # representative = newest
        self.assertEqual(card["group_count"], 3)
        self.assertEqual(card["group_ids"], [r.id for r in rows])
        self.assertTrue(card["group_key"].startswith("post_liked:7:"))
        self.assertEqual(card["actor_count"], 3)
        self.assertEqual(len(card["actors"]), 3)
        self.assertEqual(card["title"], f"{self.actors[0].username} and 2 others liked your post")
        self.assertFalse(card["is_read"])

    def test_group_is_read_only_when_every_member_is_read(self):
        self._like(self.actors[0], minutes_ago=0, read=True)
        self._like(self.actors[1], minutes_ago=1, read=False)
        card = self.client.get(LIST_URL, {"group": "1"}).data["results"][0]
        self.assertFalse(card["is_read"])
        self.assertIsNone(card["read_at"])

        Notification.objects.filter(recipient=self.me).update(is_read=True)
        card = self.client.get(LIST_URL, {"group": "1"}).data["results"][0]
        self.assertTrue(card["is_read"])

    def test_different_posts_types_and_days_do_not_merge(self):
        self._like(self.actors[0], post="7", minutes_ago=0)
        self._like(self.actors[1], post="8", minutes_ago=1)
        self._like(self.actors[2], post="7", minutes_ago=2, ntype=NT.POST_COMMENTED)
        self._like(self.actors[3], post="7", minutes_ago=60 * 24 * 3)  # three days earlier
        res = self.client.get(LIST_URL, {"group": "1"})
        self.assertEqual(len(res.data["results"]), 4)
        for card in res.data["results"]:
            self.assertIsNone(card["group_key"])
            self.assertEqual(card["group_count"], 1)
            self.assertEqual(card["group_ids"], [card["id"]])

    def test_unrelated_row_keeps_its_place_between_groups(self):
        newest = self._like(self.actors[0], minutes_ago=0)
        mid = _make(self.me, title="class starts", minutes_ago=1)
        self._like(self.actors[1], minutes_ago=2)
        res = self.client.get(LIST_URL, {"group": "1"})
        self.assertEqual(self.ids(res), [newest.id, mid.id])  # group sits at its NEWEST member's position
        self.assertEqual(res.data["results"][0]["group_count"], 2)

    def test_new_follower_batch_rows_group_but_a_real_accept_does_not(self):
        def follow(actor, minutes_ago, kind):
            data = {"actor_ids": [actor.id], "actor_id": actor.id, "actor_username": actor.username}
            if kind:
                data["kind"] = kind
            return _make(self.me, ntype=NT.FOLLOW_REQUEST_ACCEPTED, title="f", data=data, minutes_ago=minutes_ago)

        follow(self.actors[0], 0, "new_follower")
        follow(self.actors[1], 1, "new_follower")
        accepted = follow(self.actors[2], 2, None)
        res = self.client.get(LIST_URL, {"group": "1"})
        self.assertEqual(len(res.data["results"]), 2)
        card = res.data["results"][0]
        self.assertEqual(card["group_count"], 2)
        self.assertIn("started following you", card["title"])
        self.assertIsNone(card["is_following_actor"])  # nothing to "follow back" on a merged card
        self.assertEqual(res.data["results"][1]["id"], accepted.id)
        self.assertIsNone(res.data["results"][1]["group_key"])

    def test_group_works_with_cursor_and_other_users_rows_never_merge_in(self):
        for i, a in enumerate(self.actors):
            self._like(a, minutes_ago=i)
        _make(self.other, ntype=NT.POST_LIKED, data={"post_id": "7", "actor_ids": [self.actors[0].id]})
        res = self.client.get(LIST_URL, {"group": "1", "before": "", "limit": 10})
        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(res.data["results"][0]["group_count"], 4)
        self.assertFalse(res.data["has_more"])

    def test_group_split_over_a_page_boundary_shares_one_group_key(self):
        for i, a in enumerate(self.actors):
            self._like(a, minutes_ago=i)
        page1 = self.client.get(LIST_URL, {"group": "1", "before": "", "limit": 2})
        page2 = self.client.get(LIST_URL, {"group": "1", "before": page1.data["next_before"], "limit": 2})
        self.assertEqual(page1.data["results"][0]["group_key"], page2.data["results"][0]["group_key"])
        self.assertEqual(page1.data["results"][0]["group_count"] + page2.data["results"][0]["group_count"], 4)

    def test_group_without_cursor_uses_offset_pagination(self):
        for i, a in enumerate(self.actors[:2]):
            self._like(a, minutes_ago=i)
        res = self.client.get(LIST_URL, {"group": "1"})
        self.assertEqual(res.data["count"], 2)  # count is rows (legacy meaning), results are cards
        self.assertEqual(len(res.data["results"]), 1)


# =====================================================================
# 5. HELPERS
# =====================================================================
class HelperTests(SimpleTestCase):
    def test_cursor_round_trip(self):
        now = timezone.now()
        self.assertEqual(inbox.decode_cursor(inbox.encode_cursor(now, 42)), (now, 42))

    def test_decode_rejects_junk(self):
        for token in ("zzz", "!!!!", inbox.encode_cursor(timezone.now(), 1)[:-3] + "~~~"):
            with self.subTest(token=token):
                with self.assertRaises(ValueError):
                    inbox.decode_cursor(token)

    def test_group_key_for(self):
        now = timezone.now()
        like = Notification(notif_type=NT.POST_LIKED, data={"post_id": 9}, created_at=now)
        self.assertTrue(inbox.group_key_for(like).startswith("post_liked:9:"))
        self.assertIsNone(inbox.group_key_for(Notification(notif_type=NT.POST_LIKED, data={}, created_at=now)))
        self.assertIsNone(inbox.group_key_for(Notification(notif_type=NT.GENERIC, data={"post_id": 9}, created_at=now)))
        accepted = Notification(notif_type=NT.FOLLOW_REQUEST_ACCEPTED, data={}, created_at=now)
        self.assertIsNone(inbox.group_key_for(accepted))
        follower = Notification(notif_type=NT.FOLLOW_REQUEST_ACCEPTED, data={"kind": "new_follower"}, created_at=now)
        self.assertTrue(inbox.group_key_for(follower).startswith("follow_request_accepted:self:"))

    def test_every_group_tail_type_exists_in_the_enum(self):
        values = set(NT.values)
        self.assertTrue(set(inbox.GROUP_TAILS) <= values)
