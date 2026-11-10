"""
doubts/services.py — orchestration used by views.py: list / detail / summary /
ask / act. Pure glue between hub.py (vocabulary + merge) and the adapters.
"""
from __future__ import annotations

import logging

from django.core.cache import cache

from . import hub
from .adapters.campus import CampusAdapter
from .adapters.classroom_chat import ClassroomChatAdapter
from .adapters.feed_post import FeedPostAdapter
from .adapters.testseries import TestSeriesAdapter
from .adapters.tuition import TuitionAdapter

logger = logging.getLogger(__name__)

SUMMARY_CACHE_SECONDS = 30
SUMMARY_CACHE_VERSION = "v1"

ADAPTERS = {a.source: a for a in (
    ClassroomChatAdapter(), CampusAdapter(), TuitionAdapter(), FeedPostAdapter(), TestSeriesAdapter(),
)}
assert set(ADAPTERS) == set(hub.SOURCES), "every hub source needs exactly one adapter"


def _adapter(source: str):
    return ADAPTERS[source]


def _summary_key(user_id):
    return f"doubts:hub:summary:{SUMMARY_CACHE_VERSION}:{user_id}"


def invalidate_summary(user_id) -> None:
    cache.delete(_summary_key(user_id))


def list_hub(user, flt: "hub.HubFilter", request=None) -> dict:
    """One merged, newest-first page across the requested sources.

    A source that raises is logged and skipped (reported under `errors`) —
    one broken subsystem must never blank the whole hub."""
    per_source, errors = [], []
    for source in flt.sources:
        try:
            per_source.append(_adapter(source).fetch(user, flt, request))
        except Exception:
            logger.exception("doubts hub: source %s failed", source)
            errors.append(source)
    page, next_key = hub.merge_pages(per_source, flt.limit)
    return {
        "results": page,
        "next_cursor": hub.encode_cursor(*next_key) if next_key else None,
        "errors": errors,
    }


def _load(user, hub_id, request=None):
    source, source_id = hub.decode_id(hub_id)
    adapter = _adapter(source)
    found = adapter.get(user, source_id, request)
    if found is None:
        raise hub.HubError("Doubt not found.", 404, "not_found")
    obj, ctx = found
    adapter.prepare_objs([obj], ctx)
    return adapter, obj, ctx


def get_detail(user, hub_id, request=None) -> dict:
    adapter, obj, ctx = _load(user, hub_id, request)
    item = adapter.to_item(obj, ctx)
    item["thread"] = adapter.thread(obj, ctx)
    return item


def get_summary(user, request=None, use_cache=True) -> dict:
    key = _summary_key(user.pk)
    if use_cache:
        cached = cache.get(key)
        if cached is not None:
            return cached
    counts, to_answer, errors = {}, {}, []
    for source, adapter in ADAPTERS.items():
        try:
            counts[source], to_answer[source] = adapter.counts(user, request)
        except Exception:
            logger.exception("doubts hub: summary for %s failed", source)
            errors.append(source)
            counts[source], to_answer[source] = hub.empty_counts(), 0
    out = hub.summarize(counts, to_answer)
    out["errors"] = errors
    if use_cache and not errors:
        cache.set(key, out, SUMMARY_CACHE_SECONDS)
    return out


def get_targets(user, request=None) -> list:
    out = []
    for source, adapter in ADAPTERS.items():
        try:
            out.extend(adapter.targets(user, adapter.build_ctx(user, request)))
        except Exception:
            logger.exception("doubts hub: targets for %s failed", source)
    return out


def _result(response, adapter, user, request, fallback_id=None):
    """Turn a source view's Response into (body, status). Errors pass
    through verbatim; successes are re-read through the adapter so the
    client always gets the unified shape back."""
    if response.status_code >= 400:
        return response.data, response.status_code
    data = response.data if isinstance(response.data, dict) else {}
    source_id = fallback_id or data.get("id")
    if source_id:
        found = adapter.get(user, source_id, request)
        if found:
            obj, ctx = found
            adapter.prepare_objs([obj], ctx)
            item = adapter.to_item(obj, ctx)
            item["thread"] = adapter.thread(obj, ctx)
            return item, response.status_code
    return {"id": hub.encode_id(adapter.source, source_id) if source_id else None, "result": data}, response.status_code


def ask(request, payload: dict):
    source = (payload.get("source") or "").strip()
    if source not in hub.SOURCES:
        raise hub.HubError(f"source must be one of {', '.join(hub.SOURCES)}.", 400, "bad_source")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise hub.HubError("text is required.", 400, "missing_text")
    adapter = _adapter(source)
    response = adapter.dispatch_ask(request, dict(payload, text=text))
    body, code = _result(response, adapter, request.user, request)
    if code < 400:
        invalidate_summary(request.user.pk)
    return body, code


def act(request, hub_id: str, action: str, payload: dict):
    if action not in hub.ACTIONS:
        raise hub.HubError(f"action must be one of {', '.join(hub.ACTIONS)}.", 400, "bad_action")
    adapter, obj, ctx = _load(request.user, hub_id, request)
    adapter.check_action(action)
    if action == hub.ACTION_ANSWER and not str(payload.get("text") or "").strip():
        raise hub.HubError("text is required.", 400, "missing_text")
    response = adapter.dispatch_action(request, obj, action, payload, ctx)
    body, code = _result(response, adapter, request.user, request, fallback_id=obj.pk)
    if code < 400:
        # The asker's and (potentially) every teacher's badge may have changed.
        invalidate_summary(request.user.pk)
        asker = getattr(obj, "author_id", None) or getattr(obj, "asked_by_id", None) or getattr(obj, "user_id", None)
        if asker:
            invalidate_summary(asker)
    return body, code
