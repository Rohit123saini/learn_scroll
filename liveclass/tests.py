"""
liveclass/tests.py

Regression safety net for the payment-adjacent surface of this app:
coupons, the coin wallet/escrow system, refunds/cancellations, and the
session waitlist. This was previously the single biggest untested area —
zero coverage on money-moving code.

Scope (deliberately NOT a full-app test suite — see README/audit for what's
still uncovered elsewhere, e.g. scheduling/recurrence, chat, polls,
assignments):
    1. Coupon.is_valid() + classroom-scoping rules
    2. _charge_and_create_purchase() — the only place coins actually leave a
       student's wallet (discount math/rounding, insufficient balance,
       free passes, coupon redemption bookkeeping)
    3. PassPurchase escrow — charge_for_session() (per-day release to the
       teacher, idempotency, validity-window edges) and reverse() (refund
       math, no teacher clawback, coupon slot release rule)
    4. The three HTTP entrypoints onto that money movement: join-request
       accept(), pass-purchase cancel()/refund()
    5. Session waitlist — overflow on join, auto-promotion on seat-free,
       manual promote() permissions

Run: python manage.py test liveclass

Notes on fixtures:
    - LiveKit is mocked everywhere (ensure_room/generate_livekit_token/
      remove_participant) — these tests never make a real network call.
    - _safe_delay is patched to a no-op so a missing/unreachable Celery
      broker in CI never slows down or flakes a test (the real function
      already swallows broker errors in production — this just skips the
      attempt entirely so tests don't pay for it).
    - User.coin is assumed to be a plain integer/PositiveIntegerField on
      the custom User model (login.models.User), per the coin-wallet
      design documented across models.py/views.py.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings as django_settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from login.models import User

from .models import (
    ClassJoinRequest,
    ClassPass,
    ClassSession,
    Classroom,
    ClassroomBan,
    CoinTransaction,
    Coupon,
    PassDailyCharge,
    PassPurchase,
    Referral,
    SessionParticipant,
    SessionWaitlist,
    referral_code_for_user,
)
from .views import _charge_and_create_purchase, _try_promote_from_waitlist

# ---------------------------------------------------------------------------
# Shared test settings: silence throttle scopes that don't have a
# DEFAULT_THROTTLE_RATES entry in the real settings.py (coupon_validate,
# session_token) so tests don't blow up on ImproperlyConfigured, and force
# a predictable, generous rate instead of depending on prod values.
# ---------------------------------------------------------------------------
TEST_REST_FRAMEWORK_THROTTLES = override_settings(
    REST_FRAMEWORK={
        "DEFAULT_THROTTLE_RATES": {
            "coupon_validate": "1000/min",
            "session_token": "1000/min",
        }
    }
)

# Patch every LiveKit network call + Celery dispatch at the module level so
# no test in this file ever touches the network or a broker.
LIVEKIT_PATCH = patch.multiple(
    "liveclass.views",
    ensure_room=lambda *a, **k: None,
    generate_livekit_token=lambda *a, **k: "fake-token",
    remove_participant=lambda *a, **k: None,
)
SAFE_DELAY_PATCH = patch("liveclass.views._safe_delay", lambda *a, **k: None)


def _apply_common_patches(test_case: TestCase):
    p1 = LIVEKIT_PATCH
    p2 = SAFE_DELAY_PATCH
    p1.start()
    p2.start()
    test_case.addCleanup(p1.stop)
    test_case.addCleanup(p2.stop)


class LiveClassTestBase(TestCase):
    """Common fixtures shared by every test class below: a teacher, a
    student, an active classroom, and a priced (non-free) pass on it."""

    def setUp(self):
        _apply_common_patches(self)

        self.teacher = User.objects.create_user(
            username="teacher1", password="pass12345", email="teacher1@example.com"
        )
        self.student = User.objects.create_user(
            username="student1", password="pass12345", email="student1@example.com"
        )
        self.other_teacher = User.objects.create_user(
            username="teacher2", password="pass12345", email="teacher2@example.com"
        )
        self.student.coin = 1000
        self.student.save(update_fields=["coin"])

        self.classroom = Classroom.objects.create(
            teacher=self.teacher, title="DSA Batch", max_participants=2
        )
        self.other_classroom = Classroom.objects.create(
            teacher=self.other_teacher, title="Chemistry Batch"
        )

        # 10-day pass, 100 coins -> per_day_rate = 10 exactly (no rounding
        # edge cases by default; individual tests override where they need
        # a fractional per_day_rate).
        self.class_pass = ClassPass.objects.create(
            classroom=self.classroom,
            pass_type=ClassPass.PassType.MONTHLY,
            price=Decimal("100"),
            validity_days=10,
        )

    def make_session(self, classroom=None, status_=ClassSession.Status.SCHEDULED, **kwargs):
        classroom = classroom or self.classroom
        now = timezone.now()
        defaults = dict(
            classroom=classroom,
            scheduled_start=now,
            scheduled_end=now + timedelta(hours=1),
            status=status_,
        )
        defaults.update(kwargs)
        return ClassSession.objects.create(**defaults)


# ===========================================================================
# 1. COUPON — validity + classroom scoping
# ===========================================================================
class CouponValidityTests(LiveClassTestBase):
    def test_active_coupon_within_window_is_valid(self):
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            code="SAVE10",
            discount_percent=10,
            valid_until=timezone.now() + timedelta(days=5),
        )
        self.assertTrue(coupon.is_valid())

    def test_inactive_coupon_is_invalid(self):
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            code="OFF20",
            discount_percent=20,
            valid_until=timezone.now() + timedelta(days=5),
            is_active=False,
        )
        self.assertFalse(coupon.is_valid())

    def test_expired_coupon_is_invalid(self):
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            code="EXPIRED",
            discount_percent=20,
            valid_from=timezone.now() - timedelta(days=10),
            valid_until=timezone.now() - timedelta(days=1),
        )
        self.assertFalse(coupon.is_valid())

    def test_future_coupon_not_yet_valid(self):
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            code="FUTURE",
            discount_percent=20,
            valid_from=timezone.now() + timedelta(days=1),
            valid_until=timezone.now() + timedelta(days=10),
        )
        self.assertFalse(coupon.is_valid())

    def test_max_uses_exhausted_is_invalid(self):
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            code="LIMITED",
            discount_percent=10,
            valid_until=timezone.now() + timedelta(days=5),
            max_uses=1,
            used_count=1,
        )
        self.assertFalse(coupon.is_valid())

    def test_max_uses_not_yet_reached_is_valid(self):
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            code="LIMITED2",
            discount_percent=10,
            valid_until=timezone.now() + timedelta(days=5),
            max_uses=5,
            used_count=4,
        )
        self.assertTrue(coupon.is_valid())


# ===========================================================================
# 2. _charge_and_create_purchase — the only coin-debiting code path
# ===========================================================================
class ChargeAndCreatePurchaseTests(LiveClassTestBase):
    def test_full_price_debited_when_no_coupon(self):
        purchase = _charge_and_create_purchase(self.student, self.class_pass, coupon_code="")
        self.student.refresh_from_db()

        self.assertEqual(purchase.coins_spent, 100)
        self.assertEqual(purchase.amount_paid, Decimal("100"))
        self.assertEqual(purchase.status, PassPurchase.Status.SUCCESS)
        self.assertEqual(purchase.payment_method, PassPurchase.PaymentMethod.COIN_WALLET)
        self.assertEqual(self.student.coin, 900)

        txn = CoinTransaction.objects.get(user=self.student)
        self.assertEqual(txn.txn_type, CoinTransaction.TxnType.DEBIT)
        self.assertEqual(txn.reason, CoinTransaction.Reason.PASS_PURCHASE)
        self.assertEqual(txn.amount, 100)
        self.assertEqual(txn.balance_after, 900)

    def test_per_day_rate_snapshot_is_frozen(self):
        purchase = _charge_and_create_purchase(self.student, self.class_pass, coupon_code="")
        self.assertEqual(purchase.per_day_rate, Decimal("100") / 10)

        # Later price edit on the ClassPass must NOT retroactively change
        # an already-purchased pass's per-day rate.
        self.class_pass.price = Decimal("500")
        self.class_pass.save(update_fields=["price"])
        purchase.refresh_from_db()
        self.assertEqual(purchase.per_day_rate, Decimal("10"))

    def test_insufficient_balance_raises_and_writes_nothing(self):
        self.student.coin = 50
        self.student.save(update_fields=["coin"])

        with self.assertRaises(ValidationError):
            _charge_and_create_purchase(self.student, self.class_pass, coupon_code="")

        self.student.refresh_from_db()
        self.assertEqual(self.student.coin, 50)
        self.assertFalse(PassPurchase.objects.exists())
        self.assertFalse(CoinTransaction.objects.exists())

    def test_free_pass_no_coin_debit(self):
        free_pass = ClassPass.objects.create(
            classroom=self.classroom,
            pass_type=ClassPass.PassType.FREE,
            price=Decimal("0"),
            validity_days=30,
        )
        purchase = _charge_and_create_purchase(self.student, free_pass, coupon_code="")
        self.student.refresh_from_db()

        self.assertEqual(purchase.coins_spent, 0)
        self.assertEqual(purchase.payment_method, PassPurchase.PaymentMethod.FREE)
        self.assertEqual(self.student.coin, 1000)
        self.assertFalse(CoinTransaction.objects.exists())

    def test_percent_discount_rounds_half_up_in_teachers_favor(self):
        # 20% off a 49-coin pass = 39.2 -> must round UP to 39... wait,
        # ROUND_HALF_UP on 39.2 rounds to 39 (nearest), the leak case the
        # code comments call out is e.g. 49.6 -> should become 50, not 49.
        cheap_pass = ClassPass.objects.create(
            classroom=self.classroom,
            pass_type=ClassPass.PassType.DAILY,
            price=Decimal("62"),
            validity_days=5,
        )
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            classroom=self.classroom,
            code="R20",
            discount_percent=20,
            valid_until=timezone.now() + timedelta(days=5),
        )
        # 62 * 0.8 = 49.6 -> should round to 50, not truncate to 49.
        purchase = _charge_and_create_purchase(self.student, cheap_pass, coupon_code="r20")
        self.assertEqual(purchase.coins_spent, 50)
        self.assertEqual(purchase.amount_paid, Decimal("50"))

    def test_amount_and_percent_discount_stack_and_floor_at_zero(self):
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            classroom=self.classroom,
            code="STACK",
            discount_percent=50,
            discount_amount=Decimal("80"),
            valid_until=timezone.now() + timedelta(days=5),
        )
        # 100 - 50% = 50, then - 80 = -30 -> floored to 0 -> FREE, no debit.
        purchase = _charge_and_create_purchase(self.student, self.class_pass, coupon_code="STACK")
        self.assertEqual(purchase.coins_spent, 0)
        self.assertEqual(purchase.payment_method, PassPurchase.PaymentMethod.FREE)

    def test_coupon_code_is_case_insensitive(self):
        Coupon.objects.create(
            created_by=self.teacher,
            classroom=self.classroom,
            code="MixedCase",
            discount_percent=10,
            valid_until=timezone.now() + timedelta(days=5),
        )
        purchase = _charge_and_create_purchase(self.student, self.class_pass, coupon_code="mixedcase")
        self.assertEqual(purchase.coins_spent, 90)

    def test_invalid_coupon_code_raises(self):
        with self.assertRaises(ValidationError):
            _charge_and_create_purchase(self.student, self.class_pass, coupon_code="DOES_NOT_EXIST")

    def test_expired_coupon_raises(self):
        Coupon.objects.create(
            created_by=self.teacher,
            classroom=self.classroom,
            code="OLD",
            discount_percent=10,
            valid_from=timezone.now() - timedelta(days=20),
            valid_until=timezone.now() - timedelta(days=10),
        )
        with self.assertRaises(ValidationError):
            _charge_and_create_purchase(self.student, self.class_pass, coupon_code="OLD")

    def test_coupon_scoped_to_different_classroom_is_rejected(self):
        other_pass = ClassPass.objects.create(
            classroom=self.other_classroom,
            pass_type=ClassPass.PassType.MONTHLY,
            price=Decimal("100"),
            validity_days=10,
        )
        # Coupon explicitly scoped to self.classroom, but purchase is
        # against other_teacher's classroom/pass -> must be rejected even
        # though the code exists and is otherwise valid.
        Coupon.objects.create(
            created_by=self.teacher,
            classroom=self.classroom,
            code="MINE",
            discount_percent=50,
            valid_until=timezone.now() + timedelta(days=5),
        )
        with self.assertRaises(ValidationError):
            _charge_and_create_purchase(self.student, other_pass, coupon_code="MINE")

    def test_unscoped_coupon_usable_across_creators_own_classrooms(self):
        second_classroom = Classroom.objects.create(teacher=self.teacher, title="Second Batch")
        second_pass = ClassPass.objects.create(
            classroom=second_classroom,
            pass_type=ClassPass.PassType.MONTHLY,
            price=Decimal("100"),
            validity_days=10,
        )
        Coupon.objects.create(
            created_by=self.teacher,
            classroom=None,  # usable across all of this teacher's classrooms
            code="ANYCLASS",
            discount_percent=10,
            valid_until=timezone.now() + timedelta(days=5),
        )
        purchase = _charge_and_create_purchase(self.student, second_pass, coupon_code="ANYCLASS")
        self.assertEqual(purchase.coins_spent, 90)

    def test_unscoped_coupon_not_usable_on_another_teachers_classroom(self):
        other_pass = ClassPass.objects.create(
            classroom=self.other_classroom,
            pass_type=ClassPass.PassType.MONTHLY,
            price=Decimal("100"),
            validity_days=10,
        )
        Coupon.objects.create(
            created_by=self.teacher,
            classroom=None,
            code="STEAL",
            discount_percent=90,
            valid_until=timezone.now() + timedelta(days=5),
        )
        with self.assertRaises(ValidationError):
            _charge_and_create_purchase(self.student, other_pass, coupon_code="STEAL")

    def test_coupon_used_count_increments_on_successful_purchase(self):
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            classroom=self.classroom,
            code="COUNTME",
            discount_percent=10,
            valid_until=timezone.now() + timedelta(days=5),
        )
        _charge_and_create_purchase(self.student, self.class_pass, coupon_code="COUNTME")
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 1)

    def test_used_count_not_incremented_when_purchase_fails(self):
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            classroom=self.classroom,
            code="NOCHARGE",
            discount_percent=1,  # still leaves a large balance due
            valid_until=timezone.now() + timedelta(days=5),
        )
        self.student.coin = 0
        self.student.save(update_fields=["coin"])
        with self.assertRaises(ValidationError):
            _charge_and_create_purchase(self.student, self.class_pass, coupon_code="NOCHARGE")
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 0)


# ===========================================================================
# 3. PassPurchase escrow — charge_for_session() / sync_missed_charges()
# ===========================================================================
class EscrowChargeForSessionTests(LiveClassTestBase):
    def _buy(self):
        return _charge_and_create_purchase(self.student, self.class_pass, coupon_code="")

    def test_completed_session_releases_one_days_rate_to_teacher(self):
        purchase = self._buy()
        session = self.make_session(status_=ClassSession.Status.COMPLETED, actual_end=timezone.now())

        charge = purchase.charge_for_session(session)
        purchase.refresh_from_db()
        self.teacher.refresh_from_db()

        self.assertIsNotNone(charge)
        self.assertEqual(charge.amount, 10)  # 100 coins / 10 days
        self.assertEqual(purchase.coins_released, 10)
        self.assertEqual(purchase.remaining_balance, 90)
        self.assertEqual(self.teacher.coin, 10)

        txn = CoinTransaction.objects.get(user=self.teacher)
        self.assertEqual(txn.reason, CoinTransaction.Reason.CLASS_EARNING)
        self.assertEqual(txn.amount, 10)

    def test_charging_twice_for_the_same_date_is_idempotent(self):
        purchase = self._buy()
        session = self.make_session(status_=ClassSession.Status.COMPLETED, actual_end=timezone.now())

        purchase.charge_for_session(session)
        purchase.charge_for_session(session)  # simulate signal firing twice
        purchase.refresh_from_db()
        self.teacher.refresh_from_db()

        self.assertEqual(purchase.coins_released, 10)  # not 20
        self.assertEqual(self.teacher.coin, 10)
        self.assertEqual(
            PassDailyCharge.objects.filter(purchase=purchase).count(), 1
        )

    def test_two_different_days_release_twice(self):
        purchase = self._buy()
        day1 = self.make_session(
            status_=ClassSession.Status.COMPLETED,
            scheduled_start=timezone.now(),
            actual_end=timezone.now(),
        )
        day2 = self.make_session(
            status_=ClassSession.Status.COMPLETED,
            scheduled_start=timezone.now() + timedelta(days=1),
            actual_end=timezone.now() + timedelta(days=1),
        )
        purchase.charge_for_session(day1)
        purchase.charge_for_session(day2)
        purchase.refresh_from_db()

        self.assertEqual(purchase.coins_released, 20)
        self.assertEqual(
            PassDailyCharge.objects.filter(purchase=purchase).count(), 2
        )

    def test_session_outside_validity_window_is_not_charged(self):
        purchase = self._buy()
        far_future = timezone.now() + timedelta(days=365)
        session = self.make_session(
            status_=ClassSession.Status.COMPLETED,
            scheduled_start=far_future,
            actual_end=far_future,
        )
        result = purchase.charge_for_session(session)
        purchase.refresh_from_db()

        self.assertIsNone(result)
        self.assertEqual(purchase.coins_released, 0)

    def test_session_for_different_classroom_is_not_charged(self):
        purchase = self._buy()
        other_session = self.make_session(
            classroom=self.other_classroom, status_=ClassSession.Status.COMPLETED, actual_end=timezone.now()
        )
        result = purchase.charge_for_session(other_session)
        self.assertIsNone(result)

    def test_fully_released_escrow_charges_nothing_further(self):
        purchase = self._buy()
        # Manually exhaust the escrow.
        purchase.coins_released = purchase.coins_spent
        purchase.save(update_fields=["coins_released"])

        session = self.make_session(status_=ClassSession.Status.COMPLETED, actual_end=timezone.now())
        result = purchase.charge_for_session(session)
        self.assertIsNone(result)

    def test_final_day_absorbs_rounding_remainder(self):
        # 3-day pass, 10 coins -> per_day_rate = 3.333..., so day1+day2
        # round to 3 each (6 released), leaving 4 for the "final" day
        # instead of stranding a fractional coin.
        small_pass = ClassPass.objects.create(
            classroom=self.classroom,
            pass_type=ClassPass.PassType.DAILY,
            price=Decimal("10"),
            validity_days=3,
        )
        purchase = _charge_and_create_purchase(self.student, small_pass, coupon_code="")
        self.assertEqual(purchase.per_day_rate, Decimal("10") / 3)

        base = timezone.now()
        for i in range(3):
            day = self.make_session(
                status_=ClassSession.Status.COMPLETED,
                scheduled_start=base + timedelta(days=i),
                actual_end=base + timedelta(days=i),
            )
            purchase.charge_for_session(day)

        purchase.refresh_from_db()
        self.assertEqual(purchase.coins_released, 10)  # nothing stranded
        self.assertEqual(purchase.remaining_balance, 0)

    def test_sync_missed_charges_catches_up_completed_sessions(self):
        purchase = self._buy()
        base = timezone.now()
        for i in range(3):
            self.make_session(
                status_=ClassSession.Status.COMPLETED,
                scheduled_start=base + timedelta(days=i),
                actual_end=base + timedelta(days=i),
            )
        charged = purchase.sync_missed_charges()
        purchase.refresh_from_db()

        self.assertEqual(charged, 3)
        self.assertEqual(purchase.coins_released, 30)

    def test_sync_missed_charges_is_idempotent_with_signal_already_having_charged(self):
        purchase = self._buy()
        session = self.make_session(status_=ClassSession.Status.COMPLETED, actual_end=timezone.now())
        purchase.charge_for_session(session)  # simulate the post_save signal

        charged = purchase.sync_missed_charges()
        purchase.refresh_from_db()

        self.assertEqual(charged, 0)  # nothing NEW charged
        self.assertEqual(purchase.coins_released, 10)


# ===========================================================================
# 4. PassPurchase.reverse() — refund math, no teacher clawback
# ===========================================================================
class ReverseRefundTests(LiveClassTestBase):
    def _buy(self, coupon_code=""):
        return _charge_and_create_purchase(self.student, self.class_pass, coupon_code=coupon_code)

    def test_reverse_before_any_class_taught_refunds_full_amount(self):
        purchase = self._buy()
        purchase.reverse(notify=False)
        self.student.refresh_from_db()

        self.assertEqual(purchase.status, PassPurchase.Status.REFUNDED)
        self.assertFalse(purchase.is_active)
        self.assertEqual(self.student.coin, 1000)  # full 100 back

        txn = CoinTransaction.objects.filter(user=self.student, reason=CoinTransaction.Reason.REFUND).first()
        self.assertIsNotNone(txn)
        self.assertEqual(txn.amount, 100)

    def test_reverse_after_some_days_taught_refunds_only_remaining_balance(self):
        purchase = self._buy()
        session = self.make_session(status_=ClassSession.Status.COMPLETED, actual_end=timezone.now())
        purchase.charge_for_session(session)  # teacher earns 10

        purchase.reverse(notify=False)
        self.student.refresh_from_db()
        self.teacher.refresh_from_db()

        # Student started with 1000, paid 100 (-> 900), gets back
        # remaining_balance = 90 -> 990. The 10 the teacher already earned
        # is NOT clawed back.
        self.assertEqual(self.student.coin, 990)
        self.assertEqual(self.teacher.coin, 10)

    def test_reverse_never_refunds_more_than_remaining_balance(self):
        purchase = self._buy()
        purchase.coins_released = purchase.coins_spent  # fully earned out
        purchase.save(update_fields=["coins_released"])

        purchase.reverse(notify=False)
        self.student.refresh_from_db()

        self.assertEqual(self.student.coin, 900)  # no refund at all
        self.assertFalse(CoinTransaction.objects.filter(reason=CoinTransaction.Reason.REFUND).exists())

    def test_coupon_slot_released_when_no_day_was_ever_charged(self):
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            classroom=self.classroom,
            code="GIVEBACK",
            discount_percent=10,
            valid_until=timezone.now() + timedelta(days=5),
        )
        purchase = self._buy(coupon_code="GIVEBACK")
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 1)

        purchase.reverse(notify=False)
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 0)

    def test_coupon_slot_kept_once_any_day_has_been_charged(self):
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            classroom=self.classroom,
            code="KEEPIT",
            discount_percent=10,
            valid_until=timezone.now() + timedelta(days=5),
        )
        purchase = self._buy(coupon_code="KEEPIT")
        session = self.make_session(status_=ClassSession.Status.COMPLETED, actual_end=timezone.now())
        purchase.charge_for_session(session)

        purchase.reverse(notify=False)
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 1)  # NOT given back

    def test_coupon_used_count_never_goes_negative(self):
        coupon = Coupon.objects.create(
            created_by=self.teacher,
            classroom=self.classroom,
            code="ZEROFLOOR",
            discount_percent=10,
            valid_until=timezone.now() + timedelta(days=5),
            used_count=0,
        )
        purchase = self._buy(coupon_code="ZEROFLOOR")
        # Simulate used_count having drifted to 0 already through some
        # other path before reverse() runs.
        Coupon.objects.filter(pk=coupon.pk).update(used_count=0)
        purchase.reverse(notify=False)
        coupon.refresh_from_db()
        self.assertGreaterEqual(coupon.used_count, 0)

    @patch("liveclass.tasks.notify_purchase_refunded.delay")
    def test_reverse_queues_refund_notification_by_default(self, mock_delay):
        purchase = self._buy()
        purchase.reverse()  # notify=True (default)
        mock_delay.assert_called_once_with(purchase.id)

    @patch("liveclass.tasks.notify_purchase_refunded.delay")
    def test_reverse_with_notify_false_skips_notification(self, mock_delay):
        purchase = self._buy()
        purchase.reverse(notify=False)
        mock_delay.assert_not_called()


# ===========================================================================
# 5. HTTP layer — join-request accept(), pass-purchase cancel()/refund()
# ===========================================================================
@TEST_REST_FRAMEWORK_THROTTLES
class JoinRequestAcceptViewTests(LiveClassTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.student)
        self.join_request = ClassJoinRequest.objects.create(
            classroom=self.classroom, class_pass=self.class_pass, student=self.student
        )

    def test_accept_charges_student_and_grants_access(self):
        self.client.force_authenticate(self.teacher)
        url = reverse("classjoinrequest-accept", args=[self.join_request.pk])
        resp = self.client.post(url, {"note": "welcome"}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.join_request.refresh_from_db()
        self.student.refresh_from_db()

        self.assertEqual(self.join_request.status, ClassJoinRequest.Status.ACCEPTED)
        self.assertIsNotNone(self.join_request.pass_purchase)
        self.assertEqual(self.student.coin, 900)
        self.assertTrue(self.classroom.has_access(self.student))

    def test_accept_by_non_manager_is_forbidden(self):
        random_user = User.objects.create_user(username="rando", password="pass12345")
        self.client.force_authenticate(random_user)
        url = reverse("classjoinrequest-accept", args=[self.join_request.pk])
        resp = self.client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_accept_twice_is_rejected(self):
        self.client.force_authenticate(self.teacher)
        url = reverse("classjoinrequest-accept", args=[self.join_request.pk])
        self.client.post(url, {}, format="json")
        resp = self.client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_accept_with_insufficient_balance_leaves_request_pending(self):
        self.student.coin = 10
        self.student.save(update_fields=["coin"])
        self.client.force_authenticate(self.teacher)
        url = reverse("classjoinrequest-accept", args=[self.join_request.pk])
        resp = self.client.post(url, {}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.join_request.refresh_from_db()
        self.assertEqual(self.join_request.status, ClassJoinRequest.Status.PENDING)

    def test_accept_against_closed_classroom_is_rejected(self):
        self.classroom.is_active = False
        self.classroom.save(update_fields=["is_active"])
        self.client.force_authenticate(self.teacher)
        url = reverse("classjoinrequest-accept", args=[self.join_request.pk])
        resp = self.client.post(url, {}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.student.refresh_from_db()
        self.assertEqual(self.student.coin, 1000)  # never charged


@TEST_REST_FRAMEWORK_THROTTLES
class PassPurchaseCancelRefundViewTests(LiveClassTestBase):
    def setUp(self):
        super().setUp()
        self.purchase = _charge_and_create_purchase(self.student, self.class_pass, coupon_code="")

    def test_student_can_cancel_own_purchase(self):
        client = APIClient()
        client.force_authenticate(self.student)
        url = reverse("passpurchase-cancel", args=[self.purchase.pk])
        resp = client.post(url, {}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PassPurchase.Status.REFUNDED)

    def test_student_cannot_cancel_someone_elses_purchase(self):
        other_student = User.objects.create_user(username="other_student", password="pass12345")
        client = APIClient()
        client.force_authenticate(other_student)
        url = reverse("passpurchase-cancel", args=[self.purchase.pk])
        resp = client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_teacher_can_refund_a_students_purchase(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        url = reverse("passpurchase-refund", args=[self.purchase.pk])
        resp = client.post(url, {}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PassPurchase.Status.REFUNDED)

    def test_student_cannot_use_refund_action_on_self(self):
        client = APIClient()
        client.force_authenticate(self.student)
        url = reverse("passpurchase-refund", args=[self.purchase.pk])
        resp = client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_cancel_already_refunded_purchase(self):
        client = APIClient()
        client.force_authenticate(self.student)
        url = reverse("passpurchase-cancel", args=[self.purchase.pk])
        client.post(url, {}, format="json")
        resp = client.post(url, {}, format="json")  # second call
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class ClassroomCloseTests(LiveClassTestBase):
    def setUp(self):
        super().setUp()
        self.purchase = _charge_and_create_purchase(self.student, self.class_pass, coupon_code="")

    def test_close_refunds_every_active_purchase(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        url = reverse("classroom-close", args=[self.classroom.pk])
        resp = client.post(url, {}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["passes_refunded"], 1)

        self.purchase.refresh_from_db()
        self.classroom.refresh_from_db()
        self.student.refresh_from_db()

        self.assertEqual(self.purchase.status, PassPurchase.Status.REFUNDED)
        self.assertFalse(self.classroom.is_active)
        self.assertEqual(self.student.coin, 1000)

    def test_close_by_non_teacher_is_forbidden(self):
        client = APIClient()
        client.force_authenticate(self.student)
        url = reverse("classroom-close", args=[self.classroom.pk])
        resp = client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


# ===========================================================================
# 6. Session waitlist — overflow, auto-promotion, manual promote()
# ===========================================================================
class WaitlistTests(LiveClassTestBase):
    """self.classroom.max_participants = 2 (see base fixture)."""

    def setUp(self):
        super().setUp()
        self.student2 = User.objects.create_user(username="student2", password="pass12345")
        self.student3 = User.objects.create_user(username="student3", password="pass12345")
        for s in (self.student2, self.student3):
            s.coin = 1000
            s.save(update_fields=["coin"])

        # Give every student an active pass so has_access() passes for all.
        self.purchase1 = _charge_and_create_purchase(self.student, self.class_pass, coupon_code="")
        self.purchase2 = _charge_and_create_purchase(self.student2, self.class_pass, coupon_code="")
        self.purchase3 = _charge_and_create_purchase(self.student3, self.class_pass, coupon_code="")

        self.session = self.make_session(status_=ClassSession.Status.SCHEDULED)

    def _join(self, user):
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("classsession-join", args=[self.session.pk])
        return client.post(url, {}, format="json")

    def test_join_beyond_capacity_is_waitlisted(self):
        self._join(self.student)   # seat 1
        self._join(self.student2)  # seat 2 (max_participants=2)
        resp = self._join(self.student3)  # overflow

        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(
            SessionWaitlist.objects.filter(session=self.session, student=self.student3).exists()
        )
        self.assertEqual(
            SessionParticipant.objects.filter(session=self.session, left_at__isnull=True).count(), 2
        )

    def test_waitlist_join_is_idempotent(self):
        self._join(self.student)
        self._join(self.student2)
        self._join(self.student3)
        self._join(self.student3)  # duplicate waitlist attempt
        self.assertEqual(
            SessionWaitlist.objects.filter(session=self.session, student=self.student3).count(), 1
        )

    def test_leave_auto_promotes_next_waitlisted_student(self):
        self._join(self.student)
        self._join(self.student2)
        self._join(self.student3)  # waitlisted

        participant1 = SessionParticipant.objects.get(session=self.session, user=self.student)
        client = APIClient()
        client.force_authenticate(self.student)
        url = reverse("sessionparticipant-leave", args=[participant1.pk])
        resp = client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.assertFalse(SessionWaitlist.objects.filter(session=self.session).exists())
        self.assertTrue(
            SessionParticipant.objects.filter(
                session=self.session, user=self.student3, left_at__isnull=True
            ).exists()
        )

    def test_promotion_respects_first_come_first_served_order(self):
        self._join(self.student)
        self._join(self.student2)

        student4 = User.objects.create_user(username="student4", password="pass12345")
        student4.coin = 1000
        student4.save(update_fields=["coin"])
        _charge_and_create_purchase(student4, self.class_pass, coupon_code="")

        # student3 waitlists first, then student4.
        self._join(self.student3)
        self._join(student4)

        participant1 = SessionParticipant.objects.get(session=self.session, user=self.student)
        client = APIClient()
        client.force_authenticate(self.student)
        url = reverse("sessionparticipant-leave", args=[participant1.pk])
        client.post(url, {}, format="json")

        self.assertTrue(
            SessionParticipant.objects.filter(
                session=self.session, user=self.student3, left_at__isnull=True
            ).exists()
        )
        self.assertFalse(
            SessionParticipant.objects.filter(
                session=self.session, user=student4, left_at__isnull=True
            ).exists()
        )
        self.assertTrue(SessionWaitlist.objects.filter(session=self.session, student=student4).exists())

    def test_kicked_student_is_never_auto_promoted(self):
        self._join(self.student)
        self._join(self.student2)
        self._join(self.student3)  # waitlisted

        # Kick student3 from this same session's context while still on
        # the waitlist (simulate: they were kicked earlier this session).
        SessionParticipant.objects.create(
            session=self.session, user=self.student3, role=SessionParticipant.Role.STUDENT,
            left_at=timezone.now(), kicked_at=timezone.now(),
        )

        participant1 = SessionParticipant.objects.get(
            session=self.session, user=self.student, left_at__isnull=True
        )
        client = APIClient()
        client.force_authenticate(self.student)
        url = reverse("sessionparticipant-leave", args=[participant1.pk])
        client.post(url, {}, format="json")

        self.assertFalse(
            SessionParticipant.objects.filter(
                session=self.session, user=self.student3, left_at__isnull=True
            ).exists()
        )
        # The stale waitlist entry for the kicked student is dropped, not
        # left behind to block the next legitimate student forever.
        self.assertFalse(SessionWaitlist.objects.filter(session=self.session, student=self.student3).exists())

    @TEST_REST_FRAMEWORK_THROTTLES
    def test_manual_promote_is_teacher_only(self):
        self._join(self.student)
        self._join(self.student2)
        self._join(self.student3)  # waitlisted
        entry = SessionWaitlist.objects.get(session=self.session, student=self.student3)

        client = APIClient()
        client.force_authenticate(self.student2)  # a student, not staff
        url = reverse("sessionwaitlist-promote", args=[entry.pk])
        resp = client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        client.force_authenticate(self.teacher)
        resp = client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(
            SessionParticipant.objects.filter(
                session=self.session, user=self.student3, left_at__isnull=True
            ).exists()
        )

    def test_no_promotion_when_waitlist_is_empty(self):
        self._join(self.student)
        participant1 = SessionParticipant.objects.get(session=self.session, user=self.student)
        # Should simply no-op, not error.
        _try_promote_from_waitlist(self.session)
        client = APIClient()
        client.force_authenticate(self.student)
        url = reverse("sessionparticipant-leave", args=[participant1.pk])
        resp = client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

# ===========================================================================
# 7. Classroom-wide ban — permanent ban blocks join/access, refunds active
#    passes, and is reversible via unban()
# ===========================================================================
class ClassroomBanTests(LiveClassTestBase):
    def _ban(self, actor, classroom, student, reason=""):
        client = APIClient()
        client.force_authenticate(actor)
        url = reverse("classroom-ban", args=[classroom.pk])
        return client.post(url, {"student_id": student.id, "reason": reason}, format="json")

    def test_teacher_can_ban_student(self):
        resp = self._ban(self.teacher, self.classroom, self.student, reason="Disruptive in chat")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            ClassroomBan.objects.filter(classroom=self.classroom, student=self.student).exists()
        )

    def test_non_manager_cannot_ban(self):
        other_student = User.objects.create_user(username="student9", password="pass12345")
        resp = self._ban(other_student, self.classroom, self.student)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(ClassroomBan.objects.filter(classroom=self.classroom).exists())

    def test_teacher_cannot_ban_self(self):
        resp = self._ban(self.teacher, self.classroom, self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_ban_is_idempotent(self):
        first = self._ban(self.teacher, self.classroom, self.student, reason="first")
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = self._ban(self.teacher, self.classroom, self.student, reason="second attempt")
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(ClassroomBan.objects.filter(classroom=self.classroom, student=self.student).count(), 1)

    def test_ban_refunds_active_purchase_and_rejects_pending_join_request(self):
        purchase = _charge_and_create_purchase(self.student, self.class_pass, coupon_code="")
        self.assertEqual(purchase.status, PassPurchase.Status.SUCCESS)
        student_before = User.objects.get(pk=self.student.pk).coin

        pending_request = ClassJoinRequest.objects.create(
            classroom=self.classroom,
            student=self.student,
            class_pass=self.class_pass,
            status=ClassJoinRequest.Status.PENDING,
        )

        resp = self._ban(self.teacher, self.classroom, self.student)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        purchase.refresh_from_db()
        self.assertFalse(purchase.is_active)
        student_after = User.objects.get(pk=self.student.pk).coin
        self.assertGreater(student_after, student_before)

        pending_request.refresh_from_db()
        self.assertEqual(pending_request.status, ClassJoinRequest.Status.REJECTED)

    def test_banned_student_cannot_raise_join_request(self):
        ClassroomBan.objects.create(classroom=self.classroom, student=self.student, banned_by=self.teacher)
        client = APIClient()
        client.force_authenticate(self.student)
        url = reverse("classjoinrequest-list")
        resp = client.post(
            url, {"classroom": self.classroom.pk, "class_pass": self.class_pass.pk}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_banned_student_cannot_join_live_session(self):
        _charge_and_create_purchase(self.student, self.class_pass, coupon_code="")
        ClassroomBan.objects.create(classroom=self.classroom, student=self.student, banned_by=self.teacher)
        session = self.make_session(status_=ClassSession.Status.SCHEDULED)

        client = APIClient()
        client.force_authenticate(self.student)
        url = reverse("classsession-join", args=[session.pk])
        resp = client.post(url, {}, format="json")
        self.assertIn(resp.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_400_BAD_REQUEST))

    def test_bans_list_is_manager_only(self):
        ClassroomBan.objects.create(classroom=self.classroom, student=self.student, banned_by=self.teacher)
        client = APIClient()
        client.force_authenticate(self.student)
        url = reverse("classroom-bans", args=[self.classroom.pk])
        resp = client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        client.force_authenticate(self.teacher)
        resp = client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)

    def test_unban_lifts_the_ban(self):
        self._ban(self.teacher, self.classroom, self.student)
        client = APIClient()
        client.force_authenticate(self.teacher)
        url = reverse("classroom-unban", args=[self.classroom.pk, self.student.pk])
        resp = client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(
            ClassroomBan.objects.filter(classroom=self.classroom, student=self.student).exists()
        )

    def test_unban_missing_ban_returns_404(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        url = reverse("classroom-unban", args=[self.classroom.pk, self.student.pk])
        resp = client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ===========================================================================
# 8. Referral redemption — one-time, new-account-only, credits both wallets
# ===========================================================================
class ReferralRedeemTests(LiveClassTestBase):
    def setUp(self):
        super().setUp()
        # A separate pair from the shared teacher/student fixtures so the
        # redeem-window / coin-crediting math below isn't entangled with
        # any pass-purchase coin spend done elsewhere in the base fixture.
        self.referrer = User.objects.create_user(username="referrer1", password="pass12345")
        self.newcomer = User.objects.create_user(username="newcomer1", password="pass12345")

    def _redeem(self, user, code):
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("referral-redeem")
        return client.post(url, {"code": code}, format="json")

    def test_valid_code_credits_both_wallets(self):
        code = referral_code_for_user(self.referrer.id)
        referrer_before = self.referrer.coin
        newcomer_before = self.newcomer.coin

        resp = self._redeem(self.newcomer, code)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        bonus = django_settings.REFERRAL_BONUS_COINS
        self.referrer.refresh_from_db()
        self.newcomer.refresh_from_db()
        self.assertEqual(self.referrer.coin, referrer_before + bonus)
        self.assertEqual(self.newcomer.coin, newcomer_before + bonus)
        self.assertTrue(
            Referral.objects.filter(referrer=self.referrer, referred=self.newcomer, bonus_amount=bonus).exists()
        )
        self.assertEqual(
            CoinTransaction.objects.filter(
                user__in=[self.referrer, self.newcomer], reason=CoinTransaction.Reason.REFERRAL_BONUS
            ).count(),
            2,
        )

    def test_invalid_code_is_rejected(self):
        resp = self._redeem(self.newcomer, "not-a-real-code")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Referral.objects.exists())

    def test_cannot_redeem_own_code(self):
        code = referral_code_for_user(self.referrer.id)
        resp = self._redeem(self.referrer, code)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_redeem_twice(self):
        code = referral_code_for_user(self.referrer.id)
        first = self._redeem(self.newcomer, code)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second_referrer = User.objects.create_user(username="referrer2", password="pass12345")
        second_code = referral_code_for_user(second_referrer.id)
        second = self._redeem(self.newcomer, second_code)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Referral.objects.filter(referred=self.newcomer).count(), 1)

    def test_redeem_outside_window_is_rejected(self):
        window = django_settings.REFERRAL_REDEEM_WINDOW_DAYS
        self.newcomer.date_joined = timezone.now() - timedelta(days=window + 1)
        self.newcomer.save(update_fields=["date_joined"])

        code = referral_code_for_user(self.referrer.id)
        resp = self._redeem(self.newcomer, code)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Referral.objects.exists())

    def test_my_code_reflects_referral_count_and_earnings(self):
        code = referral_code_for_user(self.referrer.id)
        self._redeem(self.newcomer, code)

        client = APIClient()
        client.force_authenticate(self.referrer)
        url = reverse("referral-my-code")
        resp = client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["code"], code)
        self.assertEqual(resp.data["referral_count"], 1)
        self.assertEqual(resp.data["total_bonus_earned"], django_settings.REFERRAL_BONUS_COINS)

    def test_referral_list_is_own_ledger_only(self):
        code = referral_code_for_user(self.referrer.id)
        self._redeem(self.newcomer, code)

        client = APIClient()
        client.force_authenticate(self.newcomer)  # the referred user, not the referrer
        url = reverse("referral-list")
        resp = client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        self.assertEqual(len(results), 0)


# ===========================================================================
# 9. Teacher earnings dashboard — aggregates PassDailyCharge, not
#    CoinTransaction (see TeacherEarningsView docstring in views.py)
# ===========================================================================
class TeacherEarningsTests(LiveClassTestBase):
    def setUp(self):
        super().setUp()
        self.purchase = _charge_and_create_purchase(self.student, self.class_pass, coupon_code="")
        today = timezone.now().date()
        # Two charges within the last 30 days (this classroom), one older
        # charge outside the 30-day daily-breakdown window but still
        # inside the lifetime total, and one charge on an unrelated
        # classroom/teacher that must never leak into this teacher's sum.
        PassDailyCharge.objects.create(purchase=self.purchase, date=today, amount=10)
        PassDailyCharge.objects.create(purchase=self.purchase, date=today - timedelta(days=1), amount=10)
        PassDailyCharge.objects.create(purchase=self.purchase, date=today - timedelta(days=40), amount=10)

        other_pass = ClassPass.objects.create(
            classroom=self.other_classroom,
            pass_type=ClassPass.PassType.MONTHLY,
            price=Decimal("50"),
            validity_days=10,
        )
        other_student = User.objects.create_user(username="student_other", password="pass12345")
        other_student.coin = 1000
        other_student.save(update_fields=["coin"])
        other_purchase = _charge_and_create_purchase(other_student, other_pass, coupon_code="")
        PassDailyCharge.objects.create(purchase=other_purchase, date=today, amount=999)

    def _get(self, user, classroom=None):
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("teacher-earnings")
        if classroom is not None:
            url = f"{url}?classroom={classroom.pk}"
        return client.get(url)

    def test_totals_scoped_to_own_classrooms_only(self):
        resp = self._get(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["total_earned"], 30)
        self.assertEqual(resp.data["total_sessions_charged"], 3)

    def test_other_teachers_earnings_never_leak_in(self):
        resp = self._get(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # 999 belongs to other_classroom's teacher, not self.teacher.
        self.assertNotEqual(resp.data["total_earned"], 999)
        self.assertLess(resp.data["total_earned"], 999)

    def test_classroom_scoping_requires_ownership(self):
        resp = self._get(self.teacher, classroom=self.other_classroom)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_classroom_scoped_totals(self):
        resp = self._get(self.teacher, classroom=self.classroom)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["total_earned"], 30)

    def test_daily_breakdown_excludes_charges_older_than_30_days(self):
        resp = self._get(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        daily_total = sum(day["amount"] for day in resp.data["daily"])
        self.assertEqual(daily_total, 20)  # excludes the 40-day-old charge


# ===========================================================================
# 10. Classroom recordings library — browsable past-recordings list
# ===========================================================================
class ClassroomRecordingsTests(LiveClassTestBase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.recorded_session = self.make_session(
            status_=ClassSession.Status.COMPLETED,
            scheduled_start=now - timedelta(days=1),
            scheduled_end=now - timedelta(days=1) + timedelta(hours=1),
            actual_end=now - timedelta(days=1) + timedelta(hours=1),
            recording_url="https://cdn.example.com/recordings/abc123.mp4",
        )
        # A completed session with no recording must never show up here.
        self.make_session(
            status_=ClassSession.Status.COMPLETED,
            scheduled_start=now - timedelta(days=2),
            scheduled_end=now - timedelta(days=2) + timedelta(hours=1),
            actual_end=now - timedelta(days=2) + timedelta(hours=1),
        )

    def _get(self, user):
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("classroom-recordings", args=[self.classroom.pk])
        return client.get(url)

    def test_teacher_sees_only_recorded_sessions(self):
        resp = self._get(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.recorded_session.pk)

    def test_enrolled_student_can_view_recordings(self):
        _charge_and_create_purchase(self.student, self.class_pass, coupon_code="")
        resp = self._get(self.student)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        self.assertEqual(len(results), 1)

    def test_student_without_access_is_forbidden(self):
        resp = self._get(self.student)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


# ===========================================================================
# 11. Classroom listing filters — ?min_price=/?max_price=/?min_rating=
# ===========================================================================
class ClassroomPriceRatingFilterTests(LiveClassTestBase):
    def setUp(self):
        super().setUp()
        # self.classroom already has a 100-coin pass (self.class_pass) and
        # a default rating_avg of 0 from the base fixture — override it and
        # add a second, differently-priced/rated classroom to filter against.
        self.classroom.rating_avg = Decimal("4.5")
        self.classroom.save(update_fields=["rating_avg"])

        self.budget_classroom = Classroom.objects.create(
            teacher=self.teacher, title="Budget Batch", rating_avg=Decimal("3.0")
        )
        ClassPass.objects.create(
            classroom=self.budget_classroom,
            pass_type=ClassPass.PassType.MONTHLY,
            price=Decimal("10"),
            validity_days=10,
        )

    def _list(self, user, **params):
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("classroom-list")
        return client.get(url, params)

    def test_min_rating_filters_out_lower_rated_classrooms(self):
        resp = self._list(self.student, min_rating="4")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = {row["title"] for row in resp.data.get("results", resp.data)}
        self.assertIn(self.classroom.title, titles)
        self.assertNotIn(self.budget_classroom.title, titles)

    def test_max_price_filters_out_more_expensive_classrooms(self):
        resp = self._list(self.student, max_price="20")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = {row["title"] for row in resp.data.get("results", resp.data)}
        self.assertIn(self.budget_classroom.title, titles)
        self.assertNotIn(self.classroom.title, titles)

    def test_min_price_filters_out_cheaper_classrooms(self):
        resp = self._list(self.student, min_price="50")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = {row["title"] for row in resp.data.get("results", resp.data)}
        self.assertIn(self.classroom.title, titles)
        self.assertNotIn(self.budget_classroom.title, titles)

    def test_price_range_matches_a_classroom_with_any_pass_in_range(self):
        resp = self._list(self.student, min_price="5", max_price="15")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = {row["title"] for row in resp.data.get("results", resp.data)}
        self.assertIn(self.budget_classroom.title, titles)
        self.assertNotIn(self.classroom.title, titles)

    def test_invalid_min_rating_is_ignored_not_a_400(self):
        resp = self._list(self.student, min_rating="not-a-number")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = {row["title"] for row in resp.data.get("results", resp.data)}
        self.assertIn(self.classroom.title, titles)
        self.assertIn(self.budget_classroom.title, titles)