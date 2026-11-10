# tuitionclass/models/passes.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

import logging
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)
from django.db import IntegrityError as DjangoIntegrityError
from django.db.models import F
from django.db.models.functions import Greatest
from django.core.validators import MinValueValidator
from login.models import User
from django.core.exceptions import ValidationError
from django.db import (
    models,
    transaction,
)
from django.utils import timezone
from .schedules import ClassSession
from .classroom import Classroom


# ---------------------------------------------------------------------------
# 4. PASS (pricing plan a teacher sets up for a classroom)
# ---------------------------------------------------------------------------
class ClassPass(models.Model):
    class PassType(models.TextChoices):
        FREE = "free", "Free"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="passes")

    pass_type = models.CharField(max_length=20, choices=PassType.choices)
    title = models.CharField(max_length=100, blank=True)  # e.g. "Weekend Batch - Monthly"

    price = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
        help_text="Price in coins (User.coin). 0 = free pass.",
    )
    validity_days = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="How many days this pass stays valid after purchase",
    )

    # optional cap: e.g. a "5-class pack"; null = unlimited within validity window
    max_classes = models.PositiveIntegerField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    # FIX (frontend cross-check — Pass 14 gifting): pass_management_screen.dart
    # (Dart) already ships a per-pass "Allow gifting" toggle and
    # PassGiftClaimScreen already lets a student receive a gifted pass, but
    # nothing on this model or PassGiftViewSet.perform_create ever actually
    # gated gifting by it — every active pass was giftable regardless of what
    # a teacher set here. Defaults to True (opt-out) so existing passes keep
    # today's de-facto "always giftable" behavior after this migrates.
    # NOTE: adding this field requires a migration
    # (`manage.py makemigrations tuitionclass`) before it takes effect.
    allow_gifting = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["price"]

    def __str__(self):
        return f"{self.classroom.title} - {self.get_pass_type_display()} ({self.price} coins)"


# ---------------------------------------------------------------------------
# 5. PASS PURCHASE (student's ownership of a pass -> gates access)
# ---------------------------------------------------------------------------
class PassPurchase(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    class PaymentMethod(models.TextChoices):
        # No "online"/card/UPI method — this platform runs on an internal coin
        # economy only. A pass is ALWAYS paid for out of User.coin, except
        # when the effective price is 0 (a free pass, or a coupon that zeroes
        # the price), in which case it's just marked FREE with no coin debit.
        COIN_WALLET = "coin_wallet", "Coin Wallet"
        FREE = "free", "Free"

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="pass_purchases")
    class_pass = models.ForeignKey(ClassPass, on_delete=models.CASCADE, related_name="purchases")
    coupon = models.ForeignKey(
        "Coupon", on_delete=models.SET_NULL, null=True, blank=True, related_name="redemptions"
    )

    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.COIN_WALLET
    )
    amount_paid = models.DecimalField(max_digits=8, decimal_places=2)
    coins_spent = models.PositiveIntegerField(default=0)
    transaction_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    purchased_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    classes_attended = models.PositiveIntegerField(default=0)  # for max_classes packs
    is_active = models.BooleanField(default=True)

    # -----------------------------------------------------------------
    # NOTE (fix — "pay only for classes actually held"): a pass used to be
    # settled in one shot — the full coins_spent left the student's wallet
    # AND landed in the teacher's wallet the moment ClassJoinRequestViewSet
    # .accept() ran (see _charge_and_create_purchase in views.py). That's
    # fine if the teacher runs every single class for the whole
    # validity_days window, but the moment they stop mid-way — go quiet
    # for the rest of a yearly pass, close the classroom, whatever — the
    # student has already paid for months of classes nobody ever held, and
    # getting that back depended entirely on a manual refund clawing coins
    # back OUT of a teacher who may well have already spent/withdrawn them
    # (see the clamp in the old _refund_purchase). Actual loss for the
    # student, not just a UX gap.
    #
    # Fixed by turning a pass into an escrow instead of a lump-sum
    # transfer: coins_spent still leaves the student's wallet up front
    # (unchanged — a coin sitting in the student's own wallet doesn't
    # protect them any better, since coupon/balance checks already ran
    # once at accept() time), but it is NOT credited to the teacher all at
    # once. It's released to the teacher one calendar day at a time, and
    # ONLY for a day this pass's classroom actually held a class (a
    # ClassSession that reached COMPLETED) — see charge_for_session()
    # below, fired by the post_save signal on ClassSession near the
    # bottom of this file. A day with no class (holiday, teacher no-show,
    # cancelled session) never charges anything, no matter how much
    # validity-day time passes. Whatever hasn't been released yet
    # (remaining_balance) is exactly what reverse() refunds — see below.
    #
    #   per_day_rate     — snapshot of coins_spent / validity_days at
    #                       purchase time (see _charge_and_create_purchase
    #                       in views.py). Frozen on the purchase itself so
    #                       a later price change to the ClassPass can
    #                       never retroactively change what an existing
    #                       purchase charges per day.
    #   coins_released    — running total already released to the
    #                       teacher, one PassDailyCharge row at a time.
    #                       Capped at coins_spent — see charge_for_session().
    #   last_charge_date  — last calendar date this purchase has been
    #                       charged up to (or attempted); lets
    #                       sync_missed_charges() report progress without
    #                       re-deriving it from PassDailyCharge each time.
    # -----------------------------------------------------------------
    per_day_rate = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    coins_released = models.PositiveIntegerField(default=0)
    last_charge_date = models.DateField(null=True, blank=True)
    # NOTE (visibility fix): once reverse() runs, remaining_balance drops to
    # 0 immediately (that coin has already moved to the student's wallet) —
    # so "how much did I actually get refunded?" became unanswerable from
    # the purchase row after the fact, only reconstructable by cross-
    # referencing CoinTransaction.reference_id. Snapshot it here instead so
    # a student's purchase-history card can show the number directly.
    refunded_amount = models.PositiveIntegerField(default=0)
    refunded_at = models.DateTimeField(null=True, blank=True)

    # -----------------------------------------------------------------
    # FEATURE (refer & earn): the same per-day escrow release that pays
    # the teacher (see charge_for_session below) also pays a commission
    # to whoever referred this student — IF they came in through a
    # referral link and the classroom had referrals on at accept()-time.
    # UPDATE: the commission is now deducted OUT OF the teacher's own
    # per-day release, never added on top by the platform — see
    # charge_for_session's teacher_amount/referral_amount split. It only
    # exists at all because the teacher opted in via
    # Classroom.referral_enabled and chose the cut themselves via
    # Classroom.referral_commission_percent.
    # Set once, at purchase-creation time, from ClassJoinRequest.referred_by
    # + Classroom.referral_commission_percent — see
    # _charge_and_create_purchase in views.py.
    #
    #   referred_by                 — who gets the commission. Null =
    #                                  no referral on this purchase.
    #   referral_commission_percent — snapshot of the classroom's rate at
    #                                  purchase time, same "frozen so a
    #                                  later settings change never
    #                                  retroactively changes an existing
    #                                  purchase" reasoning as per_day_rate.
    #   referral_per_day_rate       — per_day_rate's cut, at that snapshot
    #                                  percent, pre-computed so
    #                                  charge_for_session doesn't have to
    #                                  redo the percentage math every day.
    #   referral_coins_released     — running total already paid to the
    #                                  referrer, mirrors coins_released.
    # -----------------------------------------------------------------
    referred_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="referred_purchases",
    )
    referral_commission_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    referral_per_day_rate = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    referral_coins_released = models.PositiveIntegerField(default=0)

    # -----------------------------------------------------------------
    # NEW (Pass 15 — auto-renew passes, subscription-style recurring
    # access). Off by default — a student opts IN per purchase via
    # PassPurchaseViewSet.toggle_auto_renew. When on, and this purchase
    # expires, renew() (below) is expected to be called by a scheduled
    # sweep (e.g. tasks.run_auto_renewals, checking `auto_renew=True,
    # status=SUCCESS, expires_at__lte=now`) to buy a fresh PassPurchase
    # for the SAME class_pass/student, chained via renewed_into/
    # renewed_from so a student's purchase history reads as one
    # continuous subscription rather than a series of unrelated buys.
    #
    #   auto_renew     — the student's own opt-in switch. Cleared
    #                     automatically by renew() if a renewal attempt
    #                     ever fails for insufficient balance (never
    #                     silently retried forever — see renew()).
    #   renewed_from    — the purchase this one was auto-renewed FROM, if
    #                     any. Null for a purchase that was bought
    #                     directly (join-request accept, gift claim).
    #   renewal_failed_at — set (and auto_renew cleared) the moment a
    #                     renewal attempt fails for lack of coins, so the
    #                     app can prompt the student to top up instead of
    #                     silently losing access with no explanation.
    # -----------------------------------------------------------------
    auto_renew = models.BooleanField(default=False)
    renewed_from = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="renewed_into"
    )
    renewal_failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-purchased_at"]
        indexes = [
            models.Index(fields=["student", "status", "expires_at"]),
            models.Index(fields=["referred_by"]),
            # NEW (Pass 15): the exact shape tasks.run_auto_renewals sweeps
            # — every SUCCESS purchase with auto_renew on, ordered by
            # who's expiring soonest.
            models.Index(fields=["auto_renew", "status", "expires_at"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.class_pass} (expires {self.expires_at:%d-%b-%Y})"

    def is_valid(self) -> bool:
        if self.status != self.Status.SUCCESS or not self.is_active:
            return False
        if timezone.now() > self.expires_at:
            return False
        if self.class_pass.max_classes and self.classes_attended >= self.class_pass.max_classes:
            return False
        return True

    @property
    def remaining_balance(self) -> int:
        """Coins still sitting in escrow for this purchase — already
        debited from the student, not yet released to the teacher. This
        is exactly what reverse() below refunds, and it's what makes
        stopping mid-pass safe for the student: the teacher only ever
        walks away with what they've actually earned one class at a time
        (coins_released), never with days nobody taught."""
        return max(int(self.coins_spent) - int(self.coins_released), 0)

    @property
    def referral_total_amount(self) -> int:
        """Lifetime commission cap for this purchase's referral — the same
        proportion of coins_spent that referral_per_day_rate is of
        per_day_rate, so the referrer's total never exceeds
        referral_commission_percent of what the student actually paid,
        no matter how many days end up being charged."""
        if not self.referred_by_id or not self.referral_commission_percent:
            return 0
        return int(
            (Decimal(self.coins_spent) * self.referral_commission_percent / 100)
            .to_integral_value(rounding=ROUND_HALF_UP)
        )

    @property
    def referral_remaining_balance(self) -> int:
        """Mirrors remaining_balance, but for the referral commission
        escrow — how much is still left to release to the referrer."""
        return max(self.referral_total_amount - int(self.referral_coins_released), 0)

    def charge_for_session(self, session: "ClassSession") -> "PassDailyCharge | None":
        """Release this purchase's per-day charge to the teacher for the
        calendar date `session` (a COMPLETED session of this purchase's
        own classroom) falls on — but only once per date, and only if
        that date is within this purchase's validity window and there's
        still balance left to release.

        Idempotent by design: PassDailyCharge has a unique (purchase,
        date) constraint, so calling this twice for the same date (e.g.
        the post_save signal firing again on an unrelated field update
        while status is already COMPLETED, or sync_missed_charges()
        overlapping the signal) safely no-ops the second time via
        get_or_create rather than double-charging.

        Must be called with this purchase already row-locked
        (select_for_update) by the caller, inside transaction.atomic() —
        see _charge_passes_for_completed_session below.
        """
        if self.status != self.Status.SUCCESS or not self.is_active:
            return None
        if self.class_pass.classroom_id != session.classroom_id:
            return None

        charge_date = timezone.localtime(session.actual_end or session.scheduled_start).date()
        purchase_start = timezone.localtime(self.purchased_at).date()
        purchase_end = timezone.localtime(self.expires_at).date()
        if not (purchase_start <= charge_date <= purchase_end):
            return None  # session fell outside this pass's validity window

        remaining = self.remaining_balance
        if remaining <= 0:
            return None  # fully released already — nothing left in escrow

        charge, created = PassDailyCharge.objects.get_or_create(
            purchase=self,
            date=charge_date,
            defaults={
                "session": session,
                # Last chargeable day may leave a fractional per_day_rate
                # remainder (validity_days doesn't divide coins_spent
                # evenly) — hand the escrow's full remainder to the
                # teacher on that final day instead of stranding a coin or
                # two in limbo forever.
                "amount": min(
                    int(self.per_day_rate.to_integral_value(rounding="ROUND_HALF_UP")) or 1,
                    remaining,
                ),
            },
        )
        if not created:
            return charge  # already charged for this date — no-op

        # -------------------------------------------------------------
        # FEATURE (refer & earn — UPDATE: teacher-funded commission, not
        # platform-funded): the referrer's cut is now computed FIRST and
        # taken OUT of charge.amount before the teacher is paid — the
        # platform never tops this up out of its own pocket. The teacher
        # is the one who opted into referral_enabled and chose
        # referral_commission_percent for their own classroom (see
        # Classroom.referral_enabled/referral_commission_percent — teacher-
        # only, gated by ClassroomViewSet.perform_update), so it's the
        # teacher's own escrow release that funds the commission, same as
        # any real-world referral/affiliate arrangement a business owner
        # sets up out of their own margin.
        # Capped so a teacher's day can never go negative: referral_amount
        # can take at most charge.amount, even if referral_per_day_rate
        # would technically allow more (can only happen from a rounding
        # edge on the very last, fractional-remainder day — see the
        # get_or_create defaults above).
        # -------------------------------------------------------------
        referral_amount = 0
        if self.referred_by_id and self.referral_remaining_balance > 0:
            referral_amount = min(
                int(self.referral_per_day_rate.to_integral_value(rounding="ROUND_HALF_UP")),
                self.referral_remaining_balance,
                charge.amount,
            )

        teacher_amount = charge.amount - referral_amount

        teacher = type(self.class_pass.classroom.teacher).objects.select_for_update().get(
            pk=self.class_pass.classroom.teacher_id
        )
        if teacher_amount > 0:
            teacher.coin += teacher_amount
            teacher.save(update_fields=["coin"])
            CoinTransaction.objects.create(
                user=teacher,
                txn_type=CoinTransaction.TxnType.CREDIT,
                reason=CoinTransaction.Reason.CLASS_EARNING,
                amount=teacher_amount,
                balance_after=teacher.coin,
                reference_id=f"passpurchase:{self.id}:day:{charge_date.isoformat()}",
            )

        self.coins_released = min(self.coins_released + charge.amount, self.coins_spent)
        self.last_charge_date = charge_date
        update_fields = ["coins_released", "last_charge_date"]

        if referral_amount > 0:
            referrer = type(self.referred_by).objects.select_for_update().get(pk=self.referred_by_id)
            referrer.coin += referral_amount
            referrer.save(update_fields=["coin"])
            CoinTransaction.objects.create(
                user=referrer,
                txn_type=CoinTransaction.TxnType.CREDIT,
                reason=CoinTransaction.Reason.CLASS_REFERRAL_COMMISSION,
                amount=referral_amount,
                balance_after=referrer.coin,
                reference_id=f"passpurchase:{self.id}:referral:day:{charge_date.isoformat()}",
            )
            self.referral_coins_released = min(
                self.referral_coins_released + referral_amount, self.referral_total_amount
            )
            update_fields.append("referral_coins_released")
            charge.referral_amount = referral_amount
            charge.save(update_fields=["referral_amount"])
            # TASK 12: mirror the payout into the referral commission ledger
            # (same reference string as the CoinTransaction above -> idempotent).
            ReferralCommission.objects.get_or_create(
                reference=f"passpurchase:{self.id}:referral:day:{charge_date.isoformat()}",
                defaults={
                    "referrer_id": self.referred_by_id,
                    "referee_id": self.student_id,
                    "kind": ReferralCommission.Kind.CLASSROOM,
                    "source_id": str(self.class_pass.classroom_id),
                    "gross_coins": charge.amount,
                    "percent": self.referral_commission_percent,
                    "commission_coins": referral_amount,
                    "status": ReferralCommission.Status.PAID,
                },
            )
            ReferralAttribution.mark_converted(self.student)

        self.save(update_fields=update_fields)
        return charge

    def sync_missed_charges(self) -> int:
        """Catch-up sweep: charge this purchase for every COMPLETED
        session of its classroom that falls inside its validity window
        and hasn't been charged yet. The post_save signal on ClassSession
        (below) is what normally does this in real time the moment a
        class ends; this exists as a safety net for whatever that signal
        might have missed (a stuck task, a session marked COMPLETED by
        some other path), and is called opportunistically whenever a
        student actually shows up (see ClassSessionViewSet.join in
        views.py) — so from the student's side, the pass only ever loses
        money for days a class actually happened, whether they were there
        to see it happen live or not. Returns how many new charges were
        created.
        """
        if self.remaining_balance <= 0:
            return 0
        sessions = ClassSession.objects.filter(
            classroom_id=self.class_pass.classroom_id,
            status=ClassSession.Status.COMPLETED,
        ).order_by("scheduled_start")
        charged = 0
        for session in sessions:
            with transaction.atomic():
                locked = PassPurchase.objects.select_for_update().get(pk=self.pk)
                result = locked.charge_for_session(session)
            if result is not None:
                charged += 1
            self.refresh_from_db(fields=["coins_released", "last_charge_date", "status", "is_active"])
            if self.remaining_balance <= 0:
                break
        return charged

    def reverse(self, notify: bool = True) -> None:
        """Cancel this pass and refund whatever's LEFT in escrow
        (remaining_balance) back to the student — never the full
        coins_spent, since coins_released has already legitimately gone
        to the teacher for classes they actually held, and never more
        than what's left to give back. This is what both the teacher
        (closing a classroom / refunding one student — see
        ClassroomViewSet.close and PassPurchaseViewSet.refund) and the
        student themselves (PassPurchaseViewSet.cancel — a student no
        longer has to just keep paying for a pass they don't want to
        finish using) end up calling. No teacher clawback needed at all:
        unlike the old lump-sum design, the teacher was never given money
        for classes that hadn't happened yet, so there's nothing to claw
        back.

        Caller must already hold a row lock on self (select_for_update)
        and run inside transaction.atomic() — same contract the old
        module-level _refund_purchase() in views.py used to have; this
        method replaces it.
        """
        refund = self.remaining_balance
        if refund > 0:
            student = type(self.student).objects.select_for_update().get(pk=self.student_id)
            student.coin += refund
            student.save(update_fields=["coin"])
            CoinTransaction.objects.create(
                user=student,
                txn_type=CoinTransaction.TxnType.CREDIT,
                reason=CoinTransaction.Reason.REFUND,
                amount=refund,
                balance_after=student.coin,
                reference_id=f"passpurchase:{self.id}",
            )

        self.status = self.Status.REFUNDED
        self.is_active = False
        self.refunded_amount = refund
        self.refunded_at = timezone.now()
        self.save(update_fields=["status", "is_active", "refunded_amount", "refunded_at"])

        # NOTE (fix — coupon slot permanently burned by an unused pass):
        # _charge_and_create_purchase() increments Coupon.used_count the
        # moment a purchase is created, but nothing ever gave that slot
        # back on reverse(). A student could buy with a limited coupon and
        # cancel a second later, permanently eating one redemption of a
        # coupon that never actually paid the teacher anything — squeezing
        # out a genuine student later.
        #
        # Only give the slot back if coins_released is still 0 — i.e. this
        # purchase never survived to see even one taught day charged
        # against it. Once even a single day has been released to the
        # teacher, the coupon did its job (discounted a real transaction
        # that paid out), so its use stays counted even if the student
        # cancels the remainder later — same principle reverse() already
        # applies to coins_released itself (earned money is never clawed
        # back on cancel).
        if self.coupon_id and self.coins_released == 0:
            Coupon.objects.filter(pk=self.coupon_id).update(
                used_count=Greatest(F("used_count") - 1, 0)
            )

        if notify:
            try:
                from ..tasks import notify_purchase_refunded

                notify_purchase_refunded.delay(self.id)
            except Exception:
                logging.getLogger("tuitionclass.models").exception(
                    "Failed to queue refund notification for purchase %s", self.id
                )

    def renew(self) -> "PassPurchase | None":
        """NEW (Pass 15 — auto-renew passes). Buys a fresh PassPurchase for
        the exact same (student, class_pass) this one was for — meant to be
        called once this purchase has actually expired (or is about to),
        by a scheduled sweep (see the auto_renew field's docstring above).

        Deliberately mirrors _charge_and_create_purchase() in views.py
        (same coin-debit-then-create shape, same per_day_rate math) rather
        than importing it, so models.py never has to import from views.py
        — but WITHOUT coupon/referral carry-over: a renewal is a fresh
        subscription cycle at the class_pass's current plain price, same
        "coupons are a one-time self-purchase discount, not a standing
        entitlement" reasoning PassGift already applies to gifting.

        Returns the new PassPurchase on success. On failure (insufficient
        coins, or the classroom/pass having gone inactive since), clears
        auto_renew on THIS purchase (so the sweep doesn't keep retrying a
        subscription that can't renew), stamps renewal_failed_at, and
        returns None — never raises, since a scheduled sweep processing
        many purchases can't let one student's empty wallet blow up the
        whole run.

        Caller does not need to pre-lock self — this method takes its own
        row lock internally, same contract as charge_for_session/reverse
        expect of THEIR callers, just self-contained here since renew()
        is normally invoked standalone per-purchase by the sweep rather
        than nested inside a larger already-locked transaction.
        """
        class_pass = self.class_pass
        student = self.student

        def _fail() -> None:
            PassPurchase.objects.filter(pk=self.pk).update(
                auto_renew=False, renewal_failed_at=timezone.now()
            )
            try:
                from ..tasks import notify_auto_renew_failed

                notify_auto_renew_failed.delay(self.id)
            except Exception:
                logging.getLogger("tuitionclass.models").exception(
                    "Failed to queue auto-renew-failed notification for purchase %s", self.id
                )

        if not class_pass.is_active or not class_pass.classroom.is_active or class_pass.classroom.is_deleted:
            _fail()
            return None

        coins_spent = int(class_pass.price.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

        try:
            with transaction.atomic():
                if coins_spent > 0:
                    locked_student = type(student).objects.select_for_update().get(pk=student.pk)
                    if locked_student.coin < coins_spent:
                        raise ValidationError("Insufficient coin balance for renewal.")
                    locked_student.coin -= coins_spent
                    locked_student.save(update_fields=["coin"])
                    CoinTransaction.objects.create(
                        user=locked_student,
                        txn_type=CoinTransaction.TxnType.DEBIT,
                        reason=CoinTransaction.Reason.PASS_AUTO_RENEWED,
                        amount=coins_spent,
                        balance_after=locked_student.coin,
                        reference_id=f"class_pass:{class_pass.id}:renewal_of:{self.id}",
                    )
                    payment_method = PassPurchase.PaymentMethod.COIN_WALLET
                else:
                    payment_method = PassPurchase.PaymentMethod.FREE

                per_day_rate = Decimal(coins_spent) / class_pass.validity_days
                new_purchase = PassPurchase.objects.create(
                    student=student,
                    class_pass=class_pass,
                    payment_method=payment_method,
                    amount_paid=Decimal(coins_spent),
                    coins_spent=coins_spent,
                    per_day_rate=per_day_rate,
                    status=PassPurchase.Status.SUCCESS,
                    expires_at=timezone.now() + timezone.timedelta(days=class_pass.validity_days),
                    auto_renew=True,  # subscription continues by default; student can turn it off again
                    renewed_from=self,
                )
        except (ValidationError, DjangoIntegrityError):
            _fail()
            return None

        # This purchase's own cycle is over — leave its escrow/coins_released
        # history exactly as it is (audit trail), just stop it being picked
        # up by the sweep again (only the newest link in the chain has
        # auto_renew=True).
        PassPurchase.objects.filter(pk=self.pk).update(auto_renew=False)

        try:
            from ..tasks import notify_pass_auto_renewed

            notify_pass_auto_renewed.delay(new_purchase.id)
        except Exception:
            logging.getLogger("tuitionclass.models").exception(
                "Failed to queue auto-renew notification for purchase %s", new_purchase.id
            )

        return new_purchase


# ---------------------------------------------------------------------------
# 5A2. PASS DAILY CHARGE (audit trail — one row per calendar day a pass was
#      actually charged, i.e. per day this classroom held a class while the
#      pass was valid; see PassPurchase.charge_for_session above)
# ---------------------------------------------------------------------------
class PassDailyCharge(models.Model):
    """Ledger row proving exactly which day a PassPurchase's escrow was
    tapped and why (linked back to the ClassSession that triggered it).
    The unique (purchase, date) constraint is what makes the whole
    per-day-charging feature idempotent and safe to re-run — it's the
    actual guard against a class somehow charging the same pass twice for
    the same day, not just a nice-to-have audit log."""

    purchase = models.ForeignKey(PassPurchase, on_delete=models.CASCADE, related_name="daily_charges")
    session = models.ForeignKey(
        ClassSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="pass_charges"
    )
    date = models.DateField()
    amount = models.PositiveIntegerField()
    # FEATURE (refer & earn): commission paid to purchase.referred_by for
    # this same date, alongside `amount` going to the teacher — 0 when the
    # purchase wasn't referred, or once its referral commission is fully
    # released. Kept on this same ledger row rather than a parallel table
    # since it's the same trigger, same date, same idempotency guard.
    referral_amount = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["purchase", "date"], name="one_charge_per_purchase_per_day")
        ]

    def __str__(self):
        return f"{self.purchase} - {self.date} ({self.amount} coin)"


# ---------------------------------------------------------------------------
# 5A3. GIFTING A PASS (Pass 14) — one platform user pays for a pass and
# hands it to another platform user, instead of buying it for themselves.
# ---------------------------------------------------------------------------
class PassGift(models.Model):
    """A gift is its own row, separate from PassPurchase, because the coin
    debit and the actual pass grant happen at DIFFERENT times: the gifter
    pays the moment they send the gift (so they can't back out after the
    recipient already sees it), but the PassPurchase (and its escrow
    clock — validity_days, per_day_rate, etc.) is only created once the
    recipient actually claims it — same "don't start a clock the
    recipient hasn't agreed to yet" reasoning as a gift card not
    activating until redeemed. See `_create_gift`/`claim`/`cancel` on
    PassGiftViewSet in views.py for the coin-flow details.

    SCOPING NOTE: a gift does not stack with a coupon or a referral link
    — the gifter pays the class_pass's plain price in full, and the
    resulting PassPurchase (once claimed) has no coupon/referred_by set.
    Coupons/referrals are a self-purchase discount/commission mechanic;
    mixing them into gifting would mean deciding whose referral credit
    a gift counts against (the gifter's or the recipient's), which is a
    product decision this pass deliberately leaves for later rather than
    guessing.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Claim"
        CLAIMED = "claimed", "Claimed"
        CANCELLED = "cancelled", "Cancelled By Gifter"
        EXPIRED = "expired", "Expired Unclaimed"

    # How long an unclaimed gift stays open before the recipient can no
    # longer claim it (and tasks.expire_unclaimed_gifts sweeps it back to
    # the gifter) — used by PassGiftViewSet.perform_create to stamp
    # expires_at. Kept as a class constant (not a settings.py value) since
    # it's a product decision about this one flow, not deployment config.
    CLAIM_WINDOW_DAYS = 7

    gifter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="passes_gifted")
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="passes_gifted_to_me")
    class_pass = models.ForeignKey(ClassPass, on_delete=models.CASCADE, related_name="gifts")

    coins_spent = models.PositiveIntegerField(help_text="Frozen at gift time — the class_pass's price then.")
    gift_message = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # Resulting purchase, once claimed — null until then. SET_NULL so a
    # later purchase deletion (there isn't one today, but defensively)
    # can never cascade-delete the gift's own audit trail.
    purchase = models.ForeignKey(
        PassPurchase, on_delete=models.SET_NULL, null=True, blank=True, related_name="from_gift"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    # Unclaimed gifts don't sit open forever — same "escrowed coin, not
    # free money" reasoning as PassPurchase itself: the gifter's coins are
    # already debited (see PassGiftViewSet.create), so an abandoned gift
    # needs a claim deadline and a refund path, not an indefinite hold.
    expires_at = models.DateTimeField()
    claimed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "status"]), models.Index(fields=["status", "expires_at"])]

    def __str__(self):
        return f"{self.gifter} -> {self.recipient}: {self.class_pass} ({self.get_status_display()})"

    def refund_to_gifter(self) -> None:
        """Give the gifter their coins back — used by both cancel() (the
        gifter changes their mind before the recipient claims) and the
        expiry sweep (tasks.expire_unclaimed_gifts). Caller must hold a
        row lock on self and run inside transaction.atomic(), same
        contract as PassPurchase.reverse()."""
        gifter = type(self.gifter).objects.select_for_update().get(pk=self.gifter_id)
        gifter.coin += self.coins_spent
        gifter.save(update_fields=["coin"])
        CoinTransaction.objects.create(
            user=gifter,
            txn_type=CoinTransaction.TxnType.CREDIT,
            reason=CoinTransaction.Reason.GIFT_CANCELLED_REFUND,
            amount=self.coins_spent,
            balance_after=gifter.coin,
            reference_id=f"passgift:{self.id}",
        )


# ---------------------------------------------------------------------------
# 5B. CLASS JOIN REQUEST (student's request to enroll -> teacher approval
#     gate -> ONLY on acceptance is a PassPurchase created and coins charged)
# ---------------------------------------------------------------------------
class ClassJoinRequest(models.Model):
    """The only door into a classroom for a non-owner/non-staff user.

    A student never buys a pass directly. They raise a request against a
    specific ClassPass; the classroom's teacher/co-teacher/moderator then
    accepts or rejects it. Coins are debited and the PassPurchase row (the
    thing Classroom.has_access() actually checks) is created ONLY inside
    the accept() action — so nobody is charged, and nobody becomes a
    participant, without the teacher's say-so first.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="join_requests")
    class_pass = models.ForeignKey(ClassPass, on_delete=models.CASCADE, related_name="join_requests")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="classroom_join_requests")

    coupon_code = models.CharField(max_length=30, blank=True)
    message = models.CharField(
        max_length=255, blank=True, help_text="Optional note from the student to the teacher."
    )

    # FEATURE (refer & earn): who the student's ?ref= code resolved to when
    # they raised this request — see ClassJoinRequestViewSet.perform_create,
    # which decodes it via referral_code_to_user_id() and only sets this at
    # all when the classroom had referral_enabled=True at request time.
    # Carried onto the resulting PassPurchase (see referred_by there) by
    # accept() so the actual per-day commission has something to key off;
    # SET_NULL rather than CASCADE so a referrer's account being deleted
    # later doesn't take the join-request/purchase history down with it.
    referred_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="referred_join_requests",
    )

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    # Set only when status becomes ACCEPTED — links straight to the
    # PassPurchase that accept() created, so the frontend/audit trail can
    # trace exactly which purchase this request resulted in.
    pass_purchase = models.OneToOneField(
        PassPurchase, on_delete=models.SET_NULL, null=True, blank=True, related_name="join_request"
    )
    decided_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="join_requests_decided"
    )
    decision_note = models.CharField(max_length=255, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    requested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["classroom", "status"]),
            models.Index(fields=["student", "status"]),
        ]
        constraints = [
            # A student can only ever have ONE pending request open against a
            # given classroom at a time — stops them from spamming requests
            # (or racing two accepts) while one is already awaiting a decision.
            models.UniqueConstraint(
                fields=["classroom", "student"],
                condition=models.Q(status="pending"),
                name="one_pending_join_request_per_student_per_classroom",
            )
        ]

    def __str__(self):
        return f"{self.student} -> {self.classroom.title} ({self.get_status_display()})"



# Forward references: used only inside method/function bodies, defined in a LATER module. Bound by models/__init__.py
# once every module is loaded (see _link_forward_refs there). `None` here only keeps linters quiet.
CoinTransaction = None  # -> .wallet
Coupon = None  # -> .wallet
ReferralAttribution = None  # -> .referral
ReferralCommission = None  # -> .referral
