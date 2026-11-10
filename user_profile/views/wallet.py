# user_profile/views/wallet.py — split out of the old user_profile/views.py (P-C4). Code is moved verbatim.

import hashlib
import hmac
from rest_framework.permissions import (
    AllowAny,
    IsAdminUser,
    IsAuthenticated,
)
from ..models import (
    CoinLedger,
    CoinLedgerBusy,
    CoinPurchaseRequest,
    CoinWithdrawalRequest,
    WithdrawalNotEligible,
)
from ..serializers import (
    CoinLedgerSerializer,
    CoinPurchaseConfirmSerializer,
    CoinPurchaseRequestSerializer,
    CoinWithdrawalActionSerializer,
    CoinWithdrawalAdminSerializer,
    CoinWithdrawalRequestSerializer,
)
from ..throttles import (
    CoinPurchaseBurstThrottle,
    CoinPurchaseDailyThrottle,
)
from rest_framework.generics import (
    GenericAPIView,
    ListAPIView,
)
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from drf_spectacular.types import OpenApiTypes
from django.http.request import RawPostDataException
from rest_framework.response import Response
from login.sensitive import (
    SensitiveWriteThrottle,
    record_change as _x6_record_change,
)
from rest_framework.throttling import UserRateThrottle
from common.open_throttles import GatewayWebhookThrottle  # P-B3
from common.feature_flags import (
    cashout_disabled_response as _cashout_disabled_response,
    coin_cashout_enabled as _coin_cashout_enabled,
)
from login import audit as _x6_audit
from .. import fraud
from django.conf import settings
from rest_framework import status
from ._common import (
    _wallet_busy_response,
    logger,
)


# TASK 19 — coin transaction history
# Read-only, deliberately (see CoinLedgerSerializer's docstring for why
# there's no POST here). The actual write path —
# `CoinLedger.objects.record_transaction()` — is called from wherever a
# coin-changing action happens (a purchase completing in the tuitionclass
# app, a gift being sent in the message app, an admin adjustment
# endpoint if/when one gets built); none of those views were part of
# this upload, so this is the read side only: "let me see why my
# balance is what it is", which had no path at all before this.
class CoinLedgerListView(ListAPIView):
    """
    GET /profile/coin-ledger/

    The authenticated user's own coin transaction history, newest
    first (CoinLedger.Meta.ordering already gives us that for free).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CoinLedgerSerializer

    def get_queryset(self):
        return CoinLedger.objects.filter(user=self.request.user)

    @extend_schema(
        responses={200: CoinLedgerSerializer(many=True)},
        description="List of the current user's coin transaction history (newest first).",
    )
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
        else:
            serializer = self.get_serializer(queryset, many=True)
            data = serializer.data

        return Response({
            "status": True,
            "message": "Coin transaction history fetched successfully.",
            "data": data,
        }, status=status.HTTP_200_OK)


# 🔥 TASK 3 — Buy-Coin flow
# Two-step, same shape as any pending -> confirmed payment flow: this
# view only ever creates/returns a PENDING `CoinPurchaseRequest` — it
# never touches `User.coin`. The actual credit happens in
# `BuyCoinConfirmView` below, via `CoinPurchaseRequest.objects.
# confirm_success()`, which is the only path that calls
# `CoinLedger.objects.record_transaction()` for a purchase.
class BuyCoinView(GenericAPIView):
    """
    POST /profile/buy-coin/
    {"gateway_reference": "<gateway's txn id>", "amount": "99.00", "coins": 100, "gateway": "razorpay"}

    Starts a coin purchase. Idempotent on `gateway_reference`: calling
    this again with the same reference returns the existing request
    (whatever its current status) instead of creating a duplicate — safe
    for a client retrying after a dropped response.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CoinPurchaseRequestSerializer
    # DB-bloat guard: keeps the global per-user default AND adds the two
    # purchase-specific limits (see user_profile/throttles.py).
    throttle_classes = [UserRateThrottle, CoinPurchaseBurstThrottle, CoinPurchaseDailyThrottle]

    @extend_schema(
        request=CoinPurchaseRequestSerializer,
        responses={201: CoinPurchaseRequestSerializer, 200: CoinPurchaseRequestSerializer, 429: OpenApiTypes.OBJECT},
        description="Start a coin purchase (creates a pending request; idempotent on "
        "gateway_reference). Does not credit coins — see /buy-coin/confirm/.",
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # Cap on unconfirmed rows per user. A retry of an EXISTING reference
        # is idempotent (creates nothing), so it is never blocked by the cap.
        max_pending = getattr(settings, "COIN_PURCHASE_MAX_PENDING_PER_USER", 20)
        if (
            not CoinPurchaseRequest.objects.filter(
                gateway=data.get("gateway", ""), gateway_reference=data["gateway_reference"],
            ).exists()
            and CoinPurchaseRequest.objects.filter(
                user=request.user, status=CoinPurchaseRequest.Status.PENDING,
            ).count() >= max_pending
        ):
            return Response({
                "status": False,
                "message": "Too many pending purchases. Complete or wait for the "
                           "existing ones before starting a new one.",
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)

        purchase, created = CoinPurchaseRequest.objects.start_purchase(
            user=request.user,
            gateway_reference=data["gateway_reference"],
            amount=data["amount"],
            coins=data["coins"],
            gateway=data.get("gateway", ""),
        )

        # `gateway_reference` is globally unique (it's the gateway's own
        # id), so if it already exists under a DIFFERENT user, this is
        # either a client bug or a replayed/guessed reference — never
        # silently let the caller read or "adopt" someone else's pending
        # purchase.
        if purchase.user_id != request.user.id:
            return Response({
                "status": False,
                "message": "This gateway_reference is already associated with another purchase.",
            }, status=status.HTTP_409_CONFLICT)

        return Response({
            "status": True,
            "message": "Coin purchase started." if created
            else "Coin purchase already exists for this reference.",
            "data": self.get_serializer(purchase).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class GatewayWebhookSignatureError(Exception):
    """
    Raised by `_verify_gateway_webhook_signature()` below for any
    signature failure — missing secret (misconfiguration), missing
    header, or a mismatch. Callers turn this into an HTTP response;
    kept as one exception type (not three) so the view's except-block
    can't accidentally leak *which* of the three failed to the caller —
    "invalid signature" is the only thing a webhook caller should ever
    learn either way.
    """

    def __init__(self, message, *, is_misconfiguration=False):
        super().__init__(message)
        self.is_misconfiguration = is_misconfiguration


def _verify_gateway_webhook_signature(request, gateway, raw_body=None):
    """
    TASK (this pass) — user_profile_app_reference.md §11 item 10:
    `BuyCoinConfirmView` had no real payment-gateway signature check,
    only `IsAuthenticated` + "must be your own purchase" standing in for
    one, since no gateway integration was part of any upload for this
    app.

    ⚠️ ASSUMPTION — `campus`/`tuitionclass`'s own gateway-verify code (the
    reference the person doing this task pointed at) was NOT part of
    this pass's upload either, so this isn't copied from an established
    in-repo pattern — it's a generic, gateway-agnostic HMAC-SHA256
    webhook-signature check, the same mechanism every major payment
    gateway (Razorpay, Stripe, PayU, ...) uses for webhook auth, just
    without any one gateway's specific header name/payload-canonicalization
    quirks baked in (those differ per gateway and aren't confirmable from
    here). If `campus`/`tuitionclass` turns out to already have gateway
    client code with its own verification helper, prefer reusing that
    over this — this exists so the endpoint isn't left unverified in the
    meantime, not to duplicate a real gateway SDK's verification call.

    How it works: HMAC-SHA256 over the raw request body, keyed by a
    per-gateway secret, compared against a per-gateway signature header
    — both looked up from two new settings this pass introduces (NOT
    added to settings.py in this pass — out of scope for a views.py-only
    change; see this function's docstring for the exact shape needed):

        PAYMENT_GATEWAY_WEBHOOK_SECRETS = {
            "razorpay": os.environ["RAZORPAY_WEBHOOK_SECRET"],
            ...
        }
        PAYMENT_GATEWAY_WEBHOOK_SIGNATURE_HEADERS = {
            "razorpay": "X-Razorpay-Signature",
            ...
        }
        # Falls back to "X-Webhook-Signature" for any gateway not listed
        # in the headers map above.

    `gateway` is read from the ALREADY-PERSISTED `CoinPurchaseRequest.
    gateway` (set back when `BuyCoinView.post()` created the pending
    request), never from anything in this webhook call itself — a
    request body can claim to be from any gateway it likes, but it can
    only produce a signature that verifies against the secret this
    server has on file for the gateway `start_purchase()` was actually
    given.

    Raises `GatewayWebhookSignatureError` for every failure case (no
    secret configured, no signature header present, signature mismatch)
    — see that class's own docstring for why these three collapse into
    one exception/one message rather than three distinguishable ones.
    Returns None (no exception) on success.
    """
    if not gateway:
        # A blank `gateway` is a real, valid state (CoinPurchaseRequest.
        # gateway's own field comment: "not every caller may have a
        # gateway name handy (e.g. a manual admin-initiated top-up)")
        # — but exactly BECAUSE it's blank, there is no gateway secret
        # to verify a signature against, so a request with no gateway on
        # file can never be confirmed through this now-webhook-only
        # endpoint. See BuyCoinConfirmView's own docstring — this is a
        # deliberate, flagged behavior change from before this pass
        # (when IsAuthenticated + ownership let ANY caller, gateway-less
        # purchases included, confirm their own request), not an
        # oversight.
        raise GatewayWebhookSignatureError(
            "This purchase has no gateway on file — it cannot be confirmed "
            "via a gateway webhook.",
            is_misconfiguration=True,
        )

    secrets_by_gateway = getattr(settings, "PAYMENT_GATEWAY_WEBHOOK_SECRETS", {})
    secret = secrets_by_gateway.get(gateway)
    if not secret:
        # Distinct from "signature didn't match" — this is OUR config
        # missing an entry for a gateway we otherwise recognize, not the
        # caller's fault. Surfaced as 503 by the view, not 401/403.
        raise GatewayWebhookSignatureError(
            f"No webhook secret configured for gateway {gateway!r}.",
            is_misconfiguration=True,
        )

    headers_by_gateway = getattr(settings, "PAYMENT_GATEWAY_WEBHOOK_SIGNATURE_HEADERS", {})
    header_name = headers_by_gateway.get(gateway, "X-Webhook-Signature")
    provided_signature = request.headers.get(header_name, "")
    if not provided_signature:
        raise GatewayWebhookSignatureError(
            f"Missing {header_name!r} header."
        )

    # `request.body` (raw bytes, pre-parsing) — the caller MUST have
    # already forced Django to cache this (e.g. by reading `request.body`
    # once) before ever touching `request.data`. Once DRF parses
    # `request.data` first, the underlying stream is already consumed and
    # Django raises `RawPostDataException` on a later `.body` access — see
    # BuyCoinConfirmView.post()'s own comment on why it reads `.body`
    # before `self.get_serializer(data=request.data)`, not after.
    # Prefer the bytes the view already captured (`raw_body`) so this never
    # depends on a second `.body` access succeeding.
    body_bytes = raw_body if raw_body is not None else request.body
    expected_signature = hmac.new(
        secret.encode("utf-8"), body_bytes, hashlib.sha256,
    ).hexdigest()

    # `compare_digest`, not `==` — constant-time, so a caller can't use
    # response-timing differences to guess the correct signature one
    # byte at a time. Compared as BYTES: `compare_digest(str, str)` raises
    # TypeError if either str contains a non-ASCII character, and the
    # header value is attacker-controlled — a header like "é" would turn
    # a plain 401 into an unhandled 500.
    if not hmac.compare_digest(
        provided_signature.encode("utf-8"), expected_signature.encode("utf-8"),
    ):
        raise GatewayWebhookSignatureError("Signature verification failed.")


class BuyCoinConfirmView(GenericAPIView):
    """
    POST /profile/buy-coin/confirm/
    {"gateway_reference": "<gateway's txn id>", "status": "success", "failure_reason": ""}

    Confirms a pending purchase as successful (credits `coins` through
    `CoinLedger.objects.record_transaction()`) or failed (wallet
    untouched). Idempotent and safe to call more than once for the same
    `gateway_reference` — see `CoinPurchaseRequest.objects.
    confirm_success()`/`mark_failed()` (models.py) for exactly what
    happens on a repeat call.

    TASK (this pass) — user_profile_app_reference.md §11 item 10: this is
    now a real (gateway-agnostic) webhook endpoint. `IsAuthenticated` +
    "must be your own purchase" is GONE — a genuine webhook call isn't
    "acting as" any particular authenticated user, so that check could
    never be the real gate here; it only ever worked because nothing in
    this upload could call this endpoint except a logged-in client
    testing the flow end-to-end. `permission_classes = [AllowAny]` now,
    gated instead by `_verify_gateway_webhook_signature()` above — see
    that function's own docstring for exactly what it checks and its
    ⚠️ ASSUMPTION about not having `campus`/`tuitionclass`'s own
    gateway-verify code to copy from.

    Behavior change worth flagging explicitly: a `CoinPurchaseRequest`
    with a BLANK `gateway` (the "manual admin-initiated top-up" case
    `CoinPurchaseRequest.gateway`'s own field comment names) could
    previously be confirmed by its owning user calling this endpoint
    themselves. It no longer can be — there's no gateway secret to
    verify a webhook signature against a blank gateway, and this
    endpoint's whole reason to exist now is verifying a real gateway
    webhook, not accepting a client's say-so.

    TASK 16 — that gap now has a replacement path: `AdminCoinPurchaseConfirmView`
    below is a separate, `IsAdminUser`-gated endpoint for exactly the
    blank-`gateway` case this view can no longer serve. See that view's
    own docstring for why it's a new endpoint rather than a permission
    branch inside this one. `BuyCoinView`, `CoinPurchaseRequest`,
    `CoinPurchaseRequestManager` are all otherwise unchanged.
    """
    permission_classes = [AllowAny]
    # P-B3: a webhook has no user JWT -- a stale/expired Authorization header must not turn a signed call into a 401,
    # and the 20/min anon default must not 429 a gateway retry burst. The HMAC signature is the only credential.
    authentication_classes: list = []
    throttle_classes = [GatewayWebhookThrottle]
    serializer_class = CoinPurchaseConfirmSerializer

    @extend_schema(
        request=CoinPurchaseConfirmSerializer,
        responses={200: CoinPurchaseRequestSerializer, 404: OpenApiTypes.OBJECT},
        description="Gateway webhook: confirm a coin purchase as success or failed. "
        "Signature-verified — not callable as a regular authenticated user "
        "action. Idempotent — safe to retry (e.g. a duplicated webhook delivery).",
    )
    def post(self, request, gateway=None):
        # MUST happen before `self.get_serializer(data=request.data)`
        # below — see `_verify_gateway_webhook_signature()`'s own comment
        # on why. Forces Django to cache the raw body now, while the
        # stream hasn't been read yet, so DRF's later `request.data`
        # parse reads from that cached copy instead of consuming the
        # stream directly — without this ordering, the signature check
        # further down would hit Django's `RawPostDataException` instead
        # of a raw body to hash.
        #
        # If something upstream (a middleware, a proxy layer) already
        # consumed the stream WITHOUT caching it, `.body` raises
        # RawPostDataException. We can't verify an HMAC without the raw
        # bytes, so fail CLOSED with a clean 400 (the gateway will retry)
        # instead of an unhandled 500.
        try:
            raw_body = request.body
        except RawPostDataException:
            logger.error("buy-coin webhook: raw body unavailable (stream already consumed)")
            return Response({
                "status": False,
                "message": "Could not read the request body.",
            }, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        gateway_reference = serializer.validated_data["gateway_reference"]
        outcome = serializer.validated_data["status"]

        # A gateway's transaction id is only unique WITHIN that gateway, so
        # the same string can exist on two purchases. Which one is meant is
        # decided by the gateway-specific URL (/buy-coin/confirm/<gateway>/ —
        # configured by us in the gateway's webhook settings, not by the
        # caller) or, failing that, an optional `gateway` in the body. With
        # neither, the reference must identify exactly one purchase.
        # The hint only NARROWS the lookup; the signature below is still
        # verified against the secret of the purchase's own recorded gateway,
        # so it can't be used to confirm anything without that gateway's key.
        gateway_hint = (gateway or serializer.validated_data.get("gateway") or "").strip().lower()
        matches = CoinPurchaseRequest.objects.filter(gateway_reference=gateway_reference)
        if gateway_hint:
            matches = matches.filter(gateway=gateway_hint)
        matches = list(matches.order_by("pk")[:2])
        if not matches:
            return Response({
                "status": False,
                "message": "Coin purchase request not found.",
            }, status=status.HTTP_404_NOT_FOUND)
        if len(matches) > 1:
            return Response({
                "status": False,
                "message": "This reference exists on more than one gateway — use "
                           "/buy-coin/confirm/<gateway>/ to say which.",
            }, status=status.HTTP_409_CONFLICT)
        purchase = matches[0]

        # Replaces the old `purchase.user_id != request.user.id` ownership
        # check — see class docstring. Keyed off `purchase.gateway` (this
        # server's own record of which gateway the purchase was started
        # against), never off anything the caller claims in this request.
        try:
            _verify_gateway_webhook_signature(request, purchase.gateway, raw_body=raw_body)
        except GatewayWebhookSignatureError as exc:
            if exc.is_misconfiguration:
                # Our config's fault (no secret on file / no gateway to
                # verify against at all), not the caller's — 503, not
                # 401/403, and safe to say so explicitly since it's not a
                # signature-guessing hint.
                return Response({
                    "status": False,
                    "message": str(exc),
                }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            # Deliberately generic message for every other failure
            # (missing header, bad signature) — never confirms/denies
            # *which* part was wrong to an unauthenticated caller.
            return Response({
                "status": False,
                "message": "Webhook signature verification failed.",
            }, status=status.HTTP_401_UNAUTHORIZED)

        try:
            if outcome == "success":
                purchase = CoinPurchaseRequest.objects.confirm_success(
                    gateway_reference=gateway_reference, gateway=purchase.gateway,
                )
                message = "Coin purchase confirmed and wallet credited."
            else:
                purchase = CoinPurchaseRequest.objects.mark_failed(
                    gateway_reference=gateway_reference,
                    reason=serializer.validated_data.get("failure_reason", ""),
                    gateway=purchase.gateway,
                )
                message = "Coin purchase marked as failed."
        except CoinLedgerBusy:
            return _wallet_busy_response()
        except ValueError as exc:
            # confirm_success()/mark_failed() raise this for an invalid
            # state transition (e.g. trying to fail an already-succeeded
            # purchase) — a 409, not a 400: the request body was valid,
            # the request's current state just doesn't allow this move.
            return Response({
                "status": False,
                "message": str(exc),
            }, status=status.HTTP_409_CONFLICT)

        return Response({
            "status": True,
            "message": message,
            "data": CoinPurchaseRequestSerializer(purchase).data,
        }, status=status.HTTP_200_OK)


# 🔥 TASK 16 — manual/admin-initiated top-up confirm path.
# BuyCoinConfirmView above went webhook-only for §11 item 10's signature
# hardening, which closed a real hole (any authenticated caller could
# confirm their own purchase with no proof money ever moved) but also
# closed off the one legitimate no-gateway case CoinPurchaseRequest.
# gateway's own field comment names: a manual/admin-initiated top-up,
# where there was never going to be a gateway webhook to verify in the
# first place. This view is that case's replacement confirm path —
# staff-only, separate from the webhook endpoint, not a loosening of it.
class AdminCoinPurchaseConfirmView(GenericAPIView):
    """
    POST /profile/buy-coin/admin-confirm/
    {"gateway_reference": "<manual/internal reference>", "status": "success", "failure_reason": ""}

    Confirms a manual/admin-initiated top-up (a `CoinPurchaseRequest`
    with a BLANK `gateway`) as successful or failed. Staff-only
    (`IsAdminUser`) — same "give ops an API instead of a Django shell"
    pattern `CoinWithdrawalAdminActionView` above already uses.

    Deliberately a SEPARATE endpoint from `BuyCoinConfirmView`, not a
    permission-class change on it. `BuyCoinConfirmView`'s entire reason
    to exist post-item-10 is verifying a real gateway webhook signature
    (`AllowAny` + HMAC — see `_verify_gateway_webhook_signature()`); an
    admin escape hatch folded into that same view would blur "verified
    gateway webhook" and "trusted staff override" into one code path,
    undoing the point of that hardening. Keeping them separate also
    means a compromised/misused staff account can't be used to forge a
    gateway webhook signature — it can only ever touch gateway-less
    purchases (see the scope restriction below), never one a real
    gateway is expected to confirm.

    Scope restriction: ONLY confirms purchases with a BLANK `gateway`
    on file. A `CoinPurchaseRequest` that DOES have a gateway must
    still go through the real webhook (`BuyCoinConfirmView`) — letting
    staff manually confirm a gateway-backed purchase here would let
    anyone with staff access credit coins without the gateway ever
    having actually taken payment, which is exactly the risk item 10's
    signature check exists to prevent. A gateway-backed purchase gets a
    409, not a silent success, if pointed at this endpoint.

    Otherwise mirrors `BuyCoinConfirmView`'s own contract: idempotent
    and safe to retry (`confirm_success()`/`mark_failed()`, models.py),
    404 if `gateway_reference` doesn't match any request, 409 for an
    invalid state transition (e.g. failing an already-succeeded
    purchase).
    """
    permission_classes = [IsAdminUser]
    serializer_class = CoinPurchaseConfirmSerializer

    @extend_schema(
        request=CoinPurchaseConfirmSerializer,
        responses={
            200: CoinPurchaseRequestSerializer,
            400: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
            409: OpenApiTypes.OBJECT,
        },
        description="Staff-only. Confirm a manual/admin-initiated top-up "
        "(a CoinPurchaseRequest with no gateway on file) as success or failed. "
        "Gateway-backed purchases are rejected here (409) — they must go "
        "through the gateway webhook (/buy-coin/confirm/) instead.",
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        gateway_reference = serializer.validated_data["gateway_reference"]
        outcome = serializer.validated_data["status"]

        # Only ever look at BLANK-gateway rows: a gateway-backed purchase that
        # merely shares this reference string must never be picked up here.
        purchase = CoinPurchaseRequest.objects.filter(
            gateway_reference=gateway_reference, gateway=""
        ).first()
        if purchase is None:
            if CoinPurchaseRequest.objects.filter(gateway_reference=gateway_reference).exists():
                return Response({
                    "status": False,
                    "message": "This purchase has a gateway on file and must be "
                    "confirmed via the gateway webhook (/buy-coin/confirm/), not "
                    "the admin manual-confirm endpoint.",
                }, status=status.HTTP_409_CONFLICT)
            return Response({
                "status": False,
                "message": "Coin purchase request not found.",
            }, status=status.HTTP_404_NOT_FOUND)

        if purchase.gateway:
            # See class docstring's scope restriction — a gateway-backed
            # purchase must be confirmed by that gateway's own webhook,
            # never by a staff member's say-so.
            return Response({
                "status": False,
                "message": "This purchase has a gateway on file and must be "
                "confirmed via the gateway webhook (/buy-coin/confirm/), not "
                "the admin manual-confirm endpoint.",
            }, status=status.HTTP_409_CONFLICT)

        try:
            if outcome == "success":
                purchase = CoinPurchaseRequest.objects.confirm_success(
                    gateway_reference=gateway_reference, gateway="",
                )
                message = "Coin purchase confirmed and wallet credited (manual admin action)."
            else:
                purchase = CoinPurchaseRequest.objects.mark_failed(
                    gateway_reference=gateway_reference,
                    reason=serializer.validated_data.get("failure_reason", ""),
                    gateway="",
                )
                message = "Coin purchase marked as failed (manual admin action)."
        except CoinLedgerBusy:
            return _wallet_busy_response()
        except ValueError as exc:
            # Invalid state transition (e.g. failing an already-succeeded
            # purchase) — same 409 shape BuyCoinConfirmView uses above.
            return Response({
                "status": False,
                "message": str(exc),
            }, status=status.HTTP_409_CONFLICT)

        return Response({
            "status": True,
            "message": message,
            "data": CoinPurchaseRequestSerializer(purchase).data,
        }, status=status.HTTP_200_OK)


# 🔥 TASK 4 — Withdraw-Coin flow
# Mirror image of BuyCoinView/BuyCoinConfirmView above (money direction
# reversed), but ONE view instead of two: CoinWithdrawalRequestManager.
# request_withdrawal() debits the coins the moment the request is made
# (escrow-style — see its own docstring in models.py for why), so
# there's no separate "confirm" step the way a coin purchase needs one.
# mark_processing()/confirm_success()/reject() (models.py) aren't wired
# to an endpoint in this pass — same "views weren't part of this
# upload" scope-out CoinLedger's docstring already applies to the
# actions that would eventually create a ledger entry from outside this
# app; here it applies to whatever admin/ops surface will eventually
# call those three.
class CoinWithdrawalRequestView(GenericAPIView):
    """
    GET  /profile/coin-withdrawals/            -> current user's own withdrawal requests
    POST /profile/coin-withdrawals/ {"coins": 200, "payout_method": "upi", "payout_details": {"upi_id": "a@bank"}}

    Debits `coins` immediately via CoinWithdrawalRequestManager.
    request_withdrawal(). Insufficient balance is a clean 402 with no
    partial debit and no request row left behind — the debit and the
    row creation share one transaction.atomic() block inside the
    manager, so a ValueError there (bubbled up from CoinLedger.objects.
    record_transaction) rolls both back together.

    TASK 5 (this pass): before any of that, `fraud.
    is_withdrawal_eligible(request.user, coins)` is checked. This is a
    read-only check — it runs BEFORE `request_withdrawal()`, so a
    rejection here never touches the balance and never creates a
    request row (satisfies the "balance must not shrink" acceptance
    criterion directly, rather than relying on a rollback). Distinct
    from the 402 below on purpose: 402 means "you don't have enough
    coins, period"; this is "you have enough coins, but not enough
    *withdrawal-eligible* ones" — a different, policy-level rejection,
    so it gets its own 403 rather than reusing 402's "add more funds"
    implication.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CoinWithdrawalRequestSerializer
    # X6 -- money leaves the wallet here: POSTs capped per hour (GET list is free), a refusal is audited.
    throttle_classes = [UserRateThrottle, SensitiveWriteThrottle]  # + the project-wide per-user floor
    throttle_scope = "withdrawal_request"

    @extend_schema(
        responses={200: CoinWithdrawalRequestSerializer(many=True)},
        description="List of the current user's coin withdrawal requests (newest first).",
    )
    def get(self, request):
        qs = CoinWithdrawalRequest.objects.filter(user=request.user)
        serializer = self.get_serializer(qs, many=True)
        return Response({
            "status": True,
            "message": "Withdrawal requests fetched successfully.",
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=CoinWithdrawalRequestSerializer,
        responses={
            201: CoinWithdrawalRequestSerializer,
            402: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT,
        },
        description="Request a coin withdrawal. Only coins purchased or received as a gift are "
        "withdrawal-eligible (403 if the requested amount isn't covered by eligible coins); "
        "debits the wallet immediately once eligible, with insufficient balance returning 402 "
        "and no partial debit or request row left behind either way.",
    )
    def post(self, request):
        # P-G1: cash-out kill switch (settings.COIN_CASHOUT_ENABLED, default OFF). First line on purpose: nothing below runs
        # -- no validation, no fraud lookup, no debit, no request row -- while cash-out is off.
        if not _coin_cashout_enabled():
            _x6_record_change(  # X6 -- just that a request hit the closed door; never an amount or payout details
                _x6_audit.Event.WITHDRAWAL_BLOCKED, request, request.user, outcome=_x6_audit.BLOCKED,
                reason="feature_disabled",
            )
            return _cashout_disabled_response()

        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # TASK 5: fraud/eligibility check runs before any coins move —
        # see the class docstring above for why this is a 403, distinct
        # from the 402 below.
        is_eligible, reason = fraud.is_withdrawal_eligible(request.user, data["coins"])
        if not is_eligible:
            _x6_record_change(  # X6 -- no amount, no payout details: just that a request was refused
                _x6_audit.Event.WITHDRAWAL_BLOCKED, request, request.user, outcome=_x6_audit.BLOCKED,
                reason="not_eligible",
            )
            return Response({
                "status": False,
                "message": reason,
            }, status=status.HTTP_403_FORBIDDEN)

        try:
            withdrawal = CoinWithdrawalRequest.objects.request_withdrawal(
                user=request.user,
                coins=data["coins"],
                payout_method=data.get("payout_method", ""),
                payout_details=data.get("payout_details", {}),
            )
        except WithdrawalNotEligible as exc:
            # The under-lock re-check inside request_withdrawal() (the one
            # the pre-check above can't make race-proof) rejected it: same
            # policy 403 as the pre-check, no debit, no row.
            return Response({
                "status": False,
                "message": str(exc),
            }, status=status.HTTP_403_FORBIDDEN)
        except CoinLedgerBusy:
            return _wallet_busy_response()
        except ValueError as exc:
            # Insufficient balance — 402, same "request was well-formed,
            # the wallet just can't cover it" shape campus's
            # FeePaymentViewSet.pay uses. Distinct from the 409s above
            # (BuyCoinConfirmView) which are for an invalid *state
            # transition*, not a money shortfall.
            return Response({
                "status": False,
                "message": str(exc),
            }, status=status.HTTP_402_PAYMENT_REQUIRED)

        _x6_record_change(  # X6 -- the request id + payout METHOD only; never the amount or the UPI / bank details
            _x6_audit.Event.WITHDRAWAL_REQUESTED, request, request.user,
            withdrawal_id=getattr(withdrawal, "pk", 0) or 0, payout_method=str(data.get("payout_method", ""))[:20],
        )
        return Response({
            "status": True,
            "message": "Withdrawal requested successfully.",
            "data": self.get_serializer(withdrawal).data,
        }, status=status.HTTP_201_CREATED)


# TASK 1 (feature: Admin Coin-Withdrawal Review Dashboard) — the "see
# the queue" half that was still missing: `CoinWithdrawalAdminActionView`
# below already lets staff ACT on a withdrawal by id, but nothing
# exposed the list of requests to act on over the API — only the Django
# admin (`CoinWithdrawalRequestAdmin`, admin.py) or a shell could see
# every user's requests. This is that list, scoped the same way the
# `(status, created_at)` index on the model already anticipates ("an
# admin queue view's PENDING/PROCESSING oldest first" — see that
# index's own comment in models.py).
class CoinWithdrawalAdminListView(ListAPIView):
    """
    GET /profile/coin-withdrawals/admin/
    GET /profile/coin-withdrawals/admin/?status=pending
    GET /profile/coin-withdrawals/admin/?status=pending,processing

    Staff-only (`IsAdminUser`), same permission class
    `CoinWithdrawalAdminActionView` already uses below — this is the
    read side of the same "ops" surface, not a looser one. Paginated by
    the project-wide `StandardPagination` (settings.py
    REST_FRAMEWORK["DEFAULT_PAGINATION_CLASS"]) like every other list
    endpoint in this module, so a growing table never becomes an
    unbounded query.

    `?status=` is optional and accepts a comma-separated list of
    `CoinWithdrawalRequest.Status` values (e.g. `pending,processing` to
    show only what's still actionable); omitted, it returns every
    status, oldest-first within status groups isn't attempted here —
    default ordering is the model's own `-created_at` (newest first),
    which is what an ops dashboard actually wants to land on ("what
    just came in"), NOT the oldest-first sweep order the `(status,
    created_at)` index comment describes for a background task. A
    client that wants the oldest PENDING request first can already get
    that by filtering `?status=pending` and reading from the end of the
    page — no separate `?ordering=` param is added for a queue this
    small.

    An unrecognized value in `?status=` is dropped rather than raising
    a 400 — same "ignore, don't fail, a query param" posture as
    filtering elsewhere in this codebase's list views; it never widens
    the query, it can only narrow it to nothing.
    """

    permission_classes = [IsAdminUser]
    serializer_class = CoinWithdrawalAdminSerializer

    def get_queryset(self):
        qs = CoinWithdrawalRequest.objects.select_related("user", "reviewed_by")
        raw_status = self.request.query_params.get("status", "")
        statuses = [s.strip() for s in raw_status.split(",") if s.strip()]
        valid_statuses = {choice for choice, _ in CoinWithdrawalRequest.Status.choices}
        statuses = [s for s in statuses if s in valid_statuses]
        if statuses:
            qs = qs.filter(status__in=statuses)
        return qs

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="status",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Comma-separated CoinWithdrawalRequest.Status values "
                "(pending, processing, success, rejected). Omit for all statuses.",
            )
        ],
        responses={200: CoinWithdrawalAdminSerializer(many=True)},
        description="Staff-only. Every user's coin withdrawal requests, newest first, "
        "optionally filtered by status — the review queue for "
        "CoinWithdrawalAdminActionView below.",
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# 🔥 §11 item 12 — staff/ops endpoint for CoinWithdrawalRequestManager's
# three lifecycle methods. Before this, mark_processing()/
# confirm_success()/reject() (models.py) existed and were unit-tested,
# but nothing in urls.py called them — a withdrawal could only ever
# reach PENDING through the public API (CoinWithdrawalRequestView
# above); moving it further required a Django shell. This view is the
# "future admin action" that item 12 flagged as not existing yet — it
# doesn't touch CoinWithdrawalRequestView itself (that view still only
# lets a user see/create their OWN requests) and doesn't open any new
# path to CoinLedger: it calls the exact same manager methods
# CoinLedgerAdmin's own docstring (admin.py) points to as the
# sanctioned way to move a withdrawal forward, just reachable over the
# API instead of only from a shell.
class CoinWithdrawalAdminActionView(GenericAPIView):
    """
    POST /profile/coin-withdrawals/<int:withdrawal_id>/action/
    {"action": "processing"}                              -> mark_processing()
    {"action": "success"}                                  -> confirm_success()
    {"action": "reject", "reason": "optional explanation"} -> reject()

    Staff-only (`IsAdminUser` — `request.user.is_staff`). This mirrors
    the level Django admin itself already requires to reach these same
    three methods; it does not add a new, looser way in. Ordinary
    authenticated users keep using `CoinWithdrawalRequestView` above
    for their own requests (GET to list, POST to create) — this view
    has no GET and never filters by `request.user`, since ops needs to
    act on *any* user's withdrawal, not just their own.

    Response codes follow the same conventions the rest of this
    module already uses for the underlying manager methods:
      - 404 if `withdrawal_id` doesn't exist at all.
      - 409 if the manager raises `ValueError` for an invalid state
        transition (e.g. trying to reject an already-SUCCESS request)
        — same "well-formed request, wrong current state" shape
        `BuyCoinConfirmView` already uses above for its own 409s.
      - 400 for a missing/unrecognized `action` value — a request-body
        problem, not a state problem.

    Not idempotency-guarded beyond what the manager methods themselves
    already do (`confirm_success`/`reject` are idempotent per their own
    docstrings in models.py; `mark_processing` is not, and calling it
    twice on an already-PROCESSING row is intentionally left to raise
    from a plain equality check there would need — out of scope here,
    same as the other manager-level caveats §11 already tracks).
    """

    permission_classes = [IsAdminUser]
    serializer_class = CoinWithdrawalActionSerializer

    # Kept as attributes for anything that imports them; the accepted values
    # themselves are defined once, on CoinWithdrawalActionSerializer.
    ACTION_PROCESSING = CoinWithdrawalActionSerializer.ACTION_PROCESSING
    ACTION_SUCCESS = CoinWithdrawalActionSerializer.ACTION_SUCCESS
    ACTION_REJECT = CoinWithdrawalActionSerializer.ACTION_REJECT
    VALID_ACTIONS = CoinWithdrawalActionSerializer.VALID_ACTIONS

    @extend_schema(
        request=CoinWithdrawalActionSerializer,
        responses={
            200: CoinWithdrawalRequestSerializer,
            400: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
            409: OpenApiTypes.OBJECT,
        },
        description="Staff-only. Advance a withdrawal request's lifecycle: "
        "{'action': 'processing'|'success'|'reject', 'reason': '<reject only, optional>'}.",
    )
    def post(self, request, withdrawal_id):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)
        action = serializer.validated_data["action"]

        # P-G1: while cash-out is OFF staff may not push a payout forward ("processing" / "success"). "reject" stays open:
        # it only refunds the coins, so requests made before the switch went OFF can still be unwound.
        if action != self.ACTION_REJECT and not _coin_cashout_enabled():
            return _cashout_disabled_response()

        try:
            if action == self.ACTION_PROCESSING:
                withdrawal = CoinWithdrawalRequest.objects.mark_processing(
                    withdrawal_id=withdrawal_id, reviewed_by=request.user,
                )
                message = "Withdrawal moved to processing."
            elif action == self.ACTION_SUCCESS:
                withdrawal = CoinWithdrawalRequest.objects.confirm_success(
                    withdrawal_id=withdrawal_id
                )
                message = "Withdrawal marked successful."
            else:
                withdrawal = CoinWithdrawalRequest.objects.reject(
                    withdrawal_id=withdrawal_id,
                    reason=serializer.validated_data.get("reason", ""),
                    reviewed_by=request.user,
                )
                message = "Withdrawal rejected and coins refunded."
        except CoinWithdrawalRequest.DoesNotExist:
            return Response({
                "status": False,
                "message": f"No withdrawal request with id {withdrawal_id}.",
            }, status=status.HTTP_404_NOT_FOUND)
        except CoinLedgerBusy:
            return _wallet_busy_response()
        except ValueError as exc:
            # Invalid state transition (e.g. rejecting an already-
            # SUCCESS request) — the manager methods raise ValueError
            # for exactly this; see models.py for each one's own rules.
            return Response({
                "status": False,
                "message": str(exc),
            }, status=status.HTTP_409_CONFLICT)

        return Response({
            "status": True,
            "message": message,
            "data": CoinWithdrawalRequestSerializer(withdrawal).data,
        }, status=status.HTTP_200_OK)
