"""
tuitionclass/views/coins.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import hashlib
import hmac
import logging
import uuid
from decimal import Decimal
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.db.models import Count, F, Sum
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from ..models import (
    Classroom,
    CoinPurchase,
    CoinTransaction,
    CoinWithdrawal,
    PassDailyCharge,
    PassPurchase,
    Referral,
    ReferralAttribution,
    ReferralCommission,
    referral_code_for_user,
    referral_code_to_user_id,
)
from ..serializers import (
    ClassReferralSummarySerializer,
    CoinPurchaseSerializer,
    CoinTransactionSerializer,
    CoinWithdrawalSerializer,
    MyReferralCodeSerializer,
    ReferralAttributeSerializer,
    ReferralCommissionSerializer,
    ReferralSerializer,
    TeacherEarningsSerializer,
)

from ._common import TuitionClassPagination


# ---------------------------------------------------------------------------
# 13. COIN TRANSACTION (read-only; own ledger only)
# ---------------------------------------------------------------------------
class CoinTransactionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CoinTransactionSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (production): an active user's coin ledger grows on every single
    # purchase/refund/credit — unpaginated, a heavy user's "wallet history"
    # screen would eventually have to download thousands of rows at once.
    pagination_class = TuitionClassPagination

    def get_queryset(self):
        return CoinTransaction.objects.filter(user=self.request.user)

    @action(detail=False, methods=["get"])
    def balance(self, request):
        """Real, authoritative coin balance — reads User.coin directly
        instead of asking the client to derive it from the latest ledger
        row. This is the source of truth even for a user with coins but
        zero CoinTransaction rows (e.g. a signup bonus credited without a
        ledger entry), which the "latest transaction's balance_after"
        approach used elsewhere in the app cannot represent correctly.
        Documented in urls.py; was previously undocumented-but-missing."""
        return Response({"coin": request.user.coin})


# ---------------------------------------------------------------------------
# 13C. COIN WITHDRAWAL — cash-out of a real, earned coin balance to a bank
# account / UPI id. See CoinWithdrawal in models.py for the full design
# note (why coins are debited at request time, not on approval).
#
# Coin TOP-UP (buying coins with real money) is deliberately out of scope
# here — this viewset only covers the withdrawal direction.
# ---------------------------------------------------------------------------
class CoinWithdrawalViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """TASK 6 — READ-ONLY as of this pass. Withdrawal *requests* now go
    through `user_profile`'s `CoinWithdrawalRequestView` (Task 4), which
    writes through `CoinLedger.objects.record_transaction()` — the one
    shared ledger every coin-changing action across the codebase should
    use, per that model's own docstring. Creating (and cancel/approve/
    reject/mark-paid) here would keep debiting/crediting `User.coin`
    through this app's own bespoke path, which is exactly the two
    parallel, driftable ledgers this migration exists to collapse into
    one.

    `list`/`retrieve` stay wired so existing "my withdrawal history" UI
    keeps working unchanged against pre-migration rows. Every
    state-changing action below now returns 410 Gone with a pointer at
    the new endpoint instead of touching a balance — see
    `_deprecated_write_response` above `CoinPurchaseViewSet`.

    Own withdrawal requests only, UNLESS the caller is platform staff
    (`is_staff`), who see every request across every user — staff are the
    ones who actually action a payout, so they need the full queue, not
    just their own wallet. Same "?classroom= opts a manager into a wider
    queryset" shape used by PassPurchaseViewSet.get_queryset, but here the
    escalation is a plain is_staff check since there's no per-classroom
    scoping concept for a wallet payout."""

    serializer_class = CoinWithdrawalSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination

    def get_queryset(self):
        base = CoinWithdrawal.objects.select_related("user", "reviewed_by")
        if self.request.user.is_staff:
            status_filter = self.request.query_params.get("status")
            if status_filter:
                base = base.filter(status=status_filter)
            return base
        return base.filter(user=self.request.user)

    def create(self, request):
        # NOTE: not a DRF CreateModelMixin.create — this is a plain method
        # (no @action; "create" is a reserved router route name and can't
        # be used with @action) so POST .../coin-withdrawals/ stays
        # registered and old clients get a clear redirect instead of a
        # 404, without wiring CreateModelMixin back onto this viewset.
        return _deprecated_write_response("/api/user-profile/coin-withdrawals/")

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return _deprecated_write_response("/api/user-profile/coin-withdrawals/")

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return _deprecated_write_response("/api/user-profile/coin-withdrawals/")

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return _deprecated_write_response("/api/user-profile/coin-withdrawals/")

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        return _deprecated_write_response("/api/user-profile/coin-withdrawals/")


# ---------------------------------------------------------------------------
# 13D. COIN PURCHASE — real-money -> coin top-up. See CoinPurchase in
# models.py for why this exists (TOPUP was a dead enum value until now).
#
# GATEWAY INTEGRATION: the two functions below are the ONLY places a real
# payment gateway needs to be wired in. Written against Razorpay's shape
# (order-create + HMAC-signature verify) since it's the most common choice
# for an Indian platform, but nothing else in this feature (the model, the
# viewset actions, retry logic) is Razorpay-specific — swap these two
# functions for a different gateway's SDK calls and everything above them
# keeps working unchanged.
# ---------------------------------------------------------------------------
# TASK 6: `_create_gateway_order` / `_verify_gateway_signature` are no
# longer called from this file now that `initiate`/`verify`/`retry` are
# deprecated stubs above — left in place (not deleted) since
# `tasks.reconcile_stuck_coin_purchases` (referenced in
# CoinPurchaseViewSet's old docstring, not part of this upload) may still
# import and use them against pre-migration PENDING rows. Confirm that
# task's own status before deleting these.
def _create_gateway_order(amount_inr, receipt: str) -> str:
    """STUB — replace with a real order-create call, e.g.:
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        order = client.order.create({"amount": int(amount_inr * 100), "currency": "INR", "receipt": receipt})
        return order["id"]
    Returns a locally-unique id for now so the initiate/verify/retry flow
    (and its tests) is fully exercisable without a live gateway account —
    verify() will simply always fail signature checking until the real
    call + RAZORPAY_KEY_SECRET (see §10) are both in place.
    """
    return f"order_{uuid.uuid4().hex[:20]}"


def _verify_gateway_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """STUB (partially real) — this IS Razorpay's actual verification
    algorithm (HMAC-SHA256 of "{order_id}|{payment_id}" keyed with your
    key secret), so once RAZORPAY_KEY_SECRET is set in settings and
    `_create_gateway_order` above is calling the real API, this function
    needs no further changes. Fails closed (returns False) if the secret
    isn't configured, rather than silently trusting an unverifiable
    payload."""
    secret = getattr(django_settings, "RAZORPAY_KEY_SECRET", None)
    if not secret:
        logging.getLogger(__name__).warning(
            "RAZORPAY_KEY_SECRET not configured — coin purchase verification "
            "cannot succeed until it is set (see settings §10)."
        )
        return False
    expected = hmac.new(
        secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# TASK 6 — shared by CoinWithdrawalViewSet and CoinPurchaseViewSet below.
# Both apps' write paths now live in `user_profile` (Task 3/4), writing
# through `CoinLedger.objects.record_transaction()` — the one shared
# ledger every coin-changing action should use, per that model's own
# docstring. Every state-changing action on the two viewsets below is
# replaced with this: a 410 Gone that names the replacement endpoint,
# instead of silently 404ing (route removed) or — worse — silently still
# accepting writes into this app's now-deprecated balance path.
def _deprecated_write_response(new_path: str) -> Response:
    return Response(
        {
            "detail": (
                "This action has moved. Coin purchases and withdrawals are now "
                f"handled at {new_path} — this endpoint is read-only history "
                "going forward."
            ),
            "new_endpoint": new_path,
        },
        status=status.HTTP_410_GONE,
    )


class CoinPurchaseViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """TASK 6 — READ-ONLY as of this pass, mirroring CoinWithdrawalViewSet
    above. Buying coins now goes through `user_profile`'s
    `CoinPurchaseRequest` flow (Task 3) — `start_purchase` /
    `confirm_success` / `mark_failed`, writing through `CoinLedger.
    objects.record_transaction()`. `list`/`retrieve` stay wired so
    existing "my purchase history" UI keeps working against
    pre-migration rows; `initiate`/`verify`/`retry` now return 410 Gone
    instead of touching a balance.

    Original three-step flow (kept here for history / what the rows
    below mean): 1) `initiate` created a PENDING `CoinPurchase` + gateway
    order, 2) client paid inside the gateway's checkout UI, 3) `verify`
    checked the signature and credited the wallet exactly once.
    """

    serializer_class = CoinPurchaseSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination

    def get_queryset(self):
        return CoinPurchase.objects.filter(user=self.request.user)

    @action(detail=False, methods=["post"])
    def initiate(self, request):
        return _deprecated_write_response("/api/user-profile/coin-purchases/initiate/")

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        return _deprecated_write_response("/api/user-profile/coin-purchases/")

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        return _deprecated_write_response("/api/user-profile/coin-purchases/initiate/")


# ---------------------------------------------------------------------------
# 13B. REFERRAL PROGRAM
#
# GAP THIS CLOSES: CoinTransaction.Reason.REFERRAL_BONUS existed as an enum
# choice with nothing behind it — no code path ever created a transaction
# with that reason. See Referral / referral_code_for_user /
# referral_code_to_user_id in models.py for the encode/decode scheme, and
# REFERRAL_BONUS_COINS / REFERRAL_REDEEM_WINDOW_DAYS in settings.py for the
# two tunables.
# ---------------------------------------------------------------------------
class ReferralViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """GET /tuitionclass/referrals/ -> people the caller has successfully
    referred (own ledger only, same privacy posture as coin-transactions/).
    `my-code` and `redeem` live on this same viewset since they're the
    same feature, even though they aren't list/retrieve-shaped."""

    serializer_class = ReferralSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination

    def get_queryset(self):
        return Referral.objects.filter(referrer=self.request.user).select_related("referred")

    # P-F16: remember the page so ReferralSerializer can mask referred MINORS with one lookup (see tuitionclass/serializers.py).
    def paginate_queryset(self, queryset):
        page = super().paginate_queryset(queryset)
        self._referral_rows = list(page) if page is not None else []
        return page

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["referral_rows"] = getattr(self, "_referral_rows", [])
        return ctx

    def get_throttles(self):
        # TASK 12: attribute is called on every referral-link open — cap it
        # per user so it can't be used to probe codes.
        if getattr(self, "action", None) == "attribute":
            self.throttle_scope = "referral_attribute"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    # -----------------------------------------------------------------
    # TASK 12 / 12.1 — link-based attribution. First touch wins, expires
    # after REFERRAL_ATTRIBUTION_DAYS. Always answers 200 with
    # {"attributed": bool, "reason": str} for "expected" refusals so the
    # app can silently ignore them (a bad/stale link must never show an
    # error screen); only a malformed body is a 400.
    # -----------------------------------------------------------------
    @action(detail=False, methods=["post"], url_path="attribute")
    def attribute(self, request):
        from user_profile.fraud import check_referral_abuse

        serializer = ReferralAttributeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = request.user

        referrer_id = referral_code_to_user_id(data["code"])
        referrer = get_user_model().objects.filter(pk=referrer_id).first() if referrer_id else None
        if referrer is None:
            return Response({"attributed": False, "reason": "invalid_code"})

        # Someone who already bought something isn't a "new customer" —
        # claiming them would pay commission on a customer the referrer
        # never brought in.
        if user.pass_purchases.exists() or user.testseries_purchases.exists():
            return Response({"attributed": False, "reason": "existing_customer"})

        ok, reason = check_referral_abuse(referrer, user, coins=0)
        if not ok:
            return Response({"attributed": False, "reason": reason})

        attribution, created = ReferralAttribution.claim(
            referee=user, referrer=referrer, source_type=data["source_type"], source_id=data["source_id"]
        )
        return Response(
            {
                "attributed": attribution.referrer_id == referrer.id,
                "reason": "" if attribution.referrer_id == referrer.id else "already_attributed",
                "created": created,
                "expires_at": attribution.expires_at,
            }
        )

    # -----------------------------------------------------------------
    # TASK 12 / 12.5 — everything the "Share & earn" screen needs about
    # money: totals per source + recent PAID commissions.
    # -----------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="earnings")
    def earnings(self, request):
        user = request.user
        paid = ReferralCommission.objects.filter(referrer=user, status=ReferralCommission.Status.PAID)
        by_kind = {
            row["kind"]: row["total"] or 0
            for row in paid.values("kind").annotate(total=Sum("commission_coins"))
        }
        recent = list(paid.select_related("referee")[:30])
        from common import minor_policy  # P-F16: referred minors are masked for a non-linked adult referrer

        _masked = minor_policy.masked_ids_for_viewer(user, [r.referee_id for r in recent], feature=minor_policy.REFERRAL)
        attributed = ReferralAttribution.objects.filter(referrer=user)
        data = {
            "code": referral_code_for_user(user.id),
            "total_commission_earned": sum(by_kind.values()),
            "testseries_commission": by_kind.get(ReferralCommission.Kind.TESTSERIES, 0),
            "classroom_commission": by_kind.get(ReferralCommission.Kind.CLASSROOM, 0),
            "people_attributed": attributed.count(),
            "people_converted": attributed.filter(first_purchase_at__isnull=False).count(),
            "attribution_days": django_settings.REFERRAL_ATTRIBUTION_DAYS,
            "recent": ReferralCommissionSerializer(recent, many=True, context={"masked_minor_ids": _masked}).data,
        }
        return Response(data)

    @action(detail=False, methods=["get"], url_path="my-code")
    def my_code(self, request):
        """Own referral code + a running tally of how many people have
        redeemed it and how many coins that's earned so far."""
        user = request.user
        earned = CoinTransaction.objects.filter(
            user=user, reason=CoinTransaction.Reason.REFERRAL_BONUS
        ).aggregate(total=Sum("amount"))["total"] or 0
        data = {
            "code": referral_code_for_user(user.id),
            "referral_count": Referral.objects.filter(referrer=user).count(),
            "total_bonus_earned": earned,
            "total_commission_earned": ReferralCommission.objects.filter(
                referrer=user, status=ReferralCommission.Status.PAID
            ).aggregate(total=Sum("commission_coins"))["total"] or 0,
        }
        return Response(MyReferralCodeSerializer(data).data)

    # -----------------------------------------------------------------
    # NEW (task 65 — referral-dashboard, "aapne itne log invite kiye"):
    # global counterpart to ClassroomViewSet.referral_dashboard above —
    # this one aggregates across EVERY classroom the caller has ever
    # referred a student into (via any classroom's refer-link), not just
    # one. Kept on ReferralViewSet since it reads the same
    # PassPurchase.referred_by attribution my_code/redeem already deal
    # with, just for the class-level (not signup-level) referral program.
    # -----------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="class-referral-summary")
    def class_referral_summary(self, request):
        user = request.user
        referred_qs = PassPurchase.objects.filter(referred_by=user)
        commission_earned = referred_qs.aggregate(
            total=Sum("referral_coins_released")
        )["total"] or 0
        pending_commission = sum(p.referral_remaining_balance for p in referred_qs)
        per_classroom = (
            referred_qs
            .values(
                "class_pass__classroom_id",
                "class_pass__classroom__title",
                # TASK 2 — classroom's CURRENT rate (teacher can change it
                # any time; this always reflects "what you'd earn today",
                # distinct from `referral_commission_percent` snapshotted
                # per-purchase on PassPurchase, which is the historical
                # rate that actually funded `commission_earned` above).
                "class_pass__classroom__referral_commission_percent",
            )
            .annotate(
                referred_count=Count("student_id", distinct=True),
                commission_earned=Sum("referral_coins_released"),
            )
            .order_by("-commission_earned")
        )
        data = {
            "referral_code": referral_code_for_user(user.id),
            "total_students_referred": referred_qs.values("student_id").distinct().count(),
            "total_commission_earned": commission_earned,
            "total_commission_pending": pending_commission,
            "by_classroom": [
                {
                    "classroom_id": row["class_pass__classroom_id"],
                    "classroom_title": row["class_pass__classroom__title"],
                    "referred_count": row["referred_count"],
                    "commission_earned": row["commission_earned"] or 0,
                    "commission_percent": row["class_pass__classroom__referral_commission_percent"],
                }
                for row in per_classroom
            ],
        }
        return Response(ClassReferralSummarySerializer(data).data)

    @action(detail=False, methods=["post"])
    def redeem(self, request):
        """CLOSED. Refer & earn is now URL-only and creator opt-in: commission comes only from a class / test-series
        link whose owner set a % (see `earnable`). The flat "enter a code, both get bonus coins" flow is gone, so
        this answers 410 Gone and credits nothing. Old app builds that still call it get a clear message instead
        of a 404. Existing Referral rows / past bonus ledger entries are untouched."""
        return Response(
            {
                "detail": (
                    "Referral codes can no longer be redeemed. Share a class or test series from "
                    "Refer & Earn to earn commission."
                ),
                "new_endpoint": "/referrals/earnable/",
            },
            status=status.HTTP_410_GONE,
        )

    # -----------------------------------------------------------------
    # GET /referrals/earnable/ - what the caller can share to earn commission.
    #
    # ONLY items whose creator opted in WITH a % > 0 (Classroom.referral_active /
    # testseries.access.series_is_referable) - an item with commission off, or on at 0%, is simply not listed.
    # Everyone sees the same list, minus their own classes / test series (you can't earn on your own sale).
    # Each item carries a ready-to-share URL that already has the caller's `?ref=<code>`; opening it attributes
    # the visitor to this exact item (first touch, see ReferralAttribution), which is the only way commission is
    # earned. Query: ?q=<text> (title search), ?limit=<1..100, default 50> per section.
    # -----------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="earnable")
    def earnable(self, request):
        from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

        from testseries.access import series_is_referable, series_referral_percent
        from testseries.models import TestSeries

        user = request.user
        code = referral_code_for_user(user.id)
        q = (request.query_params.get("q") or "").strip()[:100]
        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 100))
        except (TypeError, ValueError):
            limit = 50

        def with_ref(url: str) -> str:
            parts = urlparse(url)
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "ref"]
            query.append(("ref", code))
            return urlunparse(parts._replace(query=urlencode(query)))

        classes_qs = (
            Classroom.objects.filter(
                referral_enabled=True,
                referral_commission_percent__gt=0,
                is_active=True,
                is_deleted=False,
                is_flagged=False,
                is_personal=False,
            )
            .exclude(teacher=user)
            .select_related("teacher")
        )
        series_qs = (
            TestSeries.objects.filter(
                source=TestSeries.Source.INDIVIDUAL,
                status=TestSeries.Status.PUBLISHED,
                is_paid=True,
                price_coins__gt=0,
                referral_enabled=True,
                referral_commission_percent__gt=0,
            )
            .exclude(creator=user)
            .select_related("creator")
        )
        if q:
            classes_qs = classes_qs.filter(title__icontains=q)
            series_qs = series_qs.filter(title__icontains=q)

        classes = []
        for c in classes_qs.order_by("-referral_commission_percent", "-created_at")[:limit]:
            web_url, deep_link = c.referral_urls(user)
            classes.append(
                {
                    "type": "classroom",
                    "id": str(c.id),
                    "title": c.title,
                    "owner_name": getattr(c.teacher, "get_full_name", lambda: "")() or c.teacher.get_username(),
                    "commission_percent": str(Decimal(c.referral_commission_percent).quantize(Decimal("0.01"))),
                    "commission_basis": "daily",
                    "share_url": web_url,
                    "deep_link": deep_link,
                }
            )

        test_series = []
        # series_is_referable() re-checks every rule in python (single source of truth for payouts); the queryset
        # above is only the cheap prefilter, so a rule change in access.py can never make this list drift.
        for s in series_qs.order_by("-referral_commission_percent", "-created_at")[:limit]:
            if not series_is_referable(s):
                continue
            slug = s.ensure_share_slug(save=True)
            base = getattr(django_settings, "TESTSERIES_SHARE_URL_TEMPLATE", "") or ""
            if base:
                url = base.format(slug=slug)
            else:
                web = (getattr(django_settings, "PUBLIC_WEB_BASE_URL", "") or "").rstrip("/")
                url = f"{web}/test-series/{slug}/" if web else ""
            if not url:
                continue
            test_series.append(
                {
                    "type": "testseries",
                    "id": str(s.id),
                    "title": s.title,
                    "owner_name": getattr(s.creator, "get_full_name", lambda: "")() or s.creator.get_username(),
                    "commission_percent": str(series_referral_percent(s).quantize(Decimal("0.01"))),
                    "commission_basis": "per_user",
                    "price_coins": s.price_coins,
                    "share_url": with_ref(url),
                    "deep_link": "",
                }
            )

        return Response({"classes": classes, "test_series": test_series})


# ---------------------------------------------------------------------------
# TEACHER EARNINGS DASHBOARD
#
# GAP THIS CLOSES: ClassroomViewSet.stats (above) is a public listing-card
# summary (rating/timing/enrolled_count) — it has nothing about money.
# PassDailyCharge is the actual per-day escrow release ledger (see
# PassPurchase.charge_for_session in models.py), so it's the right source
# to aggregate a teacher's real earnings from, instead of re-deriving it
# from CoinTransaction (which mixes class earnings with refunds/referral
# bonuses/etc. for the SAME user and would need reason-filtering + could
# still double count if a teacher is also someone else's student).
# ---------------------------------------------------------------------------
class TeacherEarningsView(APIView):
    """GET /tuitionclass/my-earnings/ — optionally ?classroom=<id> to scope to
    one classroom (must be a classroom the caller teaches; a co-teacher/
    moderator does not themselves earn anything under the current escrow
    design — see PassPurchase.charge_for_session, which pays
    classroom.teacher only — so this is teacher-only, not staff-visible)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        charges = PassDailyCharge.objects.filter(purchase__class_pass__classroom__teacher=user)

        classroom_id = request.query_params.get("classroom")
        if classroom_id:
            classroom = Classroom.objects.filter(pk=classroom_id, teacher=user).first()
            if classroom is None:
                raise PermissionDenied("You don't teach this classroom.")
            charges = charges.filter(purchase__class_pass__classroom=classroom)

        totals = charges.aggregate(total=Sum("amount"), sessions=Count("id"))
        total_earned = totals["total"] or 0
        total_sessions_charged = totals["sessions"] or 0

        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        this_month_earned = (
            charges.filter(date__gte=month_start).aggregate(total=Sum("amount"))["total"] or 0
        )

        thirty_days_ago = timezone.now().date() - timezone.timedelta(days=29)
        daily = (
            charges.filter(date__gte=thirty_days_ago)
            .values("date")
            .annotate(amount=Sum("amount"))
            .order_by("date")
        )

        by_classroom = (
            charges.values(
                classroom_id=F("purchase__class_pass__classroom_id"),
                classroom_title=F("purchase__class_pass__classroom__title"),
            )
            .annotate(total_earned=Sum("amount"), sessions_charged=Count("id"))
            .order_by("-total_earned")
        )

        data = {
            "total_earned": total_earned,
            "total_sessions_charged": total_sessions_charged,
            "this_month_earned": this_month_earned,
            "last_30_days": list(daily),
            "by_classroom": list(by_classroom),
        }
        return Response(TeacherEarningsSerializer(data).data)
