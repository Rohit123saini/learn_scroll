"""
X3 - product analytics endpoints (core/product_analytics.py + core/experiments.py).

  GET  core/analytics/config/         JWT    {collect, consent, experiments:{key:{variant,kind}}}  - what the app may do
  POST core/analytics/events/         JWT    {"events":[{"event":"settings_opened","section":"privacy"}, ...]}
  GET  core/analytics/product/        staff JWT or scrape token - JSON summary (?days=7) / Prometheus text (?format=prom)

Privacy: the app sends only a fixed vocabulary (see product_analytics.CLIENT_EVENTS); the server counts it ONLY for
users whose latest `analytics` consent is granted, and never stores an id with it. No consent is not an error: the
answer is 202 with `counted: 0` and `consent: false`, and the app simply stops sending (it re-reads /config/).
"""
import hmac
import logging

from django.conf import settings
from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from login.authentication import SlidingSessionAuthentication

from . import experiments, product_analytics as pa

logger = logging.getLogger(__name__)
MAX_EVENTS_PER_REQUEST = 20


class AnalyticsConfigView(APIView):
    """GET -> {"collect": bool, "can_ask": bool, "consent": "granted|withdrawn|unasked", "experiments": {key: {variant, kind}}}.

    `collect` is the one switch the app obeys: false => do not call /events/ at all. Experiments are returned even
    without consent (a variant is a product decision, not tracking) - only their measurement is consent-gated."""

    authentication_classes = [SlidingSessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "product_analytics_config"

    def get(self, request):
        state = pa.consent_state(request.user)
        resp = Response({
            "collect": pa.allowed(request.user),
            "can_ask": pa.can_ask(request.user),  # X3b: show the first-run consent card (never for minors / already answered)
            "consent": {"g": "granted", "w": "withdrawn", "u": "unasked"}.get(state, "unasked"),
            "experiments": experiments.flags_for(request.user),
        })
        resp["Cache-Control"] = "private, no-store"
        return resp


class ProductEventsView(APIView):
    """POST {"events": [{"event": "settings_opened", "section": "privacy"}, {"event": "profile_edit_opened"},
    {"event": "experiment_exposure", "experiment": "<flag key>", "variant": "b"}]}   (max 20)

    202 {"accepted": n, "counted": n, "consent": bool}. 400 only for a malformed body or an event name a phone may
    not send (anything outside pa.CLIENT_EVENTS - the server writes the rest itself, so they cannot be spoofed)."""

    authentication_classes = [SlidingSessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "product_events"

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        events = data.get("events")
        if not isinstance(events, list) or len(events) > MAX_EVENTS_PER_REQUEST:
            return Response({"detail": f"events must be a list of at most {MAX_EVENTS_PER_REQUEST}."},
                            status=status.HTTP_400_BAD_REQUEST)
        for ev in events:
            name = ev.get("event") if isinstance(ev, dict) else None
            if name not in pa.CLIENT_EVENTS:
                return Response({"detail": f"unsupported event: {str(name)[:40]!r}"}, status=status.HTTP_400_BAD_REQUEST)
        consent = pa.allowed(request.user)
        counted = sum(1 for ev in events if pa.track_client(request.user, ev)) if consent else 0
        if not consent and events:
            # one aggregate tick per request: how much the consent gate leaves out (no id, no event names)
            pa._drop("no_consent")  # noqa: SLF001 - same package, deliberate
        return Response({"accepted": len(events), "counted": counted, "consent": consent},
                        status=status.HTTP_202_ACCEPTED)


class ProductAnalyticsOpsView(APIView):
    """Staff JWT or `Authorization: Bearer <PRODUCT_ANALYTICS_SCRAPE_TOKEN>` (empty => FEED_OPS_SCRAPE_TOKEN).
    `?format=prom` -> Prometheus text for Grafana (deploy/monitoring/grafana_product_dashboard.json);
    default -> JSON `summary(days)` with the funnels and per-variant open rates. Numbers only, no user data."""

    permission_classes = []
    authentication_classes = []  # a scrape token is not a user JWT; staff JWT is handled in _allowed
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "product_analytics_ops"

    @staticmethod
    def _allowed(request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        token = (getattr(settings, "PRODUCT_ANALYTICS_SCRAPE_TOKEN", "")
                 or getattr(settings, "FEED_OPS_SCRAPE_TOKEN", "") or "")
        if token and header.startswith("Bearer ") and hmac.compare_digest(header[7:].strip(), token):
            return True
        if header.startswith("Bearer "):
            try:
                auth = SlidingSessionAuthentication().authenticate(request)
                return bool(auth and (auth[0].is_staff or auth[0].is_superuser))
            except Exception:  # noqa: BLE001
                return False
        return False

    def get(self, request):
        if not self._allowed(request):
            return Response({"success": False, "message": "Staff only."}, status=status.HTTP_403_FORBIDDEN)
        if request.query_params.get("format") == "prom":
            return HttpResponse(pa.prometheus_text(), content_type="text/plain; version=0.0.4; charset=utf-8")
        try:
            days = int(request.query_params.get("days", 7))
        except (TypeError, ValueError):
            days = 7
        return Response({"success": True, "data": pa.summary(days)})
