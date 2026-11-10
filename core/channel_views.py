"""
core/channel_views.py - N8 endpoints (urls in core/urls.py).

    GET  core/notification-preferences/channels/                 status of e-mail / sms / whatsapp  (own data)
    POST core/notification-preferences/channels/email/send-code/ mail a 6-digit code to the account address
    POST core/notification-preferences/channels/email/verify/    {"code": "123456"}
    POST core/notification-preferences/channels/consent/         {"channel": "sms", "granted": true, "text_version"?}
    GET  core/notifications/unsubscribe/<token>/                 public confirm page (a mail scanner opening it changes nothing)
    POST core/notifications/unsubscribe/<token>/                 public, RFC 8058 one-click AND the page's button
    POST core/channel-webhooks/<email|sms>/                      bounce / complaint / STOP from the provider (shared secret)
"""
from __future__ import annotations

import hmac
import logging
from html import escape

from django.conf import settings
from django.http import HttpResponse
from rest_framework import status
from rest_framework.exceptions import ParseError
from rest_framework.parsers import BaseParser, JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from common.open_throttles import ChannelWebhookThrottle, UnsubscribeThrottle  # P-B3

from . import channel_policy as policy
from . import channel_provider_adapters as adapters  # N8 - SES / SendGrid / MSG91 / WhatsApp payload -> normalised event

logger = logging.getLogger(__name__)


def _client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")) or None


class ChannelStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(policy.channel_status(request.user))


class EmailSendCodeView(APIView):
    """Rate limited three ways: 60 s between codes, 5 codes / day (policy) and the DRF scope below."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "channel_verify"

    def post(self, request):
        result = policy.request_email_code(request.user)
        if result.get("ok"):
            return Response(result)
        code = {"too_soon": status.HTTP_429_TOO_MANY_REQUESTS, "daily_limit": status.HTTP_429_TOO_MANY_REQUESTS,
                "send_failed": status.HTTP_503_SERVICE_UNAVAILABLE}.get(result["reason"], status.HTTP_400_BAD_REQUEST)
        return Response(result, status=code)


class EmailVerifyView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "channel_verify"

    def post(self, request):
        result = policy.confirm_email_code(request.user, str(request.data.get("code", "")))
        if result.get("ok"):
            return Response({**result, "channels": policy.channel_status(request.user)})
        return Response(result, status=status.HTTP_400_BAD_REQUEST)


class ChannelConsentView(APIView):
    """The ONLY way to switch sms / whatsapp on: records the consent (text version, number, IP) and flips the switch in
    one step. {"granted": false} withdraws it (and the switch goes off). Works for e-mail too (grant = switch on)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        channel = request.data.get("channel")
        if channel not in policy.CHANNELS:
            return Response({"channel": ["Use one of: email, sms, whatsapp."]}, status=status.HTTP_400_BAD_REQUEST)
        granted = request.data.get("granted")
        if not isinstance(granted, bool):
            return Response({"granted": ["Send true or false."]}, status=status.HTTP_400_BAD_REQUEST)
        blockers = policy.set_channel_enabled(
            request.user, channel, granted, source="app",
            text_version=str(request.data.get("text_version") or "")[:20], ip=_client_ip(request),
        )
        if blockers:
            return Response({"code": blockers[0], "blockers": blockers, "channels": policy.channel_status(request.user)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(policy.channel_status(request.user))


# ------------------------------------------------------------------------------------------------ public unsubscribe
_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LearnScroll</title></head><body style="font-family:Arial,sans-serif;max-width:480px;margin:40px auto;padding:0 16px;color:#1c1b1f">
<h2 style="color:#4b3fd1">LearnScroll</h2>{body}</body></html>"""


def _page(body, code=200):
    return HttpResponse(_PAGE.replace("{body}", body), status=code, content_type="text/html; charset=utf-8")


class UnsubscribeView(APIView):
    """Public (the link lives in a mail) - the signed token IS the credential, so no login and no CSRF cookie."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [UnsubscribeThrottle]  # P-B3

    def get(self, request, token):
        data = policy.read_unsubscribe_token(token)
        if data is None:
            return _page("<p>This link is not valid any more. / यह लिंक अब मान्य नहीं है।</p>", 400)
        what = "summary e-mails" if data["s"] == "digest" else "e-mail notifications"
        what_hi = "सारांश ईमेल" if data["s"] == "digest" else "ईमेल नोटिफ़िकेशन"
        return _page(
            f"<p>Stop {escape(what)} from LearnScroll?<br><span style='color:#6b6975'>LearnScroll के {escape(what_hi)} बंद करें?</span></p>"
            f"<form method='post'><button type='submit' style='padding:10px 18px;font-size:16px;background:#4b3fd1;color:#fff;"
            f"border:0;border-radius:8px'>Unsubscribe / अनसब्सक्राइब</button></form>"
            f"<p style='font-size:12px;color:#6b6975'>You can turn it on again in the app: Settings &gt; Notifications.</p>"
        )

    def post(self, request, token):
        result = policy.apply_unsubscribe(token, ip=_client_ip(request))
        wants_html = "text/html" in request.META.get("HTTP_ACCEPT", "") and "application/json" not in request.META.get("HTTP_ACCEPT", "")
        if not result["ok"]:
            if wants_html:
                return _page("<p>This link is not valid any more. / यह लिंक अब मान्य नहीं है।</p>", 400)
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        if wants_html:
            return _page("<p><b>Done.</b> You will not get these e-mails any more.<br>"
                         "<span style='color:#6b6975'>हो गया। अब आपको ये ईमेल नहीं मिलेंगे।</span></p>")
        return Response(result)


# ------------------------------------------------------------------------------------------------ provider webhooks
class PlainTextJSONParser(BaseParser):
    """Amazon SNS posts its JSON with `Content-Type: text/plain`, which DRF's JSONParser refuses (415). Parse it as JSON."""

    media_type = "text/plain"

    def parse(self, stream, media_type=None, parser_context=None):
        import json

        try:
            return json.loads(stream.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ParseError("body is not valid JSON") from exc


class ChannelWebhookView(APIView):
    """Bounce / complaint (e-mail) and STOP / invalid number (sms, whatsapp) from the provider.

    Auth: header `X-Webhook-Secret` == settings.NOTIF_WEBHOOK_SECRET (constant-time compare). No secret configured = the
    endpoint is OFF (503) - never an open door that anyone could use to silence other people's mail.

    Body (one event or {"events": [..]}):
        {"event": "bounce", "bounce_type": "hard"|"soft", "address": "a@b.com"}      provider "email"
        {"event": "complaint", "address": "a@b.com"}                                   provider "email"
        {"event": "stop" | "invalid", "address": "+919876543210"}                      provider "sms"
    Provider payloads: add `?format=ses|sendgrid|msg91|meta` (core/channel_provider_adapters.py maps them to the shape above)."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ChannelWebhookThrottle]  # P-B3: was the 20/min anon default, which could 429 an SES bounce burst
    parser_classes = [JSONParser, PlainTextJSONParser]

    def post(self, request, provider):
        secret = getattr(settings, "NOTIF_WEBHOOK_SECRET", "") or ""
        if not secret:
            return Response({"detail": "webhooks are disabled"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        # Header is preferred. Amazon SNS cannot add headers to its HTTP call, so `?key=<secret>` is accepted as well
        # (use HTTPS only; rotate the secret if a proxy logs query strings).
        sent = request.META.get("HTTP_X_WEBHOOK_SECRET", "") or request.query_params.get("key", "")
        if not hmac.compare_digest(sent.encode(), secret.encode()):
            return Response({"detail": "forbidden"}, status=status.HTTP_403_FORBIDDEN)
        if provider not in ("email", "sms", "whatsapp"):
            return Response({"detail": "unknown provider"}, status=status.HTTP_404_NOT_FOUND)
        # N8 - `?format=` picks the provider adapter (generic | ses | sendgrid | msg91 | meta); default = our own shape.
        fmt = (request.query_params.get("format") or "generic").lower()
        if fmt not in adapters.ADAPTERS:
            return Response({"detail": "unknown format", "formats": sorted(adapters.ADAPTERS)}, status=status.HTTP_400_BAD_REQUEST)
        if provider not in adapters.FORMAT_CHANNELS[fmt]:
            return Response({"detail": f"format '{fmt}' cannot feed channel '{provider}'"}, status=status.HTTP_400_BAD_REQUEST)
        payload = request.data
        if fmt == "ses":
            confirm = adapters.sns_subscribe_url(payload)
            if confirm:  # first message of an SNS subscription: shown to the operator, never fetched by us (SSRF)
                logger.warning("SNS subscription confirmation pending - open this URL once: %s", confirm)
                return Response({"processed": 0, "subscribe_url": confirm})
        events = adapters.normalise(fmt, payload)
        results = []
        for ev in events[:200]:
            try:
                results.append(policy.handle_provider_event(
                    provider, ev["event"], ev["address"],
                    bounce_type=ev.get("bounce_type", ""), detail=ev.get("detail", ""),
                ))
            except Exception:  # noqa: BLE001 - one bad event must not drop the rest of the batch
                logger.exception("channel webhook event failed: %r", ev)
                results.append({"ok": False, "reason": "error"})
        return Response({"processed": len(results), "results": results})
