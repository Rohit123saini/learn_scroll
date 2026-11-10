"""
user_profile/tests_relationships.py  (P8 - relationship management)

Likhe gaye hain, CHALAYE NAHI gaye.  Run:
    python manage.py test user_profile.tests_relationships

Covers
  * followers / following: ?q= search, ?sort= (latest / oldest / name), ?filter= (mutual / not_following_back),
    paging stays stable, blocked users never listed, unknown sort/filter -> defaults (no 400)
  * bulk follow requests: accept / decline, select_all, other people's requests are skipped, validation, counts
  * relationship summary counts
  * restricted-users: ?q= + limit/offset
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from post.models import CloseFriend, MutedAccount
from user_profile.models import BlockUser, Follow, RestrictUser

User = get_user_model()


def _user(name, **kw):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345", **kw)


def _follow(follower, following, status=Follow.Status.ACCEPTED, days_ago=0):
    row = Follow.objects.create(follower=follower, following=following, status=status)
    if days_ago:  # created_at is auto_now_add -> push it back with update()
        Follow.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    return row


def _rows(resp):
    data = resp.json()
    if isinstance(data, dict) and isinstance(data.get("data"), (dict, list)):
        data = data["data"]
    return data["results"] if isinstance(data, dict) and "results" in data else data


def _names(resp):
    return [r["username"] for r in _rows(resp)]


class FollowListQueryTests(APITestCase):
    def setUp(self):
        self.me = _user("rel_me")
        self.anna = _user("rel_anna", first_name="Zed", last_name="Last")
        self.bob = _user("rel_bob")
        self.cara = _user("rel_cara")
        self.dev = _user("rel_dev")
        # followers of me: anna (oldest), bob, cara (newest)
        _follow(self.anna, self.me, days_ago=30)
        _follow(self.bob, self.me, days_ago=10)
        _follow(self.cara, self.me, days_ago=1)
        # I follow bob + dev; dev does not follow me back
        _follow(self.me, self.bob)
        _follow(self.me, self.dev)
        self.client.force_authenticate(self.me)
        self.followers = reverse("user-followers", args=[self.me.username])
        self.following = reverse("user-following", args=[self.me.username])

    def test_default_is_latest_first(self):
        self.assertEqual(_names(self.client.get(self.followers)), ["rel_cara", "rel_bob", "rel_anna"])

    def test_sort_oldest_and_name(self):
        self.assertEqual(_names(self.client.get(self.followers, {"sort": "oldest"})), ["rel_anna", "rel_bob", "rel_cara"])
        self.assertEqual(_names(self.client.get(self.followers, {"sort": "name"})), ["rel_anna", "rel_bob", "rel_cara"])

    def test_unknown_sort_and_filter_fall_back_instead_of_400(self):
        r = self.client.get(self.followers, {"sort": "banana", "filter": "nonsense"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(_names(r), ["rel_cara", "rel_bob", "rel_anna"])

    def test_search_matches_username_and_name_case_insensitive(self):
        self.assertEqual(_names(self.client.get(self.followers, {"q": "BOB"})), ["rel_bob"])
        self.assertEqual(_names(self.client.get(self.followers, {"q": "zed"})), ["rel_anna"])  # first_name
        self.assertEqual(_names(self.client.get(self.followers, {"q": "nobody-here"})), [])

    def test_mutual_filter_is_both_ways_follow(self):
        self.assertEqual(_names(self.client.get(self.followers, {"filter": "mutual"})), ["rel_bob"])
        # following list: bob is mutual, dev is not
        self.assertEqual(_names(self.client.get(self.following, {"filter": "mutual"})), ["rel_bob"])

    def test_not_following_back_filter(self):
        self.assertEqual(_names(self.client.get(self.following, {"filter": "not_following_back"})), ["rel_dev"])

    def test_filter_is_relative_to_the_viewer_on_someone_elses_list(self):
        # dev's followers = [me]; viewer anna: anna follows nobody -> mutual is empty, but plain list has me
        self.client.force_authenticate(self.anna)
        url = reverse("user-followers", args=[self.dev.username])
        self.assertEqual(_names(self.client.get(url)), ["rel_me"])
        self.assertEqual(_names(self.client.get(url, {"filter": "mutual"})), [])

    def test_pending_and_blocked_never_listed(self):
        pending = _user("rel_pending")
        _follow(pending, self.me, status=Follow.Status.PENDING)
        blocked = _user("rel_blocked")
        _follow(blocked, self.me)
        BlockUser.objects.create(blocker=self.me, blocked=blocked)  # (no signal cleans the Follow in this direct create)
        names = _names(self.client.get(self.followers))
        self.assertNotIn("rel_pending", names)
        self.assertNotIn("rel_blocked", names)

    def test_paging_is_stable_with_each_sort(self):
        for i in range(25):
            _follow(_user(f"rel_p{i:02d}"), self.me, days_ago=0)
        for sort in ("latest", "oldest", "name"):
            seen = []
            page = 1
            while True:
                r = self.client.get(self.followers, {"sort": sort, "page_size": 10, "page": page})
                self.assertEqual(r.status_code, 200)
                seen += _names(r)
                data = r.json()["data"]
                if not data.get("next"):
                    break
                page += 1
            self.assertEqual(len(seen), len(set(seen)), f"duplicate rows with sort={sort}")
            self.assertEqual(len(seen), 28)


class BulkFollowRequestTests(APITestCase):
    def setUp(self):
        self.me = _user("bulk_me", is_private=True)
        self.others = [_user(f"bulk_u{i}") for i in range(5)]
        self.reqs = [_follow(u, self.me, status=Follow.Status.PENDING) for u in self.others]
        self.url = reverse("follow-requests-bulk")
        self.client.force_authenticate(self.me)

    def test_accept_selected(self):
        ids = [self.reqs[0].id, self.reqs[1].id]
        r = self.client.post(self.url, {"action": "accept", "follow_ids": ids}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["processed"], 2)
        self.assertEqual(sorted(r.json()["ids"]), sorted(ids))
        self.assertEqual(r.json()["remaining"], 3)
        self.assertEqual(Follow.objects.filter(following=self.me, status=Follow.Status.ACCEPTED).count(), 2)
        self.me.refresh_from_db()
        self.assertEqual(self.me.followers_count, 2)  # signal recounted

    def test_decline_selected_deletes_rows_silently(self):
        ids = [self.reqs[2].id, self.reqs[3].id]
        r = self.client.post(self.url, {"action": "decline", "follow_ids": ids}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["processed"], 2)
        self.assertFalse(Follow.objects.filter(id__in=ids).exists())
        self.assertEqual(r.json()["remaining"], 3)

    def test_select_all_accept(self):
        r = self.client.post(self.url, {"action": "accept", "select_all": True}, format="json")
        self.assertEqual(r.json()["processed"], 5)
        self.assertEqual(r.json()["remaining"], 0)
        self.assertFalse(Follow.objects.filter(following=self.me, status=Follow.Status.PENDING).exists())

    def test_other_peoples_and_unknown_ids_are_skipped_not_applied(self):
        stranger = _user("bulk_stranger")
        theirs = _follow(self.others[0], stranger, status=Follow.Status.PENDING)
        r = self.client.post(self.url, {"action": "accept", "follow_ids": [theirs.id, 999999, self.reqs[4].id]}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["ids"], [self.reqs[4].id])
        self.assertCountEqual(r.json()["skipped"], [theirs.id, 999999])
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Follow.Status.PENDING)  # untouched

    def test_already_accepted_request_is_skipped(self):
        self.client.post(reverse("accept-request", args=[self.reqs[0].id]))
        r = self.client.post(self.url, {"action": "accept", "follow_ids": [self.reqs[0].id]}, format="json")
        self.assertEqual(r.json()["processed"], 0)
        self.assertEqual(r.json()["skipped"], [self.reqs[0].id])

    def test_validation(self):
        self.assertEqual(self.client.post(self.url, {"action": "accept"}, format="json").status_code, 400)  # no ids
        self.assertEqual(self.client.post(self.url, {"action": "explode", "select_all": True}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url, {"action": "accept", "follow_ids": list(range(1, 102))}, format="json").status_code, 400)

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.post(self.url, {"action": "accept", "select_all": True}, format="json").status_code, (401, 403))

    def test_bulk_caps_at_100_and_reports_remaining(self):
        for i in range(105):
            _follow(_user(f"bulk_many{i}"), self.me, status=Follow.Status.PENDING)
        r = self.client.post(self.url, {"action": "decline", "select_all": True}, format="json")
        self.assertEqual(r.json()["processed"], 100)
        self.assertEqual(r.json()["remaining"], 10)  # 5 + 105 - 100


class RelationshipSummaryTests(APITestCase):
    def test_counts(self):
        me = _user("sum_me")
        a, b, c, d = (_user(f"sum_{n}") for n in "abcd")
        _follow(a, me)                                   # follower
        _follow(me, a)                                   # + I follow back -> mutual
        _follow(me, b)                                   # I follow, they don't follow back
        _follow(c, me, status=Follow.Status.PENDING)     # request
        CloseFriend.objects.create(owner=me, friend=a)
        RestrictUser.objects.create(user=me, restricted=d)
        BlockUser.objects.create(blocker=me, blocked=_user("sum_blk"))
        MutedAccount.objects.create(user=me, muted_user=b)
        self.client.force_authenticate(me)
        data = self.client.get(reverse("relationships-summary")).json()["data"]
        self.assertEqual(data, {
            "followers": 1, "following": 2, "requests": 1, "not_following_back": 1,
            "close_friends": 1, "restricted": 1, "blocked": 1, "muted": 1,
        })

    def test_only_my_own_numbers(self):
        me, other = _user("sum2_me"), _user("sum2_other")
        _follow(_user("sum2_x"), other)
        self.client.force_authenticate(me)
        self.assertEqual(self.client.get(reverse("relationships-summary")).json()["data"]["followers"], 0)


class RestrictedListTests(APITestCase):
    def setUp(self):
        self.me = _user("rst_me")
        self.targets = [_user(f"rst_t{i}", first_name="Pat" if i == 1 else "") for i in range(6)]
        for t in self.targets:
            RestrictUser.objects.create(user=self.me, restricted=t)
        self.client.force_authenticate(self.me)
        self.url = reverse("restricted-users")

    def test_without_limit_returns_everything_like_before(self):
        r = self.client.get(self.url).json()
        self.assertEqual(len(r["data"]), 6)
        self.assertFalse(r["has_more"])

    def test_search(self):
        r = self.client.get(self.url, {"q": "pat"}).json()
        self.assertEqual([x["restricted_detail"]["username"] for x in r["data"]], ["rst_t1"])

    def test_limit_offset(self):
        first = self.client.get(self.url, {"limit": 4}).json()
        self.assertEqual(len(first["data"]), 4)
        self.assertTrue(first["has_more"])
        second = self.client.get(self.url, {"limit": 4, "offset": first["next_offset"]}).json()
        self.assertEqual(len(second["data"]), 2)
        self.assertFalse(second["has_more"])
        ids = [x["id"] for x in first["data"] + second["data"]]
        self.assertEqual(len(ids), len(set(ids)))
