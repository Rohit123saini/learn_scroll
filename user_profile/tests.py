# user_profile/tests.py
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import BlockUser, CoinLedger, CoinWithdrawalRequest, Follow, RestrictUser

User = get_user_model()


class FollowModelTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")

    def test_self_follow_blocked_at_db_level(self):
        with self.assertRaises(IntegrityError):
            Follow.objects.create(follower=self.alice, following=self.alice)


class FollowAPITests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")
        self.client.force_authenticate(user=self.alice)

    def test_cannot_follow_yourself(self):
        url = reverse("follow-user", args=[self.alice.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_follow_then_unfollow_updates_counts(self):
        url = reverse("follow-user", args=[self.bob.id])
        self.client.post(url)
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 1)
        self.assertEqual(self.bob.followers_count, 1)

        self.client.post(url)  # unfollow
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 0)
        self.assertEqual(self.bob.followers_count, 0)

    def test_blocked_user_cannot_be_followed(self):
        BlockUser.objects.create(blocker=self.bob, blocked=self.alice)
        url = reverse("follow-user", args=[self.bob.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_private_profile_hides_bio_from_non_follower(self):
        self.bob.is_private = True
        self.bob.bio = "secret bio"
        self.bob.save()

        url = reverse("user-profile-detail", args=[self.bob.username])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_restricted_view"])
        self.assertNotIn("bio", response.data["data"])

    def test_blocked_user_gets_404_on_profile_lookup(self):
        BlockUser.objects.create(blocker=self.bob, blocked=self.alice)
        url = reverse("user-profile-detail", args=[self.bob.username])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ==========================================================================
# TASK 30 — additional coverage.
#
# Written against the real `user_profile/views.py`, `urls.py`, and
# `serializers.py` (all three were uploaded for this pass) — nothing
# below is a guessed url name or an assumed response shape. Confirmed
# endpoints used:
#   FollowAPIView          POST /follow/<user_id>/            "follow-user"
#   AcceptFollowRequestView POST /accept-request/<follow_id>/ "accept-request"
#   RejectFollowRequestView POST /reject-request/<follow_id>/ "reject-request"
#   BlockedUsersView       GET/POST /blocked-users/           "blocked-users"
#   UnblockUserView        DELETE /blocked-users/<id>/        "unblock-user"
#   RestrictedUsersView    GET/POST /restricted-users/        "restricted-users"
#   UnrestrictUserView     DELETE /restricted-users/<id>/     "unrestrict-user"
#   UserSearchView         GET /search/?search=<query>        "user-search"
# ==========================================================================

class PrivateAccountFollowRequestFlowTests(APITestCase):
    """
    End-to-end: following a PRIVATE account creates a PENDING request (not
    an immediate follow), counts stay untouched until accepted, the
    target's profile stays in "restricted view" for the requester the
    whole time it's pending, and accepting flips everything over —
    status, counts, and the profile view — in one go.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345", is_private=True)
        self.client.force_authenticate(user=self.alice)

    def test_following_private_account_creates_pending_request(self):
        url = reverse("follow-user", args=[self.bob.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], Follow.Status.PENDING)

        follow = Follow.objects.get(follower=self.alice, following=self.bob)
        self.assertEqual(follow.status, Follow.Status.PENDING)

    def test_pending_follow_request_does_not_increment_counts(self):
        url = reverse("follow-user", args=[self.bob.id])
        self.client.post(url)

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 0)
        self.assertEqual(self.bob.followers_count, 0)

    def test_profile_stays_restricted_view_while_pending(self):
        self.client.post(reverse("follow-user", args=[self.bob.id]))

        detail_url = reverse("user-profile-detail", args=[self.bob.username])
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_restricted_view"])
        self.assertEqual(response.data["my_follow_status"], Follow.Status.PENDING)
        self.assertNotIn("bio", response.data["data"])

    def test_accepting_follow_request_updates_status_counts_and_profile_view(self):
        follow = Follow.objects.create(
            follower=self.alice, following=self.bob, status=Follow.Status.PENDING
        )

        self.client.force_authenticate(user=self.bob)
        response = self.client.post(reverse("accept-request", args=[follow.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        follow.refresh_from_db()
        self.assertEqual(follow.status, Follow.Status.ACCEPTED)
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 1)
        self.assertEqual(self.bob.followers_count, 1)

        # Now that alice is an accepted follower, bob's private profile
        # should stop being a restricted view for her.
        self.client.force_authenticate(user=self.alice)
        detail_url = reverse("user-profile-detail", args=[self.bob.username])
        response = self.client.get(detail_url)
        self.assertFalse(response.data["is_restricted_view"])

    def test_only_the_target_can_accept_a_follow_request(self):
        follow = Follow.objects.create(
            follower=self.alice, following=self.bob, status=Follow.Status.PENDING
        )
        # alice (the requester, not the target) tries to accept her own request
        response = self.client.post(reverse("accept-request", args=[follow.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_rejecting_follow_request_deletes_it(self):
        follow = Follow.objects.create(
            follower=self.alice, following=self.bob, status=Follow.Status.PENDING
        )
        self.client.force_authenticate(user=self.bob)
        response = self.client.post(reverse("reject-request", args=[follow.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Follow.objects.filter(id=follow.id).exists())


class BlockUnblockEdgeCaseTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")
        self.client.force_authenticate(user=self.alice)

    def test_cannot_block_yourself_via_api(self):
        response = self.client.post(reverse("blocked-users"), {"blocked": self.alice.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_block_yourself_at_db_level(self):
        with self.assertRaises(IntegrityError):
            BlockUser.objects.create(blocker=self.alice, blocked=self.alice)

    def test_blocking_twice_via_api_is_idempotent(self):
        url = reverse("blocked-users")
        first = self.client.post(url, {"blocked": self.bob.id})
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second = self.client.post(url, {"blocked": self.bob.id})
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["message"], "User already blocked.")
        self.assertEqual(BlockUser.objects.filter(blocker=self.alice, blocked=self.bob).count(), 1)

    def test_blocking_removes_existing_follow_relationship_both_ways(self):
        Follow.objects.create(follower=self.alice, following=self.bob)
        Follow.objects.create(follower=self.bob, following=self.alice)
        self.alice.following_count = 1
        self.alice.followers_count = 1
        self.alice.save(update_fields=["following_count", "followers_count"])
        self.bob.following_count = 1
        self.bob.followers_count = 1
        self.bob.save(update_fields=["following_count", "followers_count"])

        response = self.client.post(reverse("blocked-users"), {"blocked": self.bob.id})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertFalse(Follow.objects.filter(follower=self.alice, following=self.bob).exists())
        self.assertFalse(Follow.objects.filter(follower=self.bob, following=self.alice).exists())

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 0)
        self.assertEqual(self.alice.followers_count, 0)
        self.assertEqual(self.bob.following_count, 0)
        self.assertEqual(self.bob.followers_count, 0)

    def test_unblock_by_block_record_id(self):
        block = BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        response = self.client.delete(reverse("unblock-user", args=[block.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(BlockUser.objects.filter(id=block.id).exists())

    def test_unblock_by_target_user_id(self):
        # UnblockUserView accepts either the BlockUser row's own id, or the
        # blocked USER's id directly — chat screens only know the latter.
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        response = self.client.delete(reverse("unblock-user", args=[self.bob.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(BlockUser.objects.filter(blocker=self.alice, blocked=self.bob).exists())

    def test_unblocking_a_user_who_was_never_blocked_is_a_clean_404(self):
        response = self.client.delete(reverse("unblock-user", args=[self.bob.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_unblock_someone_elses_block(self):
        # bob blocked alice — alice must not be able to delete bob's block
        # record just by knowing/guessing its id.
        block = BlockUser.objects.create(blocker=self.bob, blocked=self.alice)
        response = self.client.delete(reverse("unblock-user", args=[block.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(BlockUser.objects.filter(id=block.id).exists())

    def test_restricting_an_already_blocked_user_is_rejected(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        response = self.client.post(reverse("restricted-users"), {"restricted": self.bob.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(RestrictUser.objects.filter(user=self.alice, restricted=self.bob).exists())


class RestrictUserModelTests(APITestCase):
    """
    RestrictUser has the same self-relation DB guard as Follow/BlockUser
    (see models.py Task 18 notes) but had no test coverage at all.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")

    def test_self_restrict_blocked_at_db_level(self):
        with self.assertRaises(IntegrityError):
            RestrictUser.objects.create(user=self.alice, restricted=self.alice)

    def test_duplicate_restrict_blocked_at_db_level(self):
        RestrictUser.objects.create(user=self.alice, restricted=self.bob)
        with self.assertRaises(IntegrityError):
            RestrictUser.objects.create(user=self.alice, restricted=self.bob)

    def test_restrict_does_not_touch_follow_or_counts(self):
        # Unlike block, restrict must leave Follow/counts completely alone
        # (see RestrictUser's docstring in models.py — silent, non-blocking).
        Follow.objects.create(follower=self.alice, following=self.bob, status=Follow.Status.ACCEPTED)
        self.alice.following_count = 1
        self.alice.save(update_fields=["following_count"])
        self.bob.followers_count = 1
        self.bob.save(update_fields=["followers_count"])

        self.client.force_authenticate(user=self.alice)
        response = self.client.post(reverse("restricted-users"), {"restricted": self.bob.id})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertTrue(Follow.objects.filter(follower=self.alice, following=self.bob).exists())
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 1)
        self.assertEqual(self.bob.followers_count, 1)


class UserSearchExclusionTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob_smith", password="pass12345")
        self.carol = User.objects.create_user(username="bob_jones", password="pass12345")
        self.client.force_authenticate(user=self.alice)

    def _search(self, query):
        response = self.client.get(reverse("user-search"), {"search": query})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # `data` is either a plain list or a paginated dict with `results`,
        # depending on the project's default pagination setting — handle
        # both instead of assuming one.
        results = response.data["data"]
        if isinstance(results, dict) and "results" in results:
            results = results["results"]
        return [u["username"] for u in results]

    def test_search_excludes_self(self):
        usernames = self._search("alice")
        self.assertNotIn(self.alice.username, usernames)

    def test_search_excludes_users_i_blocked(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        usernames = self._search("bob")
        self.assertNotIn(self.bob.username, usernames)
        # carol ("bob_jones") isn't blocked by/from alice, so she should
        # still show up for a "bob" search.
        self.assertIn(self.carol.username, usernames)

    def test_search_excludes_users_who_blocked_me(self):
        BlockUser.objects.create(blocker=self.carol, blocked=self.alice)
        usernames = self._search("bob")
        self.assertNotIn(self.carol.username, usernames)
        self.assertIn(self.bob.username, usernames)


class FollowRaceConditionTests(APITestCase):
    """
    `test_self_follow_blocked_at_db_level` above already proves the DB
    constraint exists. This proves `FollowAPIView.post` actually catches
    that `IntegrityError` (its own comment names exactly this race: two
    concurrent requests both pass the `.filter().first()` "does this
    follow exist" check before either commits, then both call `.create()`)
    and returns a clean response instead of letting it bubble up as a 500.

    A genuine two-thread race is slow/flaky in a test suite, so this
    forces the identical failure deterministically by making `Follow.
    objects.create` raise `IntegrityError` on its first call, exactly as
    if a concurrent request had just won that race and committed first.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")
        self.client.force_authenticate(user=self.alice)

    def test_concurrent_follow_does_not_500_and_reads_as_followed(self):
        url = reverse("follow-user", args=[self.bob.id])
        with mock.patch(
            "user_profile.models.Follow.objects.create",
            side_effect=IntegrityError('duplicate key value violates unique constraint "unique_follow"'),
        ):
            response = self.client.post(url)

        # The view's except-block re-queries for the row the "winning"
        # concurrent request must have just created. Here nothing actually
        # created it (only `.create` was mocked, not `.filter`), so it
        # correctly finds no row either — the important assertion is that
        # this reads as a clean 200, never an unhandled 500.
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["status"])

# ==========================================================================
# TASK 4 — Withdraw-Coin flow.
#
# Written against the real `user_profile/views.py`/`urls.py`/
# `serializers.py` (all uploaded for this pass). Confirmed endpoint:
#   CoinWithdrawalRequestView   GET/POST /coin-withdrawals/   "coin-withdrawal-requests"
# ==========================================================================

class CoinWithdrawalRequestManagerTests(APITestCase):
    """
    Direct manager-level tests — CoinWithdrawalRequestManager is the
    sanctioned write path (models.py), same as
    CoinPurchaseRequestManager is for CoinPurchaseRequest.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.alice.coin = 500
        self.alice.save(update_fields=["coin"])

    def test_withdrawal_debits_exact_amount(self):
        withdrawal = CoinWithdrawalRequest.objects.request_withdrawal(
            user=self.alice,
            coins=200,
            payout_method=CoinWithdrawalRequest.PayoutMethod.UPI,
            payout_details={"upi_id": "alice@bank"},
        )

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 300)

        self.assertEqual(withdrawal.status, CoinWithdrawalRequest.Status.PENDING)
        self.assertIsNotNone(withdrawal.debit_ledger_entry)
        self.assertEqual(withdrawal.debit_ledger_entry.amount, -200)
        self.assertEqual(
            withdrawal.debit_ledger_entry.transaction_type,
            CoinLedger.TransactionType.WITHDRAWAL_REQUESTED,
        )

    def test_rejected_withdrawal_credits_coins_back(self):
        withdrawal = CoinWithdrawalRequest.objects.request_withdrawal(
            user=self.alice,
            coins=200,
            payout_method=CoinWithdrawalRequest.PayoutMethod.UPI,
            payout_details={"upi_id": "alice@bank"},
        )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 300)  # sanity check on the debit

        rejected = CoinWithdrawalRequest.objects.reject(
            withdrawal_id=withdrawal.pk, reason="Bad IFSC code"
        )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 500)  # fully refunded
        self.assertEqual(rejected.status, CoinWithdrawalRequest.Status.REJECTED)
        self.assertIsNotNone(rejected.refund_ledger_entry)
        self.assertEqual(rejected.refund_ledger_entry.amount, 200)
        self.assertEqual(
            rejected.refund_ledger_entry.transaction_type,
            CoinLedger.TransactionType.WITHDRAWAL_REJECTED,
        )

        # Idempotency: a retried reject() call (double form submit, admin
        # double-click) must not credit the refund a second time.
        CoinWithdrawalRequest.objects.reject(withdrawal_id=withdrawal.pk, reason="retry")
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 500)


class CoinWithdrawalRequestAPITests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.alice.coin = 500
        self.alice.save(update_fields=["coin"])
        self.client.force_authenticate(user=self.alice)

    def test_insufficient_balance_returns_402(self):
        response = self.client.post(
            reverse("coin-withdrawal-requests"),
            {
                "coins": 10_000,
                "payout_method": CoinWithdrawalRequest.PayoutMethod.UPI,
                "payout_details": {"upi_id": "alice@bank"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertFalse(response.data["status"])

        # No partial debit, and no request row left behind — the debit
        # and the row creation share one transaction.atomic() block in
        # CoinWithdrawalRequestManager.request_withdrawal.
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 500)
        self.assertFalse(CoinWithdrawalRequest.objects.filter(user=self.alice).exists())

    def test_withdrawal_request_via_api_creates_pending_row(self):
        response = self.client.post(
            reverse("coin-withdrawal-requests"),
            {
                "coins": 150,
                "payout_method": CoinWithdrawalRequest.PayoutMethod.UPI,
                "payout_details": {"upi_id": "alice@bank"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["status"])
        self.assertEqual(response.data["data"]["status"], CoinWithdrawalRequest.Status.PENDING)

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 350)

    def test_missing_payout_details_is_rejected(self):
        response = self.client.post(
            reverse("coin-withdrawal-requests"),
            {"coins": 150, "payout_method": CoinWithdrawalRequest.PayoutMethod.UPI, "payout_details": {}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 500)  # validation failed before any debit