"""
Postgres-only concurrency tests (task 11).

Why this file exists
--------------------
SQLite silently IGNORES ``select_for_update()`` (it only has a single writer
lock), so every "two requests at the same time" guarantee in the code base --
the coin ledger, copyright claim decisions -- can look green on SQLite while
being untested. These tests use real threads, each with its own DB connection,
against a real PostgreSQL server, and are SKIPPED on any other backend.

Run them locally / in CI with:

    DATABASE_URL=postgres://user:pass@localhost:5432/learnscroll \
        python manage.py test core.tests_postgres_concurrency --noinput

They use TransactionTestCase (not TestCase): threads need COMMITTED data, and a
TestCase wraps everything in one uncommitted transaction other connections
cannot see.
"""
import threading
import unittest

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from copyrights import services
from copyrights.models import CopyrightClaim, CopyrightStrike
from copyrights.tests import mk_claimant, mk_post, mk_staff, mk_user, notice
from user_profile.models import CoinLedger

User = get_user_model()
C = CopyrightClaim

IS_POSTGRES = connection.vendor == "postgresql"
needs_postgres = unittest.skipUnless(IS_POSTGRES, "select_for_update() is only enforced on PostgreSQL")


def run_parallel(*fns, timeout=30):
    """Run every callable in its own thread + own DB connection, released together.

    Returns a list of ("ok", value) / ("err", exception) in the same order.
    """
    barrier = threading.Barrier(len(fns))
    results = [None] * len(fns)

    def worker(i, fn):
        try:
            barrier.wait(timeout=timeout)
            results[i] = ("ok", fn())
        except BaseException as exc:  # noqa: BLE001 - we want to inspect every failure
            results[i] = ("err", exc)
        finally:
            connection.close()  # each thread owns a connection; never leak it

    threads = [threading.Thread(target=worker, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    assert not any(t.is_alive() for t in threads), "a worker thread hung (possible deadlock)"
    return results


def oks(results):
    return [r for r in results if r[0] == "ok"]


def errs(results):
    return [r[1] for r in results if r[0] == "err"]


@needs_postgres
class RowLockSanityTests(TransactionTestCase):
    """Proves the test setup itself: a held row lock really blocks a second connection."""

    def test_second_connection_cannot_lock_a_row_held_by_the_first(self):
        user = mk_user("locker")
        holding = threading.Event()
        release = threading.Event()

        def holder():
            with transaction.atomic():
                User.objects.select_for_update().get(pk=user.pk)
                holding.set()
                release.wait(10)
            connection.close()

        t = threading.Thread(target=holder)
        t.start()
        self.assertTrue(holding.wait(10))
        try:
            with self.assertRaises(DatabaseError):
                with transaction.atomic():
                    # nowait=True turns "would block" into an immediate error
                    User.objects.select_for_update(nowait=True).get(pk=user.pk)
        finally:
            release.set()
            t.join(10)


@needs_postgres
class CoinLedgerConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = mk_user("wallet")
        User.objects.filter(pk=self.user.pk).update(coin=100)

    def _spend(self, ref):
        def go():
            u = User.objects.get(pk=self.user.pk)
            return CoinLedger.objects.record_transaction(
                user=u, transaction_type=CoinLedger.TransactionType.SPEND,
                amount=-30, reference=ref, description="race")
        return go

    def test_parallel_debits_never_overdraw(self):
        results = run_parallel(*[self._spend(f"spend-{i}") for i in range(10)])
        succeeded = len(oks(results))
        self.user.refresh_from_db()
        self.assertEqual(succeeded, 3, f"100 coins / 30 per spend = exactly 3 winners, got {succeeded}")
        self.assertEqual(self.user.coin, 100 - 30 * succeeded)
        self.assertGreaterEqual(self.user.coin, 0)
        self.assertEqual(CoinLedger.objects.filter(user=self.user, reference__startswith="spend-").count(), succeeded)
        for e in errs(results):  # losers must fail with a clean business error, not a crash
            self.assertIsInstance(e, (ValueError, Exception))
            self.assertNotIsInstance(e, (DatabaseError,), f"unclean DB error: {e!r}")

    def test_same_reference_is_applied_once(self):
        def credit():
            u = User.objects.get(pk=self.user.pk)
            return CoinLedger.objects.record_transaction(
                user=u, transaction_type=CoinLedger.TransactionType.PURCHASE,
                amount=50, reference="webhook-retry-1", description="dup webhook")
        results = run_parallel(*[credit for _ in range(8)])
        self.user.refresh_from_db()
        self.assertEqual(len(errs(results)), 0, errs(results))
        self.assertEqual(self.user.coin, 150, "a retried webhook must credit exactly once")
        self.assertEqual(CoinLedger.objects.filter(user=self.user, reference="webhook-retry-1").count(), 1)


@needs_postgres
class CopyrightDecisionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        call_command("setup_copyright_roles", verbosity=0)
        self.owner = mk_user("owner")
        self.rita = mk_claimant("rita", trusted=False)  # v3: a claimant needs a verified e-mail to file
        self.l2a = mk_staff("senior_a", 2)
        self.l2b = mk_staff("senior_b", 2)
        self.post = mk_post(self.owner)
        self.claim, _ = services.submit_claim(self.rita, notice("post", self.post.id))

    def _uphold(self, staff_pk):
        def go():
            actor = User.objects.get(pk=staff_pk)
            claim = C.objects.get(pk=self.claim.pk)
            return services.uphold_claim(claim, actor, "race")
        return go

    def _reject(self, staff_pk):
        def go():
            actor = User.objects.get(pk=staff_pk)
            claim = C.objects.get(pk=self.claim.pk)
            return services.reject_claim(claim, actor, "not valid")
        return go

    def test_two_seniors_upholding_at_once_decide_once_with_one_strike(self):
        results = run_parallel(self._uphold(self.l2a.pk), self._uphold(self.l2b.pk))
        self.assertEqual(len(oks(results)), 1, results)
        loser = errs(results)
        self.assertEqual(len(loser), 1)
        self.assertIsInstance(loser[0], services.ClaimError)
        self.assertEqual(loser[0].code, "bad_state")
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, C.Status.UPHELD)
        self.assertEqual(CopyrightStrike.objects.filter(claim=self.claim).count(), 1, "double strike!")

    def test_uphold_and_reject_race_has_exactly_one_winner(self):
        results = run_parallel(self._uphold(self.l2a.pk), self._reject(self.l2b.pk))
        self.assertEqual(len(oks(results)), 1, results)
        self.claim.refresh_from_db()
        strikes = CopyrightStrike.objects.filter(claim=self.claim).count()
        if self.claim.status == C.Status.UPHELD:
            self.assertEqual(strikes, 1)
        else:
            self.assertEqual(strikes, 0, "claim was rejected but a strike exists")
