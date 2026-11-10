"""
tuitionclass/views/passes.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import logging
from core.models import Notification
from core.services import create_notification
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from ..models import (
    ClassJoinRequest,
    ClassPass,
    Classroom,
    CoinTransaction,
    Coupon,
    PassGift,
    PassPurchase,
    ReferralAttribution,
    referral_code_to_user_id,
)
from ..serializers import (
    ClassJoinRequestDecisionSerializer,
    ClassJoinRequestSerializer,
    ClassPassSerializer,
    CouponSerializer,
    PassGiftSerializer,
    PassPurchaseAutoRenewSerializer,
    PassPurchaseSerializer,
)

from ._common import TuitionClassPagination, _safe_broadcast_to_user, _safe_delay
from .access import AccessLevel, _can_manage_classroom

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 4. PASS
# ---------------------------------------------------------------------------
class ClassPassViewSet(viewsets.ModelViewSet):
    serializer_class = ClassPassSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination
    queryset = ClassPass.objects.filter(is_active=True)
    # NOTE (UX): lets the buy-pass screen sort tiers by price or newest
    # instead of only ever getting the model's default (price ascending).
    filter_backends = [OrderingFilter]
    ordering_fields = ["price", "validity_days", "created_at"]
    ordering = ["price"]

    def get_queryset(self):
        # NOTE (fix): there was no get_queryset() override here at all, so
        # ?classroom=<id> — which BuyPassScreen sends on every load via
        # TuitionClassApiService.getPasses(classroomId: ...) — was silently
        # ignored by the backend. GET passes/?classroom=X returned every
        # active pass for every classroom on the platform, not just X's;
        # the buy-pass screen would then show other classrooms' pricing
        # tiers mixed in with (or instead of) the one the student actually
        # opened. Passes are meant to be publicly browsable pre-purchase
        # (no _can_view_classroom_internals gate needed, unlike schedules/
        # materials), so this just applies the filter when given.
        #
        # NOTE (fix): the base `queryset = ClassPass.objects.filter(is_active=True)`
        # also silently doubled as the detail (get_object) queryset. Once a
        # teacher paused a pass (is_active=False) — e.g. to stop new signups
        # without deleting pricing history — GET/PATCH on that pass's own
        # detail URL 404'd for them too, so there was no way to edit it or
        # ever flip is_active back to True again. Same fix as ClassroomViewSet:
        # public/other-teachers' passes still only show active ones; the
        # classroom's own teacher can always see and manage all of theirs.
        qs = ClassPass.objects.filter(Q(is_active=True) | Q(classroom__teacher=self.request.user))
        classroom_id = self.request.query_params.get("classroom")
        if classroom_id:
            qs = qs.filter(classroom_id=classroom_id)
        return qs

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can create a pass.")
        serializer.save()

    # NOTE (fix — writable-classroom-FK reassigments sweep, item 5):
    # ClassPassSerializer.classroom is writable, not read_only — permission
    # was only ever checked against the OLD `instance.classroom`, so a
    # teacher could reassign a pass they own to a different classroom in
    # the same PATCH. Same bug-class as Schedule/Session above; blocked
    # outright.
    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can edit a pass.")
        new_classroom = serializer.validated_data.get("classroom")
        if new_classroom is not None and new_classroom.id != instance.classroom_id:
            raise ValidationError({"classroom": "A pass can't be moved to a different classroom."})

        # NOTE (fix): this is the pass-level twin of the create-sell-vanish
        # gap Classroom.perform_destroy was hardened against — just reached
        # via PATCH instead of DELETE. A pass with paying holders used to be
        # freely editable: nothing stopped a teacher from selling a pass at
        # its listed price/validity/max_classes, then quietly raising the
        # price (misleading future buyers about what current holders paid)
        # or — worse — cutting validity_days/max_classes on a pass people
        # ALREADY paid coins for. PassPurchase snapshots its own expires_at
        # at purchase time, but per-purchase attendance limits are checked
        # against this pass's live max_classes, so shrinking it after the
        # fact can silently lock a currently-paying student out of classes
        # they already paid for — coins taken, value clawed back after the
        # fact, no refund. Block any such downgrade while at least one
        # currently active, paid purchase is outstanding on this pass; the
        # teacher can still edit freely once every purchase on it has
        # lapsed or been refunded, and can always pause new sales via
        # is_active without touching the terms existing holders paid for.
        #
        # NOTE (fix, race): the has_active_purchases check + the save() used
        # to run unlocked, back to back but not atomically — a join request
        # could be accept()-ed (creating a fresh active PassPurchase) in the
        # gap between this check reading "no active purchases yet" and the
        # save() actually landing, letting a downgrade slip through against
        # a pass that, by the time the edit committed, already had a paying
        # holder. Locking the row here serializes against
        # ClassJoinRequestViewSet.accept()'s own select_for_update on the
        # same ClassPass row (see that view) — whichever transaction gets
        # there first now makes the other wait, instead of both reading a
        # stale "safe" snapshot.
        with transaction.atomic():
            instance = ClassPass.objects.select_for_update().get(pk=instance.pk)
            has_active_purchases = PassPurchase.objects.filter(
                class_pass=instance,
                status=PassPurchase.Status.SUCCESS,
                is_active=True,
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).exists()

            if has_active_purchases:
                data = serializer.validated_data
                if "price" in data and data["price"] is not None and data["price"] > instance.price:
                    raise ValidationError(
                        "Can't raise the price on a pass with active holders. Create a new pass instead."
                    )
                for field in ("validity_days", "max_classes"):
                    if field in data and data[field] is not None:
                        old_value = getattr(instance, field)
                        if old_value is not None and data[field] < old_value:
                            raise ValidationError(
                                f"Can't reduce {field.replace('_', ' ')} on a pass with active "
                                "holders — that would retroactively cut access students already "
                                "paid coins for."
                            )
                if "pass_type" in data and data["pass_type"] != instance.pass_type:
                    raise ValidationError("Can't change the pass type on a pass with active holders.")

            serializer.instance = instance
            serializer.save()

    def perform_destroy(self, instance):
        if instance.classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can remove a pass.")

        # NOTE (fix): same create-sell-vanish gap as Classroom.perform_destroy
        # used to have, one level down — this used to be a bare
        # instance.delete() regardless of whether anyone had ever actually
        # paid coins for this pass. Deleting a pass that's been purchased
        # risks silently taking the purchase record (and the coin-spend +
        # access history it represents) down with it, and breaks every
        # screen that resolves a purchase back to its pass (buy_pass_screen,
        # wallet history, refund lookups). Once a pass has ever been sold,
        # deleting it is no longer a "clean up an unused pricing tier"
        # operation, so it's blocked outright — the supported way to retire
        # a sold pass is PATCH is_active=False (pause: stops new signups,
        # existing holders keep full access, refund/close flows are
        # untouched), which get_queryset() above already keeps visible to
        # the teacher for exactly this.
        #
        # NOTE (fix, race): same reasoning as perform_update above — lock
        # the row so this can't slip a delete through in the gap between an
        # in-flight accept() creating the pass's first-ever PassPurchase and
        # that transaction committing.
        with transaction.atomic():
            instance = ClassPass.objects.select_for_update().get(pk=instance.pk)
            if PassPurchase.objects.filter(class_pass=instance).exists():
                raise ValidationError(
                    "This pass has been purchased before and can't be deleted — set it to "
                    "inactive (pause) instead so existing holders keep their access and the "
                    "purchase history stays intact."
                )
            instance.delete()


# ---------------------------------------------------------------------------
# 5. PASS PURCHASE (read-only — a PassPurchase is only ever created as the
#    side effect of ClassJoinRequestViewSet.accept(); there is no direct
#    "buy" endpoint, on purpose — see ClassJoinRequest's docstring)
# ---------------------------------------------------------------------------
class PassPurchaseViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Own purchases only by default. Pass ?classroom=<id> as that
    classroom's teacher/co-teacher/moderator to see every purchase on it
    instead — see get_queryset()."""

    serializer_class = PassPurchaseSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): an active student's purchase history grows with every
    # renewal/repurchase across every classroom they've ever joined —
    # unbounded over time, same as the session/chat/ledger lists above.
    pagination_class = TuitionClassPagination

    def get_queryset(self):
        # NOTE (fix): this used to unconditionally filter to
        # student=request.user with no way for a classroom's teacher/
        # co-teacher/moderator to ever list purchases on their OWN
        # classroom. The refund() action below has always allowed them to
        # refund a specific purchase, but with no listing path there was no
        # way to discover a purchase's id in the first place — the action
        # existed but was practically unreachable from the app. ?classroom=
        # opts into "every purchase on that classroom" once
        # _can_manage_classroom confirms the caller actually manages it;
        # same opt-in pattern as SessionWaitlistViewSet's ?session= above.
        # No ?classroom= (or failing that check) falls straight through to
        # the original "own purchases only" behaviour.
        base = PassPurchase.objects.select_related("student", "coupon", "class_pass__classroom")
        classroom_id = self.request.query_params.get("classroom")
        if classroom_id:
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            if classroom and _can_manage_classroom(classroom, self.request.user):
                return base.filter(class_pass__classroom_id=classroom_id)
        return base.filter(student=self.request.user)

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        """Teacher/co-teacher/moderator/staff-only reversal of a single
        purchase — e.g. resolving one student's complaint or a report.
        NOT usable by the student themselves; that's what cancel() below
        is for.

        NOTE (fix): this used to call the old lump-sum _refund_purchase(),
        which refunded the student's FULL coins_spent and clawed that
        same amount back out of the teacher's wallet — including coins
        the teacher had already legitimately earned for days actually
        taught. Now delegates to PassPurchase.reverse(), which only
        refunds remaining_balance (whatever is still sitting in escrow,
        un-earned) and leaves coins_released alone. See reverse()'s
        docstring for why no teacher clawback is needed at all under the
        escrow design. reverse() also queues the refund notification
        itself, so there's no separate create_notification() call here
        any more.

        Deliberately not scoped through get_queryset() (which is "own
        purchases only"): the caller here needs to act on someone ELSE's
        purchase, so the object is fetched directly and the permission
        check is explicit, same pattern as ClassJoinRequestViewSet.accept.
        """
        purchase = get_object_or_404(PassPurchase, pk=pk)
        classroom = purchase.class_pass.classroom
        if not (_can_manage_classroom(classroom, request.user) or request.user.is_staff):
            raise PermissionDenied(
                "Only the classroom's teacher/co-teacher/moderator, or platform staff, can refund this pass."
            )
        if purchase.status != PassPurchase.Status.SUCCESS:
            raise ValidationError(f"This purchase is already {purchase.get_status_display().lower()}.")

        with transaction.atomic():
            purchase = PassPurchase.objects.select_for_update().get(pk=purchase.pk)
            if purchase.status != PassPurchase.Status.SUCCESS:
                raise ValidationError(f"This purchase is already {purchase.get_status_display().lower()}.")
            purchase.reverse()

        return Response(PassPurchaseSerializer(purchase).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Student self-service: cancel your OWN currently-active pass
        whenever you want, without needing the teacher to do anything.

        NOTE (fix): this endpoint didn't exist at all before, even though
        PassPurchase.reverse()'s docstring already documented it as one of
        the two callers ("the student themselves ... a student no longer
        has to just keep paying for a pass they don't want to finish
        using"). Only remaining_balance (whatever's still in escrow, i.e.
        days nobody has taught yet) is refunded — coins already released
        to the teacher for classes actually held stay with the teacher.
        Scoped to the requester's own purchase (student=request.user), the
        same as get_queryset()'s default "own purchases only" — but
        fetched directly here (not via get_queryset()) so a ?classroom=
        query param a teacher might have set for a different call on this
        viewset can never accidentally widen this lookup.
        """
        purchase = get_object_or_404(PassPurchase, pk=pk, student=request.user)
        if purchase.status != PassPurchase.Status.SUCCESS:
            raise ValidationError(f"This purchase is already {purchase.get_status_display().lower()}.")

        with transaction.atomic():
            purchase = PassPurchase.objects.select_for_update().get(pk=purchase.pk)
            if purchase.status != PassPurchase.Status.SUCCESS:
                raise ValidationError(f"This purchase is already {purchase.get_status_display().lower()}.")
            purchase.reverse()

        return Response(PassPurchaseSerializer(purchase).data)

    @action(detail=True, methods=["post"], url_path="toggle-auto-renew")
    def toggle_auto_renew(self, request, pk=None):
        """NEW (Pass 15 — auto-renew passes). The student's own opt-in/
        opt-out switch — see the auto_renew field's docstring on
        PassPurchase in models.py for what flipping it on actually does
        (a scheduled sweep, e.g. tasks.run_auto_renewals, picks this
        purchase up once it expires and calls .renew() on it). This is
        the only way auto_renew is ever set — it's read-only on
        PassPurchaseSerializer (see serializers.py) precisely so a
        student can't smuggle it in through a plain PATCH.

        POST {"auto_renew": true|false}. Scoped to the requester's own
        purchase, fetched directly (not via get_queryset()) for the same
        reason as cancel() above — a ?classroom= a teacher might have
        set for a different call on this viewset must never widen this
        lookup to someone else's purchase.
        """
        purchase = get_object_or_404(PassPurchase, pk=pk, student=request.user)
        if purchase.status != PassPurchase.Status.SUCCESS:
            raise ValidationError(f"This purchase is already {purchase.get_status_display().lower()}.")

        serializer = PassPurchaseAutoRenewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        purchase.auto_renew = serializer.validated_data["auto_renew"]
        update_fields = ["auto_renew"]
        if purchase.auto_renew:
            # Opting back in clears any earlier failure flag — otherwise
            # a student who tops up their wallet and flips this back on
            # would still show a stale renewal_failed_at from the
            # previous cycle until the next sweep run overwrites it.
            purchase.renewal_failed_at = None
            update_fields.append("renewal_failed_at")
        purchase.save(update_fields=update_fields)

        return Response(PassPurchaseSerializer(purchase).data)

    # -----------------------------------------------------------------
    # FEATURE (refer & earn): the referrer's own view of what their links
    # have brought in — every purchase currently crediting them a
    # commission, plus a running total so they don't have to sum
    # CoinTransaction rows themselves. Own referrals only (referred_by=
    # request.user) — deliberately NOT reachable via the ?classroom= path
    # above, which is scoped to classroom managers, not referrers.
    # -----------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="referral-earnings")
    def referral_earnings(self, request):
        qs = PassPurchase.objects.filter(referred_by=request.user).select_related(
            "student", "coupon", "class_pass__classroom"
        )
        total_earned = CoinTransaction.objects.filter(
            user=request.user, reason=CoinTransaction.Reason.CLASS_REFERRAL_COMMISSION
        ).aggregate(total=Sum("amount"))["total"] or 0

        page = self.paginate_queryset(qs)
        serializer = PassPurchaseSerializer(page if page is not None else qs, many=True)
        results = serializer.data
        if page is not None:
            response = self.get_paginated_response(results)
            response.data["total_earned"] = total_earned
            return response
        return Response({"total_earned": total_earned, "results": results})


def _preview_coupon_for_pass(coupon_code, class_pass):
    """Non-locking twin of the coupon checks inside
    _charge_and_create_purchase(), used at REQUEST time to price a join
    request. Returns the Coupon (or None when no code was given); raises the
    same ValidationError messages accept() would, so a request that could
    never be accepted is refused up front instead of sitting in the
    teacher's inbox."""
    if not coupon_code:
        return None
    coupon = Coupon.objects.filter(code__iexact=coupon_code).first()
    if not coupon or not coupon.is_valid():
        raise ValidationError({"coupon_code": "Invalid or expired coupon."})
    classroom = class_pass.classroom
    scoped_to_this_classroom = coupon.classroom_id == classroom.id
    scoped_to_creators_other_classroom = coupon.classroom_id is None and coupon.created_by_id == classroom.teacher_id
    if not (scoped_to_this_classroom or scoped_to_creators_other_classroom):
        raise ValidationError({"coupon_code": "This coupon isn't valid for this classroom."})
    return coupon


def _coins_for_pass(class_pass, coupon=None):
    """Whole-coin price of a pass after an optional coupon — the same maths
    (and the same ROUND_HALF_UP rounding) _charge_and_create_purchase()
    uses, so the balance check at request time can never disagree with the
    amount actually debited at accept time."""
    price = class_pass.price
    if coupon:
        if coupon.discount_percent:
            price -= price * coupon.discount_percent / 100
        if coupon.discount_amount:
            price -= coupon.discount_amount
        price = max(price, Decimal("0"))
    return int(Decimal(price).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _charge_and_create_purchase(
    student, class_pass, coupon_code, referred_by=None, referral_commission_percent=Decimal("0")
):
    """Shared coin-debit + PassPurchase-creation logic, used ONLY by
    ClassJoinRequestViewSet.accept() below. Coin-only marketplace: the
    student is charged out of their User.coin balance, and the
    PassPurchase row is created ONLY if they have enough coins for the
    (post-coupon) price — if not, we raise and nothing is written, so a
    pass can never exist without the coins actually being debited first.

    payment_method is NOT accepted from the client — there is no card/UPI
    path in this app. It's decided from the final price: 0 -> FREE (no
    coin debit), otherwise -> COIN_WALLET (mandatory coin debit).

    Must be called from inside a transaction.atomic() block by the caller,
    with row locks on the coupon (max_uses check) and the student's wallet
    (coin balance) — without this, two concurrent accepts on two different
    pending requests for the same coupon/student could both pass the "is
    coupon still valid" / "is balance sufficient" checks before either
    write lands, over-redeeming a limited coupon or letting a wallet
    balance go negative.
    """
    coupon = None
    if coupon_code:
        coupon = Coupon.objects.select_for_update().filter(code__iexact=coupon_code).first()
        if not coupon or not coupon.is_valid():
            raise ValidationError({"coupon_code": "Invalid or expired coupon."})
        # NOTE (fix): coupon codes are globally unique, but nothing tied a
        # coupon to the classroom/teacher it was actually meant for — a
        # coupon created by Teacher A (for their own classroom, or scoped
        # to one specific classroom of theirs) could be redeemed against
        # ANY other teacher's pass, discounting (or zeroing out) revenue
        # they never agreed to give away. A coupon is only usable against
        # the classroom it's scoped to (coupon.classroom), or — if
        # classroom is null ("usable across all of this teacher's
        # classrooms") — against any classroom created_by actually teaches.
        classroom = class_pass.classroom
        scoped_to_this_classroom = coupon.classroom_id == classroom.id
        scoped_to_creators_other_classroom = coupon.classroom_id is None and coupon.created_by_id == classroom.teacher_id
        if not (scoped_to_this_classroom or scoped_to_creators_other_classroom):
            raise ValidationError({"coupon_code": "This coupon isn't valid for this classroom."})

    price = class_pass.price
    if coupon:
        if coupon.discount_percent:
            price -= price * coupon.discount_percent / 100
        if coupon.discount_amount:
            price -= coupon.discount_amount
        price = max(price, Decimal("0"))

    # NOTE (fix — systematic underpayment): plain int(price) always
    # TRUNCATES toward zero, so any coupon discount that lands on a
    # fractional coin amount (e.g. 20% off a 49-coin pass = 39.2) always
    # rounded in the STUDENT's favor (39, never 39.2 -> 39 is fine, but
    # 49.6 would truncate to 49 instead of the correct 50) — a small but
    # completely predictable and exploitable leak against the teacher on
    # every single discounted purchase. Round to nearest instead (banker's-
    # rounding-safe via Decimal quantize) so neither side is systematically
    # favored; ROUND_HALF_UP matches how a price is normally displayed.
    coins_spent = int(price.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    if coins_spent > 0:
        # Coins are the ONLY way to pay -> lock the student's row, verify
        # balance, and refuse to create the pass at all if it's short. The
        # request stays PENDING so the student can top up and the teacher
        # can retry accept() later — a failed accept is not a rejection.
        user = type(student).objects.select_for_update().get(pk=student.pk)
        if user.coin < coins_spent:
            raise ValidationError({"coin": "Student has insufficient coin balance to accept this request."})
        user.coin -= coins_spent
        user.save(update_fields=["coin"])
        CoinTransaction.objects.create(
            user=user,
            txn_type=CoinTransaction.TxnType.DEBIT,
            reason=CoinTransaction.Reason.PASS_PURCHASE,
            amount=coins_spent,
            balance_after=user.coin,
            reference_id=f"class_pass:{class_pass.id}",
        )

        # NOTE (fix — "pay only for classes actually held"): this used to
        # credit the teacher the full coins_spent right here, the moment
        # accept() ran — before a single class of this pass's validity
        # window had even happened. That's exactly the loss case a
        # student hits when a teacher stops teaching mid-pass: the money
        # for every un-taught day was already gone from escrow and sitting
        # in the teacher's wallet, so closing/refunding the pass could
        # only claw it back if the teacher hadn't already spent/withdrawn
        # it (see the clamp that used to live in _refund_purchase).
        #
        # The coins still leave the student's wallet here (unchanged —
        # that's the escrow debit, and the balance/coupon checks above
        # already ran once), but they are NOT paid to the teacher yet.
        # They sit in escrow on this PassPurchase and are released to the
        # teacher one calendar day at a time, only for a day this
        # classroom actually held a class — see
        # PassPurchase.charge_for_session(), fired by the post_save
        # signal on ClassSession in models.py. per_day_rate is the
        # snapshot this purchase releases against; frozen here so a later
        # price edit on the ClassPass can never retroactively change what
        # an already-purchased pass charges per day.
        payment_method = PassPurchase.PaymentMethod.COIN_WALLET
    else:
        payment_method = PassPurchase.PaymentMethod.FREE

    # FEATURE (refer & earn): same "frozen at purchase time" treatment as
    # per_day_rate just above — referral_per_day_rate is pre-computed here
    # (percent-of-per-day-rate) so charge_for_session() never has to redo
    # the percentage math, and so a later change to the classroom's rate
    # can never retroactively change what an already-accepted purchase
    # pays a referrer. No referral on this purchase at all (referred_by is
    # None, or the classroom had referrals off at accept-time — see the
    # caller) just leaves these at their zero defaults.
    per_day_rate = Decimal(coins_spent) / class_pass.validity_days
    referral_per_day_rate = Decimal("0")
    if referred_by is not None and referral_commission_percent:
        referral_per_day_rate = per_day_rate * referral_commission_percent / 100

    purchase = PassPurchase.objects.create(
        student=student,
        class_pass=class_pass,
        coupon=coupon,
        payment_method=payment_method,
        # NOTE (fix): record what was actually debited (coins_spent), not
        # the pre-rounding fractional price — otherwise amount_paid and
        # coins_spent could disagree on this exact row (e.g. 49.6 vs 50),
        # which is exactly the kind of mismatch a dispute/audit trail must
        # not have.
        amount_paid=Decimal(coins_spent),
        coins_spent=coins_spent,
        # Escrow release rate — see the NOTE above payment_method for why
        # the teacher is no longer paid coins_spent upfront. Decimal
        # division here (not int) is deliberate: charge_for_session()
        # rounds each day's charge itself, and hands the final day
        # whatever fractional remainder is left rather than stranding it.
        per_day_rate=per_day_rate,
        referred_by=referred_by,
        referral_commission_percent=referral_commission_percent if referred_by is not None else Decimal("0"),
        referral_per_day_rate=referral_per_day_rate,
        status=PassPurchase.Status.SUCCESS,
        expires_at=timezone.now() + timezone.timedelta(days=class_pass.validity_days),
    )
    if coupon:
        Coupon.objects.filter(pk=coupon.pk).update(used_count=F("used_count") + 1)

    # R9 / N6b - receipt notification for a PAID pass (a free pass has nothing to receipt; the task skips coins_spent=0
    # anyway). Enqueued after the caller's transaction commits, never raises into the accept.
    if coins_spent > 0:
        try:
            from core.notification_receipts import KIND_PASS, queue_receipt

            queue_receipt(KIND_PASS, purchase.pk)
        except Exception:  # noqa: BLE001
            logger.exception("class pass receipt could not be queued (payment itself is confirmed)")

    # NEW (task 65 — classroom refer & earn, student side): one-time,
    # platform-funded join bonus to the STUDENT, awarded exactly once per
    # purchase, the same moment referred_by is attributed. Separate credit
    # from the coin_wallet debit above (that block only runs when
    # coins_spent > 0; a free pass can still carry a referral and should
    # still pay this bonus), so re-lock the wallet here rather than reuse
    # `user` from that block, which may not exist on this code path.
    if referred_by is not None:
        bonus = django_settings.CLASSROOM_REFERRAL_JOIN_BONUS_COINS
        if bonus > 0:
            locked_student = type(student).objects.select_for_update().get(pk=student.pk)
            locked_student.coin += bonus
            locked_student.save(update_fields=["coin"])
            CoinTransaction.objects.create(
                user=locked_student,
                txn_type=CoinTransaction.TxnType.CREDIT,
                reason=CoinTransaction.Reason.CLASS_REFERRAL_JOIN_BONUS,
                amount=bonus,
                balance_after=locked_student.coin,
                reference_id=f"class_referral_join:{purchase.id}",
            )

    return purchase


# NOTE (fix): the old module-level _refund_purchase() (lump-sum refund +
# clawback out of the teacher's wallet) used to live here. It's gone now —
# PassPurchase.reverse() in models.py replaces it entirely under the
# per-day escrow design (refunds only remaining_balance, no teacher
# clawback, notification queued from inside the model). Both call sites
# (ClassroomViewSet.close() and PassPurchaseViewSet.refund(), plus the new
# PassPurchaseViewSet.cancel()) now call purchase.reverse() directly.


# ---------------------------------------------------------------------------
# 5B. CLASS JOIN REQUEST (the only door in — see ClassJoinRequest's
#     docstring in models.py. A student never buys a pass directly: they
#     raise a request here, and the classroom's teacher/co-teacher/
#     moderator accepts or rejects it. Coins are charged, and the
#     PassPurchase that actually grants access is created, ONLY inside
#     accept() below.)
# ---------------------------------------------------------------------------
class ClassJoinRequestViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ClassJoinRequestSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a popular classroom's join-request inbox grows unbounded
    # over the classroom's lifetime (every request ever raised against it,
    # accepted/rejected/cancelled included) — same class of gap as the
    # session/purchase/chat/ledger lists above.
    pagination_class = TuitionClassPagination
    # NOTE (perf): student/classroom_title/class_pass_title/class_pass_price
    # are all nested or dotted-source fields on every row.
    queryset = ClassJoinRequest.objects.select_related("student", "classroom", "class_pass")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user

        # NOTE (fix): this filtering is only correct for the LIST action.
        # It used to run unconditionally, which meant get_object() (used by
        # retrieve/accept/reject/cancel) filtered through it too. Detail
        # calls like POST join-requests/{id}/accept/ don't carry a
        # ?classroom= query param, so classroom_id was always None on those
        # calls, and every request fell through to `qs.filter(student=user)`
        # — which is never true for the teacher accepting/rejecting someone
        # ELSE's request. Result: accept()/reject() 404'd ("not found") on
        # requests that genuinely existed and that the teacher/co-teacher/
        # moderator had every right to act on, even though the explicit
        # _can_manage_classroom() permission check further down in accept()/
        # reject() would have allowed it. Scoping this filter to `list` only
        # fixes that; detail actions now get the full queryset and rely on
        # the explicit permission checks (added to retrieve() below, and
        # already present in accept()/reject()/cancel()) instead.
        if self.action != "list":
            return qs

        classroom_id = self.request.query_params.get("classroom")

        if classroom_id:
            qs = qs.filter(classroom_id=classroom_id)
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            # Teacher/co-teacher/moderator reviewing their own classroom's
            # inbox sees every request raised against it; anyone else only
            # ever sees the ones they personally raised.
            if classroom and _can_manage_classroom(classroom, user):
                status_filter = self.request.query_params.get("status")
                return qs.filter(status=status_filter) if status_filter else qs
            return qs.filter(student=user)

        # No ?classroom= filter -> "my requests" across every classroom.
        return qs.filter(student=user)

    def retrieve(self, request, *args, **kwargs):
        # NOTE (fix): get_queryset() no longer restricts detail lookups (see
        # above), so this view is now the only thing standing between "any
        # authenticated user" and "any join request by id" — it previously
        # relied entirely on the list-style queryset filtering for that,
        # which is exactly what broke accept()/reject(). Explicit check here:
        # the student who raised it, or a manager of that classroom, only.
        instance = self.get_object()
        user = request.user
        if instance.student_id != user.id and not _can_manage_classroom(instance.classroom, user):
            raise PermissionDenied("You don't have permission to view this join request.")
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        class_pass = serializer.validated_data["class_pass"]
        user = self.request.user

        # NOTE (fix): neither classroom.is_active nor class_pass.is_active
        # was checked here — a student could raise (and a teacher could
        # still accept/charge for) a join request against a deactivated
        # classroom or a pass the teacher had paused/retired.
        if not classroom.is_active:
            raise ValidationError("This classroom is no longer active.")
        if not class_pass.is_active:
            raise ValidationError("This pass is no longer available.")
        if classroom.teacher_id == user.id:
            raise ValidationError("You can't raise a join request against your own classroom.")
        # NOTE (fix — ClassroomBan had no teeth in the join-request path):
        # without this a banned student could just raise a fresh join
        # request against a NEW pass immediately after being banned — the
        # ban only ever meant "your current access is revoked", not
        # "you can never come back".
        if classroom.bans.filter(student_id=user.id).exists():
            raise ValidationError("You've been banned from this classroom by the teacher.")
        if classroom.has_access(user):
            raise ValidationError("You already have access to this classroom.")
        if ClassJoinRequest.objects.filter(
            classroom=classroom, student=user, status=ClassJoinRequest.Status.PENDING
        ).exists():
            raise ValidationError("You already have a pending request for this classroom.")

        # -------------------------------------------------------------
        # BALANCE GATE: a join request is only allowed when the student can
        # actually pay for the pass (after coupon) right now. Previously the
        # request was always created and the "insufficient coin balance"
        # error only surfaced when the teacher tried to accept it. accept()
        # still re-checks under a row lock (balance can change between
        # request and accept), this is the early, user-facing check.
        # Free passes (0 coins) skip it.
        # -------------------------------------------------------------
        coupon = _preview_coupon_for_pass(serializer.validated_data.get("coupon_code", ""), class_pass)
        coins_needed = _coins_for_pass(class_pass, coupon)
        if coins_needed > 0:
            balance = int(get_user_model().objects.filter(pk=user.pk).values_list("coin", flat=True).first() or 0)
            if balance < coins_needed:
                raise ValidationError({
                    "coin": (
                        f"Insufficient coin balance — this pass needs {coins_needed} coins "
                        f"and you have {balance}. Add {coins_needed - balance} more coins to send the request."
                    )
                })

        # -------------------------------------------------------------
        # FEATURE (refer & earn): decode the ?ref= code the student's link
        # carried (see ClassroomViewSet.refer_link / referral_code field on
        # the serializer) into the referring user, right here at request
        # time — not at accept() time — so a teacher later flipping
        # referral_enabled off can't retroactively erase who actually sent
        # this student. accept() re-checks referral_enabled itself before
        # deciding whether to actually pay out (see _charge_and_create_purchase),
        # this is just about correctly recording who gets credit if it's
        # still on when that happens.
        # -------------------------------------------------------------
        referral_code = serializer.validated_data.pop("referral_code", "")
        referred_by = None
        if referral_code:
            if not classroom.referral_active:
                raise ValidationError({"referral_code": "This classroom doesn't have referrals enabled."})
            referrer_id = referral_code_to_user_id(referral_code)
            if referrer_id is None:
                raise ValidationError({"referral_code": "Invalid referral code."})
            if referrer_id == user.id:
                raise ValidationError({"referral_code": "You can't refer yourself."})
            referred_by = get_user_model().objects.filter(pk=referrer_id).first()
            if referred_by is None:
                raise ValidationError({"referral_code": "Invalid referral code."})
            # TASK 12: also record link-based attribution (first touch wins),
            # so this student's LATER purchases elsewhere (test series) also
            # credit the same referrer. Best effort — never blocks the request.
            try:
                from user_profile.fraud import check_referral_abuse

                if check_referral_abuse(referred_by, user, coins=0)[0]:
                    ReferralAttribution.claim(
                        referee=user, referrer=referred_by,
                        source_type=ReferralAttribution.SourceType.CLASSROOM, source_id=classroom.id,
                    )
            except Exception:  # noqa: BLE001
                logger.exception("referral attribution claim failed")
        elif classroom.referral_active:
            # TASK 12 / 12.3 + T-REF: no ?ref= on THIS request, but the student may have opened THIS class's link
            # earlier (and still be inside the attribution window) — that referrer gets credit. A link to some other
            # class / test series / the app never counts here.
            _attr = ReferralAttribution.active_for(user, commissionable_only=True)
            if (
                _attr is not None
                and _attr.referrer_id != user.id
                and _attr.source_type == ReferralAttribution.SourceType.CLASSROOM
                and str(_attr.source_id) == str(classroom.id)
            ):
                referred_by = _attr.referrer

        # NOTE (fix — race): the exists() check above and serializer.save()
        # below aren't atomic against each other — two identical "raise a
        # join request" calls fired at the same instant (double-tap on a
        # slow connection, a retried request) could both pass this check
        # before either insert lands, and the second one would then hit the
        # DB's own one-pending-per-classroom-per-student unique constraint
        # as a raw IntegrityError -> unhandled 500 instead of the same
        # clean "you already have a pending request" 400 the first caller
        # would have gotten. Caught and converted here.
        try:
            join_request = serializer.save(student=user, referred_by=referred_by)
        except IntegrityError:
            raise ValidationError("You already have a pending request for this classroom.")

        # REALTIME (Flutter Phase 1, item 3 — live pending-count badge):
        # push straight to the teacher's user-scoped WS channel so
        # ClassroomDetailScreen's manage-sheet badge can go +1 immediately
        # instead of waiting for the screen to reopen/poll. This is
        # DIFFERENT from broadcast_to_session (targets everyone in a *live
        # session*'s group) — a join request can be raised with no session
        # running at all, so it has to be a per-user channel. See
        # _safe_broadcast_to_user()'s docstring — broadcast_to_user() now
        # exists in realtime.py.
        _safe_broadcast_to_user(
            classroom.teacher_id,
            "join_request.created",
            {
                "id": join_request.id,
                "classroom_id": classroom.id,
                "classroom_title": classroom.title,
                "student_id": user.id,
                "student_name": user.get_full_name() or user.username,
                "pending_count": classroom.join_requests.filter(
                    status=ClassJoinRequest.Status.PENDING
                ).count(),
            },
        )

        create_notification(
            recipient=classroom.teacher,
            notif_type=Notification.NotifType.JOIN_REQUEST_RECEIVED,
            title="New join request",
            message=f"{user.get_full_name() or user.username} wants to join '{classroom.title}'.",
            classroom=classroom,
        )
        # NOTE (fix): in-app row only got created above — the teacher got no
        # push if the app wasn't open. Queued so a slow/failing push provider
        # never adds latency here (same reasoning as every other
        # notify_*.delay() call site in this file).
        from ..tasks import notify_join_request_received

        _safe_delay(notify_join_request_received, join_request.id)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Teacher/co-teacher/moderator only. Charges the student's coin
        wallet (if the pass isn't free) and creates the PassPurchase that
        actually grants access — nothing is charged and nobody becomes a
        participant until this runs."""
        join_request = self.get_object()
        if not _can_manage_classroom(join_request.classroom, request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can accept a join request."
            )
        if join_request.status != ClassJoinRequest.Status.PENDING:
            raise ValidationError(f"This request is already {join_request.get_status_display().lower()}.")

        decision = ClassJoinRequestDecisionSerializer(data=request.data)
        decision.is_valid(raise_exception=True)
        note = decision.validated_data.get("note", "")

        with transaction.atomic():
            join_request = ClassJoinRequest.objects.select_for_update().get(pk=join_request.pk)
            if join_request.status != ClassJoinRequest.Status.PENDING:
                raise ValidationError(f"This request is already {join_request.get_status_display().lower()}.")

            # NOTE (fix): class_pass itself wasn't locked — only the join
            # request and (inside _charge_and_create_purchase) the coupon
            # and wallets were. That left a real window for a teacher's
            # ClassPassViewSet.perform_update/perform_destroy to race a
            # student's accept() here: e.g. the pass gets edited (or
            # deactivated) in between this view reading join_request's
            # class_pass and the price actually being charged, so the
            # student could be charged a stale price, or a purchase could
            # land against a pass mid-edit. Row-locking it here means
            # ClassPassViewSet's own select_for_update (see that view) waits
            # for this transaction to commit before it can decide whether
            # "active purchases" exist, and vice versa — the two paths can
            # no longer interleave.
            class_pass = ClassPass.objects.select_for_update().get(pk=join_request.class_pass_id)
            if not class_pass.is_active:
                raise ValidationError("This pass is no longer available.")

            # NOTE (fix — charge-after-close fraud): perform_create() checks
            # classroom.is_active when the REQUEST is first raised, but
            # accept() itself never re-checked it. A teacher could call
            # classrooms/{id}/close/ (refunds every current student, marks
            # the classroom inactive — the supported "I'm done running
            # this" path) and then still accept() any join requests left
            # pending from before the close, charging a brand-new student
            # real coins for a classroom the teacher has already declared
            # they've stopped running.
            #
            # Row-locked the same way class_pass/coupon/wallets already are
            # in this function (see the earlier NOTE (fix, race) above) so
            # close()'s is_active write can't land in the gap between this
            # read and the charge actually committing — whichever of
            # close()/accept() gets here first now makes the other wait,
            # instead of both racing off a stale "still active" snapshot.
            classroom = Classroom.objects.select_for_update().get(pk=join_request.classroom_id)
            if not classroom.is_active or classroom.is_deleted:
                raise ValidationError("This classroom is no longer active — the request can't be accepted.")

            # FEATURE (refer & earn): only actually pay a commission if the
            # classroom STILL has referrals on right now — re-checked here
            # (not just trusted from request time) for the same reason
            # is_active is re-checked above: a teacher turning referrals
            # off between the request and the accept shouldn't obligate
            # them to a rate they've since disabled. join_request.referred_by
            # itself is untouched either way — see the NOTE above where
            # it's set in perform_create for why that's recorded regardless.
            # TASK 12 / 12.4: re-run the fraud rules at payout-attribution time
            # (referrer may have been disabled / become circular since request).
            _ref_user = join_request.referred_by if classroom.referral_active else None
            if _ref_user is not None:
                from user_profile.fraud import check_referral_abuse

                if not check_referral_abuse(_ref_user, join_request.student, coins=0)[0]:
                    _ref_user = None
            purchase = _charge_and_create_purchase(
                student=join_request.student,
                class_pass=class_pass,
                coupon_code=join_request.coupon_code,
                referred_by=_ref_user,
                referral_commission_percent=(
                    classroom.referral_commission_percent if classroom.referral_active else Decimal("0")
                ),
            )
            join_request.status = ClassJoinRequest.Status.ACCEPTED
            join_request.pass_purchase = purchase
            join_request.decided_by = request.user
            join_request.decision_note = note
            join_request.decided_at = timezone.now()
            join_request.save(
                update_fields=["status", "pass_purchase", "decided_by", "decision_note", "decided_at"]
            )

        # REALTIME (Flutter Phase 1, item 2 — pending -> active without
        # reopening the app): student's own ClassroomDetailScreen should
        # flip out of "Request Sent — Waiting for Approval" the instant
        # this commits.
        _safe_broadcast_to_user(
            join_request.student_id,
            "join_request.decided",
            {
                "id": join_request.id,
                "classroom_id": join_request.classroom_id,
                "status": ClassJoinRequest.Status.ACCEPTED,
                "access_level": AccessLevel.ACTIVE,
            },
        )

        create_notification(
            recipient=join_request.student,
            notif_type=Notification.NotifType.JOIN_REQUEST_ACCEPTED,
            title="Join request accepted",
            message=f"You now have access to '{join_request.classroom.title}'.",
            classroom=join_request.classroom,
        )
        # NOTE (fix): student got no push telling them access just landed —
        # queued the same way every other notify_*.delay() call site in this
        # file is.
        from ..tasks import notify_join_request_accepted

        _safe_delay(notify_join_request_accepted, join_request.id)
        return Response(ClassJoinRequestSerializer(join_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Teacher/co-teacher/moderator only. No charge, no PassPurchase."""
        join_request = self.get_object()
        if not _can_manage_classroom(join_request.classroom, request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can reject a join request."
            )
        if join_request.status != ClassJoinRequest.Status.PENDING:
            raise ValidationError(f"This request is already {join_request.get_status_display().lower()}.")

        decision = ClassJoinRequestDecisionSerializer(data=request.data)
        decision.is_valid(raise_exception=True)

        join_request.status = ClassJoinRequest.Status.REJECTED
        join_request.decided_by = request.user
        join_request.decision_note = decision.validated_data.get("note", "")
        join_request.decided_at = timezone.now()
        join_request.save(update_fields=["status", "decided_by", "decision_note", "decided_at"])

        # REALTIME (Flutter Phase 1, item 2 — pending -> none without
        # reopening the app).
        _safe_broadcast_to_user(
            join_request.student_id,
            "join_request.decided",
            {
                "id": join_request.id,
                "classroom_id": join_request.classroom_id,
                "status": ClassJoinRequest.Status.REJECTED,
                "access_level": AccessLevel.NONE,
            },
        )

        create_notification(
            recipient=join_request.student,
            notif_type=Notification.NotifType.JOIN_REQUEST_REJECTED,
            title="Join request rejected",
            message=f"Your request to join '{join_request.classroom.title}' was rejected.",
            classroom=join_request.classroom,
        )
        from ..tasks import notify_join_request_rejected

        _safe_delay(notify_join_request_rejected, join_request.id)
        return Response(ClassJoinRequestSerializer(join_request).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """The requesting student can withdraw their own still-pending
        request (e.g. changed their mind, or want to re-request against a
        different pass — cancelling frees up the one-pending-per-classroom
        slot for a fresh request)."""
        join_request = self.get_object()
        if join_request.student_id != request.user.id:
            raise PermissionDenied("You can only cancel your own join request.")
        if join_request.status != ClassJoinRequest.Status.PENDING:
            raise ValidationError(f"This request is already {join_request.get_status_display().lower()}.")

        join_request.status = ClassJoinRequest.Status.CANCELLED
        join_request.decided_at = timezone.now()
        join_request.save(update_fields=["status", "decided_at"])

        return Response(ClassJoinRequestSerializer(join_request).data)


# ---------------------------------------------------------------------------
# 5A3. PASS GIFT (Pass 14 — "Gifting a pass"). The ViewSet was the missing
# piece: PassGift (models.py) and PassGiftSerializer (serializers.py)
# already existed, but nothing ever exposed create/claim/cancel over the
# API. Mirrors _charge_and_create_purchase's coin-debit shape above, minus
# coupon/referral (see PassGift's docstring in models.py for why gifting
# deliberately doesn't stack with either).
#
#   GET    pass-gifts/            own gifts — sent AND received
#   GET    pass-gifts/{id}/
#   POST   pass-gifts/            send a gift (body: recipient_id, class_pass, gift_message)
#   POST   pass-gifts/{id}/claim/   recipient only, while PENDING and not expired
#   POST   pass-gifts/{id}/cancel/  gifter only, while still PENDING (refunds the gifter)
# ---------------------------------------------------------------------------
class PassGiftViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    serializer_class = PassGiftSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination
    queryset = PassGift.objects.select_related(
        "gifter", "recipient", "class_pass", "class_pass__classroom", "purchase"
    )

    def get_queryset(self):
        # Own gifts only — sent AND received, same "no browsing other
        # people's gifts" boundary as everywhere else money moves in this
        # app (e.g. PassPurchaseViewSet's own-purchases-only default).
        user = self.request.user
        qs = super().get_queryset().filter(Q(gifter=user) | Q(recipient=user))

        # FIX (audit): the frontend (tuitionclass_api_service.dart ->
        # PassGiftApi.myGiftsSent()/myGiftsReceived()) has always sent
        # ?direction=sent|received expecting this to split the "own
        # gifts" queryset above into sent-only / received-only — this
        # filter never existed, so both calls silently returned the
        # same unfiltered sent+received mix. Restricted to gifter=user /
        # recipient=user (not an arbitrary id) so this can't be used to
        # peek at someone else's gifts via the query param.
        direction = self.request.query_params.get("direction")
        if direction == "sent":
            qs = qs.filter(gifter=user)
        elif direction == "received":
            qs = qs.filter(recipient=user)
        return qs

    def perform_create(self, serializer):
        class_pass = serializer.validated_data["class_pass"]
        recipient = serializer.validated_data["recipient"]
        gifter = self.request.user

        classroom = class_pass.classroom
        if not classroom.is_active or classroom.is_deleted:
            raise ValidationError({"class_pass": "This classroom is no longer active."})
        if not class_pass.is_active:
            raise ValidationError({"class_pass": "This pass is no longer available."})
        # FIX (frontend cross-check): pass_management_screen.dart's
        # per-pass "Allow gifting" toggle had nothing enforcing it
        # server-side — see ClassPass.allow_gifting's doc comment in
        # models.py.
        if not class_pass.allow_gifting:
            raise ValidationError({"class_pass": "The teacher has disabled gifting for this pass."})

        # Same rounding rule as _charge_and_create_purchase above — a
        # fractional coin price is never left to truncate in either
        # side's favor.
        coins_spent = int(class_pass.price.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

        with transaction.atomic():
            if coins_spent > 0:
                # The gifter pays NOW (not the recipient, and not at claim
                # time) — see PassGift's docstring in models.py for why:
                # they need to be committed before the recipient ever sees
                # the gift, so they can't back out after the fact.
                user = type(gifter).objects.select_for_update().get(pk=gifter.pk)
                if user.coin < coins_spent:
                    raise ValidationError({"coin": "Insufficient coin balance to gift this pass."})
                # S9: this legacy path writes CoinTransaction directly (not CoinLedger.record_transaction), so the
                # user's spend limit / minor cap is checked here explicitly. Before any debit; raises -> 400.
                from user_profile.models import SpendLimitExceeded
                from user_profile.wallet_settings import check_spend_allowed

                try:
                    check_spend_allowed(gifter.pk, coins_spent, "gift_sent")
                except SpendLimitExceeded as exc:
                    raise ValidationError({"coin": str(exc), "code": "spend_limit"})
                user.coin -= coins_spent
                user.save(update_fields=["coin"])
                CoinTransaction.objects.create(
                    user=user,
                    txn_type=CoinTransaction.TxnType.DEBIT,
                    reason=CoinTransaction.Reason.GIFT_SENT,
                    amount=coins_spent,
                    balance_after=user.coin,
                    reference_id=f"class_pass:{class_pass.id}",
                )

            gift = PassGift.objects.create(
                gifter=gifter,
                recipient=recipient,
                class_pass=class_pass,
                coins_spent=coins_spent,
                gift_message=serializer.validated_data.get("gift_message", ""),
                expires_at=timezone.now() + timezone.timedelta(days=PassGift.CLAIM_WINDOW_DAYS),
            )

        serializer.instance = gift

        create_notification(
            recipient=recipient,
            notif_type=Notification.NotifType.PASS_GIFT_RECEIVED,
            title="You've received a class pass gift!",
            message=(
                f"{gifter.get_full_name() or gifter.username} gifted you a pass "
                f"for '{classroom.title}'."
            ),
            classroom=classroom,
        )
        from ..tasks import notify_pass_gift_received

        _safe_delay(notify_pass_gift_received, gift.id)

    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        """Recipient only. Only NOW is the PassPurchase actually created
        and the escrow clock started (validity_days counts from claim, not
        from send) — same "don't start a clock the recipient hasn't
        agreed to yet" reasoning as an unactivated gift card, see
        PassGift's docstring in models.py."""
        gift = self.get_object()
        if gift.recipient_id != request.user.id:
            raise PermissionDenied("Only the recipient can claim this gift.")
        if gift.status != PassGift.Status.PENDING:
            raise ValidationError(f"This gift is already {gift.get_status_display().lower()}.")
        if timezone.now() > gift.expires_at:
            raise ValidationError("This gift has expired and can no longer be claimed.")

        with transaction.atomic():
            gift = PassGift.objects.select_for_update().get(pk=gift.pk)
            if gift.status != PassGift.Status.PENDING:
                raise ValidationError(f"This gift is already {gift.get_status_display().lower()}.")
            if timezone.now() > gift.expires_at:
                raise ValidationError("This gift has expired and can no longer be claimed.")

            class_pass = ClassPass.objects.select_for_update().get(pk=gift.class_pass_id)
            if not class_pass.is_active:
                raise ValidationError("This pass is no longer available and can't be claimed.")

            per_day_rate = Decimal(gift.coins_spent) / class_pass.validity_days
            purchase = PassPurchase.objects.create(
                student=gift.recipient,
                class_pass=class_pass,
                payment_method=(
                    PassPurchase.PaymentMethod.FREE
                    if gift.coins_spent == 0
                    else PassPurchase.PaymentMethod.COIN_WALLET
                ),
                amount_paid=Decimal(gift.coins_spent),
                coins_spent=gift.coins_spent,
                per_day_rate=per_day_rate,
                status=PassPurchase.Status.SUCCESS,
                expires_at=timezone.now() + timezone.timedelta(days=class_pass.validity_days),
                auto_renew=False,
            )
            gift.status = PassGift.Status.CLAIMED
            gift.purchase = purchase
            gift.claimed_at = timezone.now()
            gift.save(update_fields=["status", "purchase", "claimed_at"])

        create_notification(
            recipient=gift.gifter,
            notif_type=Notification.NotifType.PASS_GIFT_CLAIMED,
            title="Your gift was claimed",
            message=(
                f"{gift.recipient.get_full_name() or gift.recipient.username} "
                f"claimed your gifted pass for '{class_pass.classroom.title}'."
            ),
            classroom=class_pass.classroom,
        )
        from ..tasks import notify_pass_gift_claimed

        _safe_delay(notify_pass_gift_claimed, gift.id)
        return Response(PassGiftSerializer(gift).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Gifter only, while still unclaimed. Refunds the gifter's coins
        — see PassGift.refund_to_gifter()."""
        gift = self.get_object()
        if gift.gifter_id != request.user.id:
            raise PermissionDenied("Only the gifter can cancel this gift.")
        if gift.status != PassGift.Status.PENDING:
            raise ValidationError(f"This gift is already {gift.get_status_display().lower()}.")

        with transaction.atomic():
            gift = PassGift.objects.select_for_update().get(pk=gift.pk)
            if gift.status != PassGift.Status.PENDING:
                raise ValidationError(f"This gift is already {gift.get_status_display().lower()}.")
            if gift.coins_spent > 0:
                gift.refund_to_gifter()
            gift.status = PassGift.Status.CANCELLED
            gift.save(update_fields=["status"])

        return Response(PassGiftSerializer(gift).data)


# ---------------------------------------------------------------------------
# 12. COUPON
# ---------------------------------------------------------------------------
class CouponViewSet(viewsets.ModelViewSet):
    """NOTE (fix): this was wide open —
    1. No get_queryset override at all: GET /coupons/ returned EVERY
       coupon on the whole platform (code, discount, classroom, created_by)
       to any signed-in user — competitors could enumerate each other's
       discount codes.
    2. perform_create only stamped created_by; there was no check that the
       caller actually owns the target classroom (or is null/'usable
       across all of my classrooms' for their own account only) — anyone
       could create a coupon against someone else's classroom.
    3. There were no perform_update/perform_destroy checks whatsoever — any
       authenticated user could edit ANY coupon (e.g. set discount_percent
       to 100, making every pass on that classroom free) or delete anyone
       else's coupons. This is a direct financial exploit, fixed by
       restricting every write to the coupon's own creator.
    List is now scoped to "coupons I created" (optionally narrowed by
    ?classroom=<id>, still only within the caller's own coupons) — a
    classroom's *listing* page doesn't need to show live coupon codes
    (that's what /coupons/validate/ is for); only the teacher managing
    their own promos does.
    """

    serializer_class = CouponSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination
    # NOTE (fix): same as ClassSessionViewSet above — the `validate` action
    # below passes throttle_scope="coupon_validate" as an @action kwarg,
    # which DRF's router validates against `hasattr(cls, key)` at
    # url-import time. Without this base attribute the whole app fails to
    # boot. Value is irrelevant — the action overrides it per-request.
    throttle_scope = None
    # NOTE (fix — N+1 query): CouponSerializer nests `created_by` as a full
    # UserMiniSerializer (read_only), not a plain FK id — without
    # select_related, DRF fires one extra user lookup per coupon row while
    # serializing. The list here is already scoped to the caller's own
    # coupons (small), but a teacher managing several classrooms' promos at
    # once can still have enough rows for this to add up, and it's free —
    # folds the same lookup into the original query via a JOIN.
    queryset = Coupon.objects.select_related("created_by")

    def get_queryset(self):
        qs = Coupon.objects.filter(created_by=self.request.user).select_related("created_by", "classroom")
        classroom_id = self.request.query_params.get("classroom")
        return qs.filter(classroom_id=classroom_id) if classroom_id else qs

    def perform_create(self, serializer):
        classroom = serializer.validated_data.get("classroom")
        # classroom may legitimately be null (a coupon usable across every
        # classroom the caller teaches) — only block creating one against
        # a classroom the caller does NOT teach/co-teach/moderate.
        if classroom is not None and not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied("You can only create coupons for classrooms you teach or manage.")
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        if serializer.instance.created_by_id != self.request.user.id:
            raise PermissionDenied("Only the coupon's creator can edit it.")
        classroom = serializer.validated_data.get("classroom", serializer.instance.classroom)
        if classroom is not None and not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied("You can only assign coupons to classrooms you teach or manage.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.created_by_id != self.request.user.id:
            raise PermissionDenied("Only the coupon's creator can delete it.")
        instance.delete()

    # NOTE (abuse): coupon codes are short, guessable strings — without a
    # rate limit this endpoint is a free oracle for brute-forcing valid
    # codes (every guess returns a clean 200/404 with no cost). Scoped
    # throttle; add e.g. {"coupon_validate": "20/min"} to
    # DEFAULT_THROTTLE_RATES in settings.py.
    @action(
        detail=False,
        methods=["get"],
        throttle_classes=[ScopedRateThrottle],
        throttle_scope="coupon_validate",
    )
    def validate(self, request):
        """GET coupons/validate/?code=XYZ&classroom=<id> — checks a code
        without spending it, so the frontend can show the discount before
        buyPass() commits. Registered as a list-level action so it resolves
        before the coupons/{pk}/ detail route.

        NOTE (fix): accepts an OPTIONAL ?classroom= now, checked against the
        same classroom-scoping rule enforced at actual redemption time in
        _charge_and_create_purchase (see that function's NOTE (fix)) — kept
        optional (rather than required) so this doesn't break existing
        frontend callers that only ever sent ?code=; but any caller that
        does pass ?classroom= gets an accurate preview instead of seeing
        "valid" for a coupon that accept() would then actually reject.
        """
        code = request.query_params.get("code")
        classroom_id = request.query_params.get("classroom")
        if not code:
            raise ValidationError({"code": "This field is required."})
        coupon = Coupon.objects.filter(code__iexact=code, is_active=True).first()
        if not coupon or not coupon.is_valid():
            raise NotFound("Invalid or expired coupon code.")
        if classroom_id:
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            if not classroom:
                raise NotFound("Classroom not found.")
            scoped_to_this_classroom = coupon.classroom_id == classroom.id
            scoped_to_creators_other_classroom = (
                coupon.classroom_id is None and coupon.created_by_id == classroom.teacher_id
            )
            if not (scoped_to_this_classroom or scoped_to_creators_other_classroom):
                raise NotFound("This coupon isn't valid for this classroom.")
        return Response(CouponSerializer(coupon).data)
