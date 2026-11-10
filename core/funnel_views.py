"""
T3.8 -- funnel endpoints.

  POST core/funnel/events/         public (JWT optional) -- the app reports install / app_open
  POST core/funnel/first-feed-latency/   JWT -- the app reports how long the user waited for the first feed (ms), once
  POST core/onboarding/step/       JWT -- P-F1: the app reports an onboarding step viewed / completed / skipped
  GET  core/analytics/funnel/      staff only, JSON  (?days=30&platform=android)
  GET  core/analytics/funnel/dashboard/   staff only, HTML dashboard (login via the hardened /admin/ login)
"""
import html
import logging

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from login.authentication import SlidingSessionAuthentication

from . import funnel, onboarding_steps
from .models_funnel import FunnelEvent

logger = logging.getLogger(__name__)
MAX_EVENTS_PER_REQUEST = 20


class OptionalSlidingSessionAuthentication(SlidingSessionAuthentication):
    """Same JWT validation as the rest of the API, but an expired/invalid token must NOT turn an
    install/app_open ping into a 401 -- the app launches (and must still be counted) before login."""

    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except Exception:  # noqa: BLE001 - any auth failure => treat as anonymous
            return None


class FunnelEventsView(APIView):
    """POST {"device_id": "<16-64 chars [A-Za-z0-9_-]>", "platform": "android|ios|web|other",
    "app_version": "1.4.0", "events": [{"event": "install"}, {"event": "app_open"}]}

    * `events` may be empty -- an authenticated call with no events just links this device to the user
      (the app does that right after login / signup).
    * only `install` and `app_open` are accepted from clients; signup / onboarding / first doubt / first test
      are recorded by the server itself, so they cannot be spoofed or missed.
    """

    authentication_classes = [OptionalSlidingSessionAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "funnel_events"

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        device_id = str(data.get("device_id") or "")
        if not funnel.DEVICE_ID_RE.match(device_id):
            return Response({"detail": "device_id must be 16-64 chars of [A-Za-z0-9_-]."}, status=status.HTTP_400_BAD_REQUEST)
        platform = str(data.get("platform") or "other").lower()
        platform = platform if platform in funnel.PLATFORMS else "other"
        app_version = str(data.get("app_version") or "")[:32]
        events = data.get("events") or []
        if not isinstance(events, list) or len(events) > MAX_EVENTS_PER_REQUEST:
            return Response({"detail": f"events must be a list of at most {MAX_EVENTS_PER_REQUEST}."},
                            status=status.HTTP_400_BAD_REQUEST)
        names = []
        for ev in events:
            name = ev.get("event") if isinstance(ev, dict) else None
            if name not in {e.value for e in FunnelEvent.CLIENT_EVENTS}:
                return Response({"detail": f"unsupported event: {name!r}"}, status=status.HTTP_400_BAD_REQUEST)
            names.append(name)

        user = request.user if getattr(request.user, "is_authenticated", False) else None
        stored = 0
        for name in dict.fromkeys(names):  # de-dupe within the request
            if name == FunnelEvent.Event.INSTALL:
                stored += funnel.record_install(device_id, platform, app_version, user=user)
            else:
                stored += funnel.record_app_open(device_id, user=user, platform=platform, app_version=app_version)
        if user:
            funnel.link_device(user, device_id)
        return Response({"accepted": len(names), "stored": stored}, status=status.HTTP_202_ACCEPTED)


class FirstFeedLatencyView(APIView):
    """POST {"ms": 1840, "device_class": "standard|low_end"}  -> 202 {"status": "stored|already|..."}

    P-A. The phone measures "feed request started -> first card >= 50 % visible" (PerfMonitor) for the user's FIRST
    feed and reports it once. The server does not trust it for anything but a metric: it only lands on the user's own
    server-written `first_feed` row (see funnel.record_client_first_feed), within 30 minutes of that row, once, and
    0 < ms <= 120 000. Always 202 for a well-formed body (a refused value is a status, not an error: the app must not
    retry), 400 only when `ms` is missing / not a number.
    """

    authentication_classes = [SlidingSessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "funnel_events"

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        ms = data.get("ms")
        if isinstance(ms, bool) or not isinstance(ms, (int, float, str)):
            return Response({"detail": "ms must be a number."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ms = int(float(ms))
        except (TypeError, ValueError, OverflowError):
            return Response({"detail": "ms must be a number."}, status=status.HTTP_400_BAD_REQUEST)
        result = funnel.record_client_first_feed(request.user, ms=ms, device_class=str(data.get("device_class") or ""))
        return Response({"status": result}, status=status.HTTP_202_ACCEPTED)


class OnboardingStepView(APIView):
    """POST {"step": "quick_win", "action": "viewed|completed|skipped", "role": "student", "goal_minutes": 10,
    "ai_doubt": true} -> 202 {"stored": true|false}

    P-F1. One row per (user, step, action), first time only. Only the allow-listed step / action / role names are
    accepted (400 otherwise); the only extras the app may send are `goal_minutes` (a number, goal step) and `ai_doubt`
    (a bool, quick-win step: the user opened "Ask AI a doubt" - a doubt asked to the AI creates no DoubtQuestion row, so
    this is the only trace of it).
    `stored: false` just means "already counted" - the app must never retry or show anything for it.
    """

    authentication_classes = [SlidingSessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "funnel_events"

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        step, action = data.get("step"), data.get("action")
        if not onboarding_steps.valid_step(step):
            return Response({"detail": f"step must be one of {list(onboarding_steps.STEPS)}."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not onboarding_steps.valid_action(action):
            return Response({"detail": f"action must be one of {list(onboarding_steps.ACTIONS)}."},
                            status=status.HTTP_400_BAD_REQUEST)
        extra = {}
        minutes = data.get("goal_minutes")
        if isinstance(minutes, int) and not isinstance(minutes, bool) and 0 < minutes <= 600:
            extra["goal_minutes"] = minutes
        if data.get("ai_doubt") is True:
            extra["ai_doubt"] = True
        stored = onboarding_steps.record_step(request.user, step, action, role=str(data.get("role") or ""), props=extra)
        return Response({"stored": bool(stored)}, status=status.HTTP_202_ACCEPTED)


def _parse_params(request):
    try:
        days = int(request.GET.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    platform = request.GET.get("platform") or None
    if platform not in funnel.PLATFORMS:
        platform = None
    return days, platform


class FunnelReportView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        days, platform = _parse_params(request)
        report = funnel.funnel_report(days=days, platform=platform)
        report["onboarding_steps"] = onboarding_steps.steps_report(days=days)  # P-F1
        return Response(report)


@never_cache
@staff_member_required
def funnel_dashboard(request):
    days, platform = _parse_params(request)
    r = funnel.funnel_report(days=days, platform=platform)
    ob = onboarding_steps.steps_report(days=days)  # P-F1
    esc = html.escape

    def fmt(v):
        return "–" if v is None else f"{v}%"

    def dash(v):
        return "–" if v is None else v

    max_count = max([s["count"] for s in r["steps"]] + [1])
    step_rows = "".join(
        f"<tr><td>{esc(s['step'])}</td><td class=n>{s['count']}</td><td class=n>{fmt(s['pct_of_previous'])}</td>"
        f"<td class=n>{fmt(s['pct_of_installs'])}</td>"
        f"<td><div class=bar style='width:{max(2, int(100 * s['count'] / max_count))}%'></div></td></tr>"
        for s in r["steps"]
    )
    ret = r["retention"]
    ret_rows = "".join(
        f"<tr><td>{k.upper()}</td><td class=n>{v['retained']} / {v['eligible']}</td><td class=n>{fmt(v['pct'])}</td></tr>"
        for k, v in ret.items()
    )
    daily_rows = "".join(
        f"<tr><td>{esc(d['date'])}</td><td class=n>{d['installs']}</td><td class=n>{d['signups']}</td></tr>"
        for d in reversed(r["daily"])
    )
    ob_rows = "".join(
        f"<tr><td>{esc(x['step'])}</td><td class=n>{x['viewed']}</td><td class=n>{x['completed']} ({fmt(x['completed_pct_of_viewed'])})</td>"
        f"<td class=n>{x['skipped']} ({fmt(x['skipped_pct_of_viewed'])})</td><td class=n>{x['left']}</td></tr>"
        for x in ob["steps"]
    )
    qw = ob["quick_win"]
    det = r["details"]
    cs = r["cold_start"]
    w = r["window"]
    opts = "".join(
        f"<option value='{p}'{' selected' if (p or 'all') == w['platform'] else ''}>{p or 'all'}</option>"
        for p in (None, "android", "ios", "web")
    )
    day_opts = "".join(f"<option{' selected' if d == w['days'] else ''}>{d}</option>" for d in (7, 14, 30, 60, 90, 180))
    body = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Funnel dashboard</title><style>
body{{font-family:system-ui,sans-serif;margin:24px;max-width:960px;color:#1b1b1f;background:#fafafa}}
h1{{font-size:20px}} h2{{font-size:15px;margin-top:28px}} table{{border-collapse:collapse;width:100%;background:#fff}}
th,td{{border:1px solid #ddd;padding:6px 10px;text-align:left;font-size:14px}} th{{background:#f0f0f4}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}} .bar{{height:14px;background:#4f6bed;border-radius:3px}}
form{{display:flex;gap:12px;align-items:center;margin-bottom:12px}} .muted{{color:#666;font-size:13px}}
</style></head><body>
<h1>Growth funnel &amp; retention</h1>
<form method=get><label>Window <select name=days>{day_opts}</select> days</label>
<label>Platform <select name=platform>{opts}</select></label><button>Apply</button></form>
<p class=muted>{esc(w['start'])} → {esc(w['end'])} (installs in this window, followed through to today). Each step is a subset of the previous one.</p>
<table><tr><th>Step</th><th>Users</th><th>% of previous</th><th>% of installs</th><th style="width:30%"></th></tr>{step_rows}</table>
<p class=muted>Onboarding: {det['onboarding_finished']} finished, {det['onboarding_skipped']} skipped &middot;
first doubt: {det['first_doubt']} &middot; first test: {det['first_test']} &middot;
installs that never logged in: {det['installs_not_logged_in']}</p>
<h2>Retention D1 / D3 / D7 (of signed-up users, exact day after signup)</h2>
<table><tr><th>Day</th><th>Retained / eligible</th><th>Rate</th></tr>{ret_rows}</table>
<h2>Cold start (signup &rarr; interests &rarr; first personalised feed, target {cs['target_seconds']} s)</h2>
<table><tr><th>Signed up</th><th>Interests saved</th><th>First feed served</th><th>&le; {cs['target_seconds']} s</th><th>Personalised</th><th>Backfilled</th><th>Empty (must be 0)</th><th>Median s</th></tr>
<tr><td class=n>{cs['signed_up']}</td><td class=n>{cs['interests_saved']} ({fmt(cs['interests_saved_pct'])})</td><td class=n>{cs['first_feed']}</td>
<td class=n>{cs['first_feed_within_target']} ({fmt(cs['first_feed_within_target_pct'])})</td><td class=n>{cs['first_feed_personalised']}</td>
<td class=n>{cs['first_feed_backfilled']}</td><td class=n>{cs['first_feed_empty']}</td><td class=n>{'–' if cs['median_seconds_to_first_feed'] is None else cs['median_seconds_to_first_feed']}</td></tr></table>
<h3>First feed as the phone measured it (ms the user waited at Home, reported once by the app)</h3>
<table><tr><th>Reports</th><th>Median ms</th><th>p75 ms</th><th>p95 ms</th><th>Median ms by device</th></tr>
<tr><td class=n>{cs['client_first_feed']['count']}</td><td class=n>{dash(cs['client_first_feed']['median_ms'])}</td><td class=n>{dash(cs['client_first_feed']['p75_ms'])}</td><td class=n>{dash(cs['client_first_feed']['p95_ms'])}</td>
<td>{esc(', '.join(f'{k}: {v}' for k, v in cs['client_first_feed']['median_ms_by_device_class'].items()) or '–')}</td></tr></table>
<h2>First-session onboarding, step by step (P-F1)</h2>
<table><tr><th>Step</th><th>Viewed</th><th>Completed</th><th>Skipped (left the flow here)</th><th>Left without action</th></tr>{ob_rows}</table>
<p class=muted>Quick win: {qw['viewed']} users saw it, {qw['converted']} ({fmt(qw['converted_pct'])}) started a test or asked a doubt within 24 h; {qw['ai_doubt_opened']} opened "Ask AI a doubt" (app-reported).
Steps are counted once per user; the window is by when the step happened, not by install date.</p>
<h2>Daily</h2><table><tr><th>Date</th><th>Installs</th><th>Signups</th></tr>{daily_rows}</table>
</body></html>"""
    return HttpResponse(body)
