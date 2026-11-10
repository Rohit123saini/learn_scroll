"""aiquality/prompts.py - P-F6: prompt VERSION registry.

Every AI feature has a version string. Bump it (here, or without a deploy via env / settings) whenever you edit that
feature's prompt text, so the staff summary can compare 👎 rates of v1 against v2.

    key            = "<surface>.<feature>"           e.g. "post_study.quiz", "doubt.answer"
    override       = settings.AI_PROMPT_VERSIONS  (dict)   or env AI_PROMPT_VERSIONS_JSON='{"doubt.answer": "v2"}'

Safety net: telemetry also stores a short HASH of the real prompt text of every call (`prompt_hash`), so an edit that
nobody remembered to bump is still visible in the dashboard as a new hash under the same version.
"""
import json
import os
import re

from django.conf import settings

DEFAULT_VERSION = "v1"

# surface -> features that may carry feedback (also what POST /ai-quality/feedback/ accepts)
SURFACES = {
    "post_study": ("summary", "quiz", "flashcards"),                                   # feed post "AI study" (cached per post)
    "post_assist": ("caption", "tags", "summary", "alt_text", "translate", "hint", "questions"),  # composer helpers
    "doubt": ("answer",),                                                              # "Ask AI" doubt solver
    "study_buddy": ("explain", "quiz", "flashcards"),                                  # one-tap study buddy
    "copilot": ("answer",),                                                            # classroom copilot
    "revision_deck": ("deck",),                                                        # study-room revision deck
    "smart_reply": ("suggestions",),                                                   # cost log only (no feedback bar)
    "transcribe": ("audio",),                                                          # cost log only
    "study_room": ("summary", "quiz"),                                                 # whiteboard summary / quiz (cost log only)
    "other": ("other",),
}

# surfaces whose output a student can rate in the app
FEEDBACK_SURFACES = tuple(s for s in SURFACES if s not in ("smart_reply", "transcribe", "study_room", "other"))

_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_VERSION = re.compile(r"^[A-Za-z0-9._-]{1,24}$")


def key(surface, feature):
    return f"{surface}.{feature}"


def _overrides():
    data = getattr(settings, "AI_PROMPT_VERSIONS", None)
    if not isinstance(data, dict):
        try:
            data = json.loads(os.getenv("AI_PROMPT_VERSIONS_JSON", "") or "{}")
        except ValueError:
            data = {}
    return data if isinstance(data, dict) else {}


def version_for(surface, feature):
    v = _overrides().get(key(surface, feature)) or _overrides().get(surface)
    v = str(v or "").strip()
    return v if _VERSION.match(v) else DEFAULT_VERSION


def valid_slug(value):
    return bool(_SLUG.match(str(value or "")))


def valid_version(value):
    return bool(_VERSION.match(str(value or "")))


def known(surface, feature=None):
    if surface not in SURFACES:
        return False
    return feature is None or feature in SURFACES[surface]


# message/ai_service.py cache "mode" -> (surface, feature). The cache key carries the prompt version of that pair, so
# bumping a version (env / settings) makes the next request miss the 24h cache instead of serving the old prompt's answer.
_CACHE_MODES = {
    "summary": ("study_room", "summary"),
    "quiz": ("study_room", "quiz"),
    "transcript": ("transcribe", "audio"),
    "replies": ("smart_reply", "suggestions"),
    "classroom_qa": ("copilot", "answer"),
    "doubt": ("doubt", "answer"),
    "revision_deck": ("revision_deck", "deck"),
}


def cache_version(mode):
    """Prompt version to put in a cache key for an ai_service cache `mode` (e.g. "doubt", "studybuddy_quiz_hi")."""
    mode = str(mode or "")
    pair = _CACHE_MODES.get(mode)
    if pair is None and mode.startswith("studybuddy_"):
        parts = mode.split("_")
        if len(parts) >= 2:
            pair = ("study_buddy", parts[1])
    if pair is None:
        return DEFAULT_VERSION
    return version_for(*pair)
