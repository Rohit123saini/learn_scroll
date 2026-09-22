# user_profile/tests_performance_fixes.py
#
# Regression tests for the performance / scalability audit items 17-21.
import hashlib
import hmac
import json
from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.db import connection
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from . import tasks
from .admin import CoinLedgerAdmin, WithdrawalEligibleFilter
from .models import (
    BlockUser,
    CoinLedger,
    CoinLedgerBusy,
    CoinPurchaseRequest,
    CoinWithdrawalRequest,
    Follow,
    coin_lock_guard,
)
from .serializers import bulk_accepted_connection_ids
from .signals import follow_counts_recomputed

User = get_user_model()
TT = CoinLedger.TransactionType


def mk(name, **kw):
    return User.objects.create_user(username=name, password="pass12345", **kw)


def counts(u):
    u.refresh_from_db()
    return (u.followers_count, u.following_count)


# --------------------------------------------------------------------- 17
class BoundedBulkConnectionsTests(APITestCase):
    def test_unrestricted_call_over_the_page_limit_is_refused(self):
        with override_settings(MAX_PAGE_SIZE=5):
            with self.assertRaises(ValueError):
                bulk_accepted_connection_ids(range(1, 7))
            bulk_accepted_connection_ids(range(1, 6))  # exactly the limit is fine

    def test_restricted_call_is_chunked_not_one_giant_in_list(self):
        me = mk("me")
        ids = list(range(10**6, 10**6 + 1200))
        with CaptureQueriesContext(connection) as ctx:
            result = bulk_accepted_connection_ids(ids, restrict_to_user=me)
        self.assertEqual(result, {})
        self.assertEqual(len(ctx.captured_queries), 6)  # 3 chunks x 2 queries
        for q in ctx.captured_queries:
            # no statement ever carries more than one chunk's worth of ids
            self.assertLess(q["sql"].count(","), 520, "IN list is not chunked")


class ChatContactSearchScalingTests(APITestCase):
    def setUp(self):
        self.me = mk("me")
        self.client.force_authenticate(self.me)
        self.url = reverse("chat-search") if False else None

    def _url(self):
        from django.urls import get_resolver
        for name in ("chat-search", "message-contact-search", "chat-contacts"):
            try:
                return reverse(name)
            except Exception:
                continue
        raise AssertionError("chat-search url name not found")

    def _connect(self, n, prefix):
        made = []
        for i in range(n):
            other = mk(f"{prefix}{i}")
            if i % 2:
                Follow.objects.create(follower=self.me, following=other)
            else:
                Follow.objects.create(follower=other, following=self.me)
            made.append(other)
        return made

    def results(self, **params):
        r = self.client.get(self._url(), params)
        self.assertEqual(r.status_code, 200, r.data)
        return {row["username"]: row for row in r.data["data"]["results"]}

    def test_returns_followers_and_followees_only_and_hides_blocked_and_self(self):
        following, follower, stranger = mk("following"), mk("follower"), mk("stranger")
        blocked_by_me, blocked_me = mk("blockedbyme"), mk("blockedme")
        Follow.objects.create(follower=self.me, following=following)
        Follow.objects.create(follower=follower, following=self.me)
        Follow.objects.create(follower=self.me, following=blocked_by_me)
        Follow.objects.create(follower=blocked_me, following=self.me)
        BlockUser.objects.create(blocker=self.me, blocked=blocked_by_me)
        BlockUser.objects.create(blocker=blocked_me, blocked=self.me)
        got = self.results()
        self.assertEqual(set(got), {"following", "follower"})

    def test_pending_requests_are_not_contacts(self):
        pend = mk("pending")
        Follow.objects.create(follower=self.me, following=pend, status=Follow.Status.PENDING)
        self.assertEqual(self.results(), {})

    def test_query_count_does_not_grow_with_the_number_of_connections(self):
        self._connect(4, "a")
        with CaptureQueriesContext(connection) as small:
            self.results()
        self._connect(40, "b")
        with CaptureQueriesContext(connection) as big:
            self.results()
        self.assertEqual(len(small.captured_queries), len(big.captured_queries))
        # and no query ships the connection ids as a literal list
        for q in big.captured_queries:
            self.assertLess(q["sql"].count(","), 200)

    def test_mutual_friends_count_is_correct_without_loading_my_set(self):
        m = mk("mutual")
        pal = mk("pal")
        Follow.objects.create(follower=self.me, following=m)
        Follow.objects.create(follower=pal, following=m)
        Follow.objects.create(follower=pal, following=self.me)
        row = self.results()["pal"]
        self.assertEqual(row["mutual_friends"], 1)

    def test_search_filter_still_works(self):
        self._connect(3, "zed")
        other = mk("needle")
        Follow.objects.create(follower=self.me, following=other)
        self.assertEqual(set(self.results(search="needle")), {"needle"})


# --------------------------------------------------------------------- 18
class BlockAndDeleteFanOutTests(APITestCase):
    def test_hard_deleting_a_hub_user_recounts_others_in_a_constant_number_of_updates(self):
        hub = mk("hub")
        fans = [mk(f"fan{i}") for i in range(30)]
        for f in fans:
            Follow.objects.create(follower=f, following=hub)
            Follow.objects.create(follower=hub, following=f)
        self.assertEqual(counts(hub), (30, 30))
        with CaptureQueriesContext(connection) as ctx:
            hub.delete()
        updates = [q for q in ctx.captured_queries if q["sql"].startswith('UPDATE "login_user"')]
        self.assertLessEqual(len(updates), 3, [u["sql"][:60] for u in updates])
        for f in fans:
            self.assertEqual(counts(f), (0, 0))

    def test_block_removes_both_follows_with_bounded_queries(self):
        a, b = mk("a"), mk("b")
        Follow.objects.create(follower=a, following=b)
        Follow.objects.create(follower=b, following=a)
        self.client.force_authenticate(a)
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.post(reverse("blocked-users"), {"blocked": b.id}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        # at most two Follow rows can ever exist between two users, so this is
        # a small constant — not one query per connection
        self.assertLess(len(ctx.captured_queries), 25)
        self.assertEqual(counts(a), (0, 0))
        self.assertEqual(counts(b), (0, 0))


# --------------------------------------------------------------------- 19
class WalletLockGuardTests(APITestCase):
    def _lock_error(self, code="55P03"):
        class FakePgError(Exception):
            pgcode = code
            sqlstate = None

        err = OperationalError("could not obtain lock")
        err.__cause__ = FakePgError()
        return err

    def test_lock_timeout_becomes_coin_ledger_busy(self):
        with self.assertRaises(CoinLedgerBusy):
            with coin_lock_guard():
                raise self._lock_error()

    def test_other_operational_errors_pass_through(self):
        with self.assertRaises(OperationalError):
            with coin_lock_guard():
                raise self._lock_error(code="08006")  # connection failure

    def test_guard_is_a_noop_on_non_postgres(self):
        with CaptureQueriesContext(connection) as ctx:
            with coin_lock_guard():
                pass
        self.assertEqual(len(ctx.captured_queries), 0)

    def test_record_transaction_surfaces_busy_and_writes_nothing(self):
        u = mk("busy")
        with mock.patch("django.db.models.query.QuerySet.get", side_effect=self._lock_error()):
            with self.assertRaises(CoinLedgerBusy):
                CoinLedger.objects.record_transaction(
                    user=u, transaction_type=TT.PURCHASE, amount=10, reference="x",
                )
        self.assertEqual(CoinLedger.objects.count(), 0)
        u.refresh_from_db()
        self.assertEqual(u.coin, 0)

    def test_withdrawal_view_returns_503_with_retry_after(self):
        u = mk("wd")
        CoinLedger.objects.record_transaction(user=u, transaction_type=TT.PURCHASE, amount=500, reference="f")
        self.client.force_authenticate(u)
        with mock.patch.object(
            CoinWithdrawalRequest.objects, "request_withdrawal", side_effect=CoinLedgerBusy("busy"),
        ):
            r = self.client.post(reverse("coin-withdrawal-requests"), {
                "coins": 150, "payout_method": "upi", "payout_details": {"upi_id": "alice@bank"},
            }, format="json")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r["Retry-After"], "2")

    @override_settings(
        PAYMENT_GATEWAY_WEBHOOK_SECRETS={"razorpay": "s"},
        PAYMENT_GATEWAY_WEBHOOK_SIGNATURE_HEADERS={"razorpay": "X-Webhook-Signature"},
    )
    def test_webhook_returns_503_so_the_gateway_retries(self):
        u = mk("payer")
        CoinPurchaseRequest.objects.start_purchase(
            user=u, gateway_reference="r1", amount="5.00", coins=5, gateway="razorpay",
        )
        body = json.dumps({"gateway_reference": "r1", "status": "success"})
        sig = hmac.new(b"s", body.encode(), hashlib.sha256).hexdigest()
        with mock.patch.object(
            CoinPurchaseRequest.objects, "confirm_success", side_effect=CoinLedgerBusy("busy"),
        ):
            r = self.client.post(
                reverse("buy-coin-confirm"), data=body, content_type="application/json",
                HTTP_X_WEBHOOK_SIGNATURE=sig,
            )
        self.assertEqual(r.status_code, 503)
        u.refresh_from_db()
        self.assertEqual(u.coin, 0)

    def test_normal_wallet_flow_still_works_with_only_pk_and_coin_loaded(self):
        u = mk("ok")
        e1 = CoinLedger.objects.record_transaction(user=u, transaction_type=TT.PURCHASE, amount=100, reference="a")
        e2 = CoinLedger.objects.record_transaction(user=u, transaction_type=TT.SPEND, amount=-30, reference="b")
        self.assertEqual((e1.balance_after, e2.balance_after), (100, 70))
        u.refresh_from_db()
        self.assertEqual(u.coin, 70)


# --------------------------------------------------------------------- 20
class ReconcileTaskTests(APITestCase):
    def setUp(self):
        self.seen = []

        def receiver(sender, user_ids, **kwargs):
            self.seen.append(set(user_ids))

        self.receiver = receiver
        follow_counts_recomputed.connect(receiver, weak=False)
        self.addCleanup(follow_counts_recomputed.disconnect, receiver)

    def test_signal_reports_every_recount(self):
        a, b = mk("a"), mk("b")
        Follow.objects.create(follower=a, following=b)
        self.assertTrue(any({a.id, b.id} <= ids for ids in self.seen))

    def test_a_failing_subscriber_cannot_break_a_follow(self):
        def bad(sender, **kwargs):
            raise RuntimeError("cache down")

        follow_counts_recomputed.connect(bad, weak=False)
        self.addCleanup(follow_counts_recomputed.disconnect, bad)
        a, b = mk("a"), mk("b")
        Follow.objects.create(follower=a, following=b)  # must not raise
        self.assertEqual(counts(b), (1, 0))

    def _drift(self):
        a, b, c, d, e = (mk(n) for n in "abcde")
        Follow.objects.create(follower=a, following=b)
        Follow.objects.create(follower=c, following=b)
        Follow.objects.create(follower=b, following=d)
        # corrupt WITHOUT signals: too high, too low, and a stale non-zero
        # counter on a user with no follows at all
        User.objects.filter(pk=b.pk).update(followers_count=99, following_count=0)
        User.objects.filter(pk=d.pk).update(followers_count=0)
        User.objects.filter(pk=e.pk).update(followers_count=7, following_count=3)
        self.seen.clear()
        return a, b, c, d, e

    def test_reconcile_repairs_drift_in_both_directions(self):
        a, b, c, d, e = self._drift()
        out = tasks.reconcile_follow_counts()
        self.assertEqual(counts(a), (0, 1))
        self.assertEqual(counts(b), (2, 1))
        self.assertEqual(counts(d), (1, 0))
        self.assertEqual(counts(e), (0, 0))
        self.assertEqual(out["corrected_followers_count"], 3)   # b, d, e
        self.assertEqual(out["corrected_following_count"], 2)   # b, e

    def test_reconcile_notifies_subscribers_and_never_uses_bulk_update(self):
        a, b, c, d, e = self._drift()
        with mock.patch.object(User.objects, "bulk_update") as bu:
            tasks.reconcile_follow_counts()
        bu.assert_not_called()
        notified = set().union(*self.seen)
        self.assertEqual(notified, {b.id, d.id, e.id})  # only the drifted users

    def test_reconcile_is_correct_across_chunk_boundaries(self):
        a, b, c, d, e = self._drift()
        with mock.patch.object(tasks, "_CHUNK_SIZE", 2):
            out = tasks.reconcile_follow_counts()
        self.assertEqual(counts(b), (2, 1))
        self.assertEqual(counts(e), (0, 0))
        self.assertEqual(out["checked"], User.objects.count())

    def test_reconcile_with_no_drift_writes_nothing(self):
        a, b = mk("a"), mk("b")
        Follow.objects.create(follower=a, following=b)
        self.seen.clear()
        with CaptureQueriesContext(connection) as ctx:
            out = tasks.reconcile_follow_counts()
        self.assertEqual(out["corrected_followers_count"] + out["corrected_following_count"], 0)
        self.assertFalse([q for q in ctx.captured_queries if q["sql"].startswith("UPDATE")])
        self.assertEqual(self.seen, [])


# --------------------------------------------------------------------- 21
class LedgerAdminFilterTests(APITestCase):
    def setUp(self):
        self.u = mk("ledgeruser")
        self.staff = mk("boss", is_staff=True, is_superuser=True)

    def _filter(self, value):
        request = RequestFactory().get("/admin/", {"withdrawal_eligible": value})
        request.user = self.staff
        model_admin = CoinLedgerAdmin(CoinLedger, admin.site)
        flt = WithdrawalEligibleFilter(
            request, {"withdrawal_eligible": [value]}, CoinLedger, model_admin,
        )
        return flt.queryset(request, CoinLedger.objects.all())

    def _raw_row(self, ttype, amount):
        # A row with NO metadata at all — like rows written before the flag existed
        return CoinLedger.objects.create(
            user=self.u, transaction_type=ttype, amount=amount, balance_after=0, metadata={},
        )

    def test_filter_uses_transaction_type_not_json(self):
        purchase = self._raw_row(TT.PURCHASE, 10)
        gift = self._raw_row(TT.GIFT_RECEIVED, 10)
        earn = self._raw_row(TT.EARN, 10)
        spend = self._raw_row(TT.SPEND, -5)
        yes = self._filter("1")
        no = self._filter("0")
        self.assertEqual(set(yes.values_list("pk", flat=True)), {purchase.pk, gift.pk})
        self.assertEqual(set(no.values_list("pk", flat=True)), {earn.pk, spend.pk})
        # (the SELECT list names every column; what matters is the WHERE clause)
        self.assertNotIn("metadata", str(yes.query).lower().split(" where ")[1])
        self.assertNotIn("metadata", str(no.query).lower().split(" where ")[1])

    def test_no_filter_value_returns_everything(self):
        self._raw_row(TT.EARN, 1)
        self.assertEqual(self._filter("").count(), 1)

    def test_metadata_flag_agrees_with_fraud_for_rejected_withdrawals(self):
        CoinLedger.objects.record_transaction(user=self.u, transaction_type=TT.PURCHASE, amount=500, reference="f")
        w = CoinWithdrawalRequest.objects.request_withdrawal(
            user=self.u, coins=200, payout_method="upi", payout_details={"upi_id": "alice@bank"},
        )
        CoinWithdrawalRequest.objects.reject(withdrawal_id=w.pk, reason="bad")
        refund = CoinLedger.objects.get(transaction_type=TT.WITHDRAWAL_REJECTED)
        self.assertIs(refund.metadata["withdrawal_eligible"], True)
        self.assertIn(refund.pk, set(self._filter("1").values_list("pk", flat=True)))

    def test_transaction_type_index_exists(self):
        names = {i.name for i in CoinLedger._meta.indexes}
        self.assertIn("coinledger_type_created_idx", names)
