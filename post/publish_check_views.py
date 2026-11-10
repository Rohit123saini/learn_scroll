"""
post/publish_check_views.py - the two small READ-ish endpoints of the H23 publish safety work.

    GET  /post/minor-privacy/    what the composer / story / live screens must grey out for THIS account
    POST /post/publish-check/    preflight: "would this be accepted?" BEFORE the user uploads a big file

minor-privacy -> `post.minor_privacy.privacy_defaults_for` (the same rules the server enforces: a minor's
location is dropped, public live is class-only, the story question sticker is blocked) plus a short human
`messages` text for each switched-off control. The app only greys things out; the server stays the judge.

publish-check -> the SAME gate the create views use (`publish_gate.evaluate(..., check_only=True)`: spam rate
limit + abusive / NSFW text, no file hashing) and a cheap text-duplicate lookup. It NEVER records anything (no
rate-limit tick, no fingerprint, no flag, no notice) - a preflight must not use up the user's posting budget.
Files are not sent here, so file duplicates, the audio-copyright check and the real hold still happen at publish;
the answer is therefore a promise about the text and the rate limit only.

    request  {"surface": "post" | "story" | "resource" (default post),
              ...the same text fields the create endpoint takes (title, content, media_captions, poll_options,
              caption, stickers, description, subject ...),  "texts": ["extra text", ...] (optional)}
    response 200 always (it is a check, not an action):
             {"allowed": bool,            # False -> publishing now would be refused
              "will_hold": bool,          # True  -> it would be created but held for a moderator
              "status": 200|409|422|429,  # what the real publish would answer
              "code", "message", "retry_after", "reasons": [...], "warnings": [...],
              "location_allowed": bool}   # False -> a minor: the location will be dropped

Throttled (30/min per user) because a free "is this text OK?" endpoint is also a word-list probe.
FAILS OPEN like the gate: if the check itself breaks, `allowed` is True (the real publish still runs the gate).
"""
import logging

from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from . import minor_privacy

logger = logging.getLogger(__name__)

TEXT_MAX = 5000      # chars kept per text
TEXTS_MAX = 50       # texts kept in total

_MINOR_MESSAGES = {
    "public_live": "Live classes from a minor's account are only visible to the people of that class.",
    "location": "Location is switched off on accounts under 18.",
    "dm_sticker": "The question sticker is switched off on accounts under 18.",
}


class PublishCheckThrottle(UserRateThrottle):
    scope = "publish_check"
    rate = "30/min"


class MinorPrivacyAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Privacy defaults of this account (minor accounts get stricter ones)",
                   responses={200: OpenApiTypes.OBJECT}, tags=["Publish safety"])
    def get(self, request):
        data = minor_privacy.privacy_defaults_for(request.user)
        messages = {}
        if not data["public_live_allowed"]:
            messages["public_live"] = _MINOR_MESSAGES["public_live"]
        if not data["location_allowed"]:
            messages["location"] = _MINOR_MESSAGES["location"]
        if not data["dm_sticker_allowed"]:
            messages["dm_sticker"] = _MINOR_MESSAGES["dm_sticker"]
        data["messages"] = messages
        return Response(data)


def _texts_from(surface, data):
    from . import publish_gate

    texts = publish_gate.texts_from_request(surface, data)
    extra = data.getlist("texts") if hasattr(data, "getlist") else data.get("texts")
    if isinstance(extra, str):
        extra = [extra]
    if isinstance(extra, (list, tuple)):
        texts.extend(str(t).strip() for t in extra if isinstance(t, str) and t.strip())
    return [t[:TEXT_MAX] for t in texts][:TEXTS_MAX]


class PublishCheckAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [PublishCheckThrottle]

    @extend_schema(summary="Preflight: would this publish be accepted? (text + rate limit, records nothing)",
                   request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT}, tags=["Publish safety"])
    def post(self, request):
        from . import publish_gate

        surface = str(request.data.get("surface") or "post").strip().lower()
        if surface not in publish_gate.SURFACES:
            return Response({"detail": "surface must be one of: " + ", ".join(publish_gate.SURFACES) + "."},
                            status=status.HTTP_400_BAD_REQUEST)
        texts = _texts_from(surface, request.data)

        body = {
            "allowed": True, "will_hold": False, "status": 200, "code": "", "message": "",
            "retry_after": None, "reasons": [], "warnings": [],
            "location_allowed": not minor_privacy.location_blocked(request.user),
        }
        try:
            verdict = publish_gate.evaluate(request.user, surface, texts=texts, check_only=True)
        except Exception:
            logger.exception("publish-check: gate failed - answering 'allowed'")
            return Response(body)

        body["location_allowed"] = verdict.location_allowed
        if not verdict.ok:
            body.update(allowed=False, status=verdict.status, code=verdict.code, message=verdict.message,
                        retry_after=verdict.retry_after, reasons=list(verdict.extra.get("reasons") or []))
            return Response(body)

        # same text already published by this user inside the duplicate window (files cannot be checked here)
        try:
            digest = publish_gate.text_digest(texts)
            own = publish_gate.check_duplicates(request.user, surface, digest, [])[0] if digest else None
        except Exception:
            logger.exception("publish-check: duplicate lookup failed - skipping it")
            own = None
        if own is not None:
            if str(getattr(settings, "PUBLISH_DUP_ACTION", "block")).lower() == "block":
                body.update(allowed=False, status=409, code=publish_gate.CODE_DUPLICATE,
                            message="You already posted this. Please post something new.",
                            reasons=[publish_gate.REASON_DUPLICATE_TEXT])
                return Response(body)
            body["warnings"].append(publish_gate.REASON_DUPLICATE_TEXT)

        if verdict.hold:
            body.update(will_hold=True, reasons=verdict.hold_reasons,
                        message=verdict.held_payload().get("hold_message", ""))
        body["warnings"].extend(f.reason for f in verdict.flags if not f.hold)
        return Response(body)
