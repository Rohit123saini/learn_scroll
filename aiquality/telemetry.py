"""aiquality/telemetry.py - P-F6: one place that sees every AI call.

    with ai_context("doubt", "answer", request.user) as ctx:      # the HTTP layer says WHAT the call is for
        answer = generate_doubt_answer(...)                         # the model seam calls observe(...) inside
    body["ai_meta"] = ctx.meta()                                     # the app echoes this back with a 👍 / 👎

    observe(call, prompt=text)   wraps ONE model call: times it, reads token usage, writes an AiCallLog row.
    note_cache_hit()             counts an answer that was served from a cache (no model call, no cost).

Rules:
  * Telemetry must NEVER break an AI answer: every write is guarded and runs in its own savepoint.
  * observe() has to run in the REQUEST thread (a ContextVar does not cross into the Gemini thread pool), so it wraps
    the code that submits to the pool, not the code inside it.
  * Cost is an ESTIMATE: tokens x settings.AI_PRICE_INPUT_PER_M_USD / AI_PRICE_OUTPUT_PER_M_USD (defaults = public
    Gemini 2.5 Flash list price). Change the two numbers when the model or the price changes.
"""
import contextvars
import hashlib
import logging
import time
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from . import prompts

logger = logging.getLogger(__name__)

_CTX = contextvars.ContextVar("aiquality_ctx", default=None)
_HIT_TTL = 3 * 24 * 3600


class AiContext:
    __slots__ = ("surface", "feature", "user_id", "prompt_version", "call_id", "model", "cached", "usage")

    def __init__(self, surface, feature, user_id=None):
        self.surface = surface
        self.feature = feature
        self.user_id = user_id
        self.prompt_version = prompts.version_for(surface, feature)
        self.call_id = None
        self.model = ""
        self.cached = False
        self.usage = None  # (tokens_in, tokens_out) noted by a seam that only returns text

    def meta(self):
        """What the app echoes back in a feedback request. `call_id` is None for a cached answer."""
        return {
            "surface": self.surface,
            "feature": self.feature,
            "prompt_version": self.prompt_version,
            "model": self.model or _model_name(),
            "call_id": str(self.call_id) if self.call_id else None,
            "cached": bool(self.cached and not self.call_id),
        }


@contextmanager
def ai_context(surface, feature, user=None):
    ctx = AiContext(surface, feature, getattr(user, "id", None) if user is not None else None)
    token = _CTX.set(ctx)
    try:
        yield ctx
    finally:
        _CTX.reset(token)


def current():
    return _CTX.get()


def _model_name():
    try:
        from message.ai_service import _MODEL
        return str(_MODEL)[:60]
    except Exception:  # pragma: no cover
        return ""


def _num(name, default):
    try:
        return float(getattr(settings, name, default))
    except (TypeError, ValueError):
        return float(default)


def _int(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def usage_from(res):
    """(tokens_in, tokens_out) from a google-genai response, or None when the response carries no numbers.
    'Thinking' tokens are billed like output tokens, so they are added to the output side."""
    um = getattr(res, "usage_metadata", None)
    if um is None:
        return None
    tin = _int(getattr(um, "prompt_token_count", None))
    tout = _int(getattr(um, "candidates_token_count", None))
    think = _int(getattr(um, "thoughts_token_count", None)) or 0
    if tin is None and tout is None:
        return None
    return tin or 0, (tout or 0) + think


def note_usage(res):
    """For a seam that returns only text: remember the usage of its response for the observe() around it."""
    ctx = current()
    if ctx is not None:
        u = usage_from(res)
        if u:
            ctx.usage = u


def cost_micro_usd(tokens_in, tokens_out):
    """USD per 1M tokens == micro-USD per token, so no extra scaling is needed."""
    return int(round(tokens_in * _num("AI_PRICE_INPUT_PER_M_USD", 0.30) + tokens_out * _num("AI_PRICE_OUTPUT_PER_M_USD", 2.50)))


def prompt_fingerprint(prompt):
    if not isinstance(prompt, str) or not prompt:
        return ""
    # whitespace-insensitive, so re-indenting a prompt does not look like a new prompt
    return hashlib.sha1(" ".join(prompt.split()).encode("utf-8", "ignore")).hexdigest()[:12]


def _text_of(res):
    if isinstance(res, str):
        return res
    t = getattr(res, "text", None)
    return t if isinstance(t, str) else ""


def _write(ctx, ok, started, prompt, result, error_kind, has_image):
    try:
        latency = max(int((time.monotonic() - started) * 1000), 0)
        usage = usage_from(result) or (ctx.usage if ctx is not None else None)
        estimated = usage is None
        if usage is None:
            usage = (max(len(prompt) // 4, 1) if isinstance(prompt, str) else 0,
                     max(len(_text_of(result)) // 4, 0) if ok else 0)
        tin, tout = usage
        surface = ctx.surface if ctx else "other"
        feature = ctx.feature if ctx else "other"
        with transaction.atomic():
            row = _call_model().objects.create(
                user_id=ctx.user_id if ctx else None,
                surface=surface, feature=feature,
                model=_model_name(),
                prompt_version=ctx.prompt_version if ctx else prompts.DEFAULT_VERSION,
                prompt_hash=prompt_fingerprint(prompt),
                ok=ok, error_kind=(error_kind or "")[:40], has_image=bool(has_image),
                latency_ms=latency, tokens_in=tin, tokens_out=tout, tokens_estimated=estimated,
                cost_micro_usd=cost_micro_usd(tin, tout) if ok else 0,
            )
        if ctx is not None and ok:
            ctx.call_id = row.id
            ctx.model = row.model
        return row
    except Exception:  # telemetry must never break an AI answer
        logger.warning("aiquality: could not log AI call", exc_info=True)
        return None


def _call_model():
    from .models import AiCallLog
    return AiCallLog


def observe(call, *, prompt=None, has_image=False):
    """Run `call()` (one model call) and log it. Returns whatever `call` returns; re-raises its exception."""
    ctx = current()
    started = time.monotonic()
    try:
        result = call()
    except Exception as exc:
        _write(ctx, False, started, prompt, None, type(exc).__name__, has_image)
        raise
    _write(ctx, True, started, prompt, result, "", has_image)
    return result


# ------------------------------------------------------------------------------------------ cache-hit counters
def _hit_key(surface, feature, day=None):
    day = day or timezone.localtime(timezone.now()).strftime("%Y%m%d")
    return f"aiq:hit:{day}:{surface}.{feature}"


def note_cache_hit(surface=None, feature=None):
    """+1 for an answer served from a cache. Uses the current ai_context when surface / feature are not given."""
    ctx = current()
    if ctx is not None:
        ctx.cached = True
    surface = surface or (ctx.surface if ctx else None)
    feature = feature or (ctx.feature if ctx else None)
    if not surface:
        return
    try:
        key = _hit_key(surface, feature)
        cache.add(key, 0, _HIT_TTL)
        cache.incr(key)
    except Exception:
        pass


def cache_hits(surface, feature, day):
    try:
        return int(cache.get(_hit_key(surface, feature, day)) or 0)
    except Exception:
        return 0


def call_in_context(_surface, _feature, _user, _fn, *args, **kwargs):
    """`result, ctx = call_in_context("doubt", "answer", request.user, generate_doubt_answer, question=...)`

    Same as `with ai_context(...): result = _fn(...)`, as one expression so a long existing call does not have to be
    re-indented. `ctx.meta()` is what the response puts in `ai_meta`."""
    with ai_context(_surface, _feature, _user) as ctx:
        result = _fn(*args, **kwargs)
    return result, ctx
