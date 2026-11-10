"""
post/ai_assist_views.py  (composer AI helpers - the HTTP layer)

  POST /post/ai-assist/<feature>/      feature = caption | tags | summary | alt_text | translate | hint | quality
  GET  /post/ai-assist/config/         what the composer may show (kill switches, per-feature quota left)

Request (JSON, or multipart for the two picture features):
  caption / tags   {title?, content?, subject?, topics?: [..], kind?: post|reel|doubt|poll|resource, language?: auto|en|hi|hinglish}
  summary          {content, title?, language?}
  translate        {text, target?: hi|en|auto}
  hint             {content (the question), subject?, language?}
  alt_text         multipart: image=<file>, caption?, language?      (JPG / PNG / WEBP, <= 5 MB; off for minor accounts)
  quality          multipart or JSON: kind?, title?, images=<file> x up to 5, dims? = JSON [[w,h],..] of the ORIGINAL
                   photo sizes when the app sent shrunk copies   (NO AI: free, never counted)

Success : {success: True, feature, cached, remaining, ...result}
          caption {captions: [..]} - tags {tags: [..]} - summary {lines: [..], text} - hint {hints: [..]}
          alt_text {alt_text} - translate {text, target} - quality {hints: [{code, severity, index, message}]}
Error   : {success: False, code, message, retry_after?}  (+ a Retry-After header when retry_after is set)
          400 not_enough_content | image_required | image_too_large | image_type
          403 ai_not_for_minors            404 unknown_feature
          429 quota_exceeded               503 ai_disabled | feature_disabled | ai_busy | budget_exhausted | ai_unavailable
          502 ai_failed (the model failed or gave nothing usable; the user's quota was given back)

The cost guard (post/ai_assist_guard.py) sits in ai_assist.run(); this file only maps exceptions to HTTP.
"""
import logging

from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from aiquality.telemetry import ai_context  # P-F6
from . import ai_assist
from . import ai_assist_guard as guard

logger = logging.getLogger(__name__)


# Rates live in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] (post_ai_assist 20/min, _image 8/min, _quality 30/min).
class PostAiAssistThrottle(UserRateThrottle):
    scope = "post_ai_assist"


class PostAiAssistImageThrottle(UserRateThrottle):
    scope = "post_ai_assist_image"


class PostAiAssistQualityThrottle(UserRateThrottle):
    scope = "post_ai_assist_quality"


def _err(code, message, http=status.HTTP_400_BAD_REQUEST, retry_after=None):
    body = {"success": False, "code": code, "message": message}
    headers = {}
    if retry_after:
        body["retry_after"] = int(retry_after)
        headers["Retry-After"] = str(int(retry_after))
    return Response(body, status=http, headers=headers)


def _feature(raw):
    return str(raw or "").strip().lower().replace("-", "_")


def _read_upload(f, limit):
    """Bytes of one uploaded file, or None when it is larger than `limit` (never reads more than limit + 1)."""
    if f is None:
        return None
    if getattr(f, "size", 0) and f.size > limit:
        return None
    data = f.read(limit + 1)
    return data if len(data) <= limit else None


class PostAiAssistAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_throttles(self):
        feature = _feature(self.kwargs.get("feature"))
        if feature == "quality":
            return [PostAiAssistQualityThrottle()]
        if feature in ai_assist.IMAGE_FEATURES:
            return [PostAiAssistThrottle(), PostAiAssistImageThrottle()]
        return [PostAiAssistThrottle()]

    # ------------------------------------------------------------------ POST
    def post(self, request, feature):
        feature = _feature(feature)
        if feature == "quality":
            return self._quality(request)
        if feature not in guard.AI_FEATURES:
            return _err("unknown_feature", "Unknown AI feature.", status.HTTP_404_NOT_FOUND)
        if not ai_assist.available():
            return _err("ai_unavailable", "AI is not available right now.", status.HTTP_503_SERVICE_UNAVAILABLE)

        image = None
        if feature in ai_assist.IMAGE_FEATURES:
            upload = request.FILES.get("image")
            if upload is None:
                return _err("image_required", "Choose a photo first.")
            image = _read_upload(upload, ai_assist.MAX_IMAGE_BYTES)
            if image is None:
                return _err("image_too_large", "The photo is too large for AI help (max 5 MB).")

        try:
            with ai_context("post_assist", feature, request.user) as ctx:
                result, cached = ai_assist.run(request.user, feature, request.data, image)
        except ai_assist.BadInput as e:
            return _err(e.code, e.message)
        except ai_assist.NotAllowed as e:
            return _err(e.code, e.message, status.HTTP_403_FORBIDDEN)
        except guard.GuardRefused as e:
            return _err(e.code, e.message, e.http, e.retry_after)
        except ai_assist.ModelFailure:
            logger.warning("ai_assist %s: model failed user=%s", feature, request.user.id, exc_info=True)
            return _err("ai_failed", "AI is not answering right now. Please try again in a moment.",
                        status.HTTP_502_BAD_GATEWAY)
        except ai_assist.UnusableResult:
            return _err("ai_failed", "The AI could not help with this one. Try changing the text a little.",
                        status.HTTP_502_BAD_GATEWAY)
        return Response({"success": True, "feature": feature, "cached": cached,
                         "remaining": guard.remaining(request.user, feature),
                         "ai_meta": ctx.meta(),  # P-F6: the app echoes this back with a 👍 / 👎
                         **result})

    # ------------------------------------------------------------------ quality (no AI)
    def _quality(self, request):
        images = []
        for f in request.FILES.getlist("images")[:ai_assist.MAX_QUALITY_IMAGES]:
            data = _read_upload(f, ai_assist.MAX_QUALITY_IMAGE_BYTES)
            images.append(data or b"")  # keep the position: a too-large file just gets no hint
        try:
            hints = ai_assist.quality_hints(
                ai_assist.clean_text(request.data.get("kind", "post"), 12, multiline=False).lower(),
                request.data.get("title", ""),
                images,
                ai_assist.clean_sizes(request.data.get("dims"), len(images)),
            )
        except Exception:  # a tip is a nicety: never an error for the composer
            logger.warning("ai_assist quality failed", exc_info=True)
            hints = []
        return Response({"success": True, "feature": "quality", "cached": False, "remaining": None, "hints": hints})


class PostAiAssistConfigAPIView(APIView):
    """What the composer may show for THIS user. Cheap (cache reads only) and not throttled."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        features = guard.snapshot(request.user)
        if not ai_assist.image_ai_allowed(request.user):
            features["alt_text"]["enabled"] = False
        # P-LEFT (P-J2): the admin / env kill switch `ai_features` must show up HERE too, so the composer hides the AI strip
        # from the same answer instead of relying on the app-config flag alone. (The POST views are already refused by the
        # feature gate middleware; this only keeps the two answers consistent.)
        from common import feature_flags as ff

        switched_off = ff.server_blocked(ff.AI_FEATURES) is not None
        if switched_off:
            for f in features.values():
                if isinstance(f, dict):
                    f["enabled"] = False
        return Response({
            "success": True,
            "enabled": guard.enabled() and not switched_off,
            "feature_flag_off": switched_off,
            "available": ai_assist.available(),  # false = the Gemini key / client is not set up on this server
            "features": features,
            "limits": {
                "caption_max_chars": ai_assist.CAPTION_MAX,
                "summary_min_chars": ai_assist.MIN_SUMMARY_CHARS,
                "hint_min_chars": ai_assist.MIN_HINT_CHARS,
                "questions_max": ai_assist.QUESTIONS_MAX,
                "image_max_bytes": ai_assist.MAX_IMAGE_BYTES,
                "quality_max_images": ai_assist.MAX_QUALITY_IMAGES,
            },
        })
