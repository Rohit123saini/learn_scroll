"""
common/moderation_ai.py

OPTIONAL AI second pass for auto-moderation. OFF unless
settings.AUTOMOD_AI_ENABLED is true AND settings.ANTHROPIC_API_KEY is set.

It is only ever called from the Celery task `user_profile.tasks.automod_ai_screen`
(never inside a request), after the cheap word-list pass found nothing. It
uses the Anthropic Messages API over plain urllib (no new dependency) with a
short timeout, and fails SAFE: any network/parse problem -> "not flagged".

classify(text) -> (is_flagged, reason_slug)   reason_slug in AI_REASONS
"""

import json
import logging
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

AI_REASONS = ("hate", "harassment", "sexual", "violence", "self_harm", "abuse")

_SYSTEM = (
    "You are a content-moderation classifier for a student learning app that "
    "has many minors. Decide whether the user text is abusive. Reply with ONLY "
    'a JSON object: {"flag": true|false, "category": "hate|harassment|sexual|'
    'violence|self_harm|abuse|none"}. Flag hate speech, harassment/bullying, '
    "sexual content, threats/violence, self-harm encouragement, or other "
    "abusive language, in ANY language or script (including Hinglish). Do NOT "
    "flag normal studying talk, mild frustration, or exam stress."
)


def ai_enabled() -> bool:
    return bool(getattr(settings, "AUTOMOD_AI_ENABLED", False) and getattr(settings, "ANTHROPIC_API_KEY", ""))


def classify(text: str):
    if not ai_enabled() or not text:
        return False, ""
    try:
        body = json.dumps({
            "model": getattr(settings, "AUTOMOD_AI_MODEL", "claude-haiku-5-5"),
            "max_tokens": 60,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": text[:2000]}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=getattr(settings, "AUTOMOD_AI_TIMEOUT", 5)) as resp:
            data = json.loads(resp.read())
        raw = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        verdict = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        if verdict.get("flag") is True:
            cat = verdict.get("category")
            return True, f"ai_{cat if cat in AI_REASONS else 'abuse'}"
        return False, ""
    except Exception:
        logger.warning("automod AI classify failed — leaving content unflagged", exc_info=True)
        return False, ""
