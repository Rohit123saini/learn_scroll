"""
core/product_analytics.py - X3: privacy-safe product analytics (funnel events + A/B results).

PRIVACY BY CONSTRUCTION (read this before adding an event)
    * AGGREGATE COUNTERS ONLY. An event is `+1` on a counter whose key is (event, d1, d2). No user id, device id, IP,
      free text or timestamp-per-person is ever stored - so there is nothing to leak, export or delete per person.
    * FIXED VOCABULARY. Every event has a spec (EVENT_SPECS) and every dimension a normaliser: a value that is not on
      the whitelist is dropped (counted as `dropped{reason="invalid"}`), never stored. Free text cannot get in, so PII
      cannot get in. Settings VALUES are never recorded - only that a setting (by its field name) was changed.
    * CONSENT-GATED. Nothing is counted unless the user's LATEST `analytics` ConsentRecord (S5, login.data_rights) is
      `granted`. Never asked = NOT collected (settings.PRODUCT_ANALYTICS_REQUIRE_OPT_IN, default True, DPDP style).
      Withdrawing takes effect at once (record_consent -> invalidate_consent). Minors are never counted
      (settings.PRODUCT_ANALYTICS_INCLUDE_MINORS, default False). Master switch: settings.PRODUCT_ANALYTICS_ENABLED.
    * A number is therefore "of the users who agreed". Ratios stay honest because numerator and denominator are gated
      by the same rule. The `dropped{reason="no_consent"}` counter shows how much is being left out.
    * No migration: storage is the same 5-minute-style cache counters as feed_ops / call_ops / notification_ops
      (Redis in prod): cumulative `pa:c:*` (Prometheus) + per-day `pa:d:<YYYYMMDD>:*` (JSON summary, 100 days).

WHO RECORDS WHAT
    server (unspoofable, the app needs no change):
        settings_changed         PATCH /profile/preferences/me/, /profile/privacy/, /core/notification-preferences/
        notification_opened      POST /core/notifications/<id>/opened/        (type, surface)
        notification_variant     push sent / opened per copy-experiment variant (core/experiments.py)
        profile_field_added / profile_progress / profile_completed   PATCH /profile/update/ (before -> after)
    client (POST /core/analytics/events/, only CLIENT_EVENTS):
        settings_opened          the denominator of "settings changed"
        profile_edit_opened      the denominator of "profile completed"
        experiment_exposure      feature FLAGS only; the variant must be the user's own assignment

Recording NEVER raises into a request or a task.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import timedelta
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from . import experiments

logger = logging.getLogger(__name__)

# ---- events ---------------------------------------------------------------------------------------------------------
EV_SETTINGS_OPENED = "settings_opened"
EV_SETTINGS_CHANGED = "settings_changed"
EV_NOTIF_OPENED = "notification_opened"
EV_NOTIF_VARIANT = "notification_variant"
EV_PROFILE_EDIT_OPENED = "profile_edit_opened"
EV_PROFILE_FIELD = "profile_field_added"
EV_PROFILE_PROGRESS = "profile_progress"
EV_PROFILE_COMPLETED = "profile_completed"
EV_EXPERIMENT_EXPOSURE = "experiment_exposure"

#: event -> names of its dimensions (Prometheus label names, in order)
EVENT_SPECS: Dict[str, tuple] = {
    EV_SETTINGS_OPENED: ("section",),
    EV_SETTINGS_CHANGED: ("section", "key"),
    EV_NOTIF_OPENED: ("type", "surface"),
    EV_NOTIF_VARIANT: ("experiment", "variant", "stage"),
    EV_PROFILE_EDIT_OPENED: (),
    EV_PROFILE_FIELD: ("field",),
    EV_PROFILE_PROGRESS: ("bucket",),
    EV_PROFILE_COMPLETED: (),
    EV_EXPERIMENT_EXPOSURE: ("experiment", "variant"),
}
#: the only events a phone may report (everything else is written by the server itself)
CLIENT_EVENTS = (EV_SETTINGS_OPENED, EV_PROFILE_EDIT_OPENED, EV_EXPERIMENT_EXPOSURE)

SECTIONS = ("notifications", "privacy", "appearance", "learning", "security", "account", "data", "other")
STAGES = ("pushed", "opened")
BUCKETS = ("0_24", "25_49", "50_74", "75_99", "100")
DROP_REASONS = ("no_consent", "invalid")

DAILY_TTL_SECONDS = 100 * 86400
CONSENT_TTL_SECONDS = 10 * 60
MAX_COMBOS_PER_EVENT = 1500
MAX_KEYS_PER_REQUEST = 12
MAX_SUMMARY_DAYS = 90

_SAFE = re.compile(r"[^a-z0-9_]")
_local: Dict[str, int] = {}
_local_lock = threading.Lock()
_last_warn = [0.0]


# ---------------------------------------------------------------------------------------------------------------------
# switches
# ---------------------------------------------------------------------------------------------------------------------
def enabled() -> bool:
    return bool(getattr(settings, "PRODUCT_ANALYTICS_ENABLED", True))


def require_opt_in() -> bool:
    return bool(getattr(settings, "PRODUCT_ANALYTICS_REQUIRE_OPT_IN", True))


# ---------------------------------------------------------------------------------------------------------------------
# consent gate
# ---------------------------------------------------------------------------------------------------------------------
_CONSENT_GRANTED, _CONSENT_WITHDRAWN, _CONSENT_UNASKED = "g", "w", "u"


def _consent_key(user_id) -> str:
    return f"pa:consent:{int(user_id)}"


def invalidate_consent(user_id) -> None:
    """Called by login.data_rights.record_consent: a withdrawal (or grant) is effective on the very next event."""
    try:
        cache.delete(_consent_key(user_id))
    except Exception:  # pragma: no cover
        pass


def consent_state(user) -> str:
    uid = getattr(user, "pk", None)
    if not uid:
        return _CONSENT_UNASKED
    key = _consent_key(uid)
    try:
        hit = cache.get(key)
        if hit in (_CONSENT_GRANTED, _CONSENT_WITHDRAWN, _CONSENT_UNASKED):
            return hit
    except Exception:  # pragma: no cover - cache down: fall through to the DB
        pass
    from login.models import ConsentRecord

    latest = (ConsentRecord.objects.filter(user_id=uid, purpose=ConsentRecord.PURPOSE_ANALYTICS)
              .order_by("-created_at", "-id").values_list("granted", flat=True).first())
    state = _CONSENT_UNASKED if latest is None else (_CONSENT_GRANTED if latest else _CONSENT_WITHDRAWN)
    try:
        cache.set(key, state, CONSENT_TTL_SECONDS)
    except Exception:  # pragma: no cover
        pass
    return state


def allowed(user) -> bool:
    """May we count an event for `user`? (master switch + logged in + not a minor + consent)."""
    if not enabled() or not getattr(user, "pk", None):
        return False
    if getattr(user, "is_minor", False) and not getattr(settings, "PRODUCT_ANALYTICS_INCLUDE_MINORS", False):
        return False
    state = consent_state(user)
    if state == _CONSENT_GRANTED:
        return True
    if state == _CONSENT_UNASKED:
        return not require_opt_in()
    return False


def can_ask(user) -> bool:
    """X3b - may the app show the first-run "allow analytics?" card? Only a grown-up who was never asked, while the
    master switch is on and consent is actually required. A minor never gets the card (and stays OFF by default);
    someone who already answered (yes OR no) is never asked again - the choice lives in Settings > Data & privacy."""
    if not enabled() or not require_opt_in() or not getattr(user, "pk", None):
        return False
    if getattr(user, "is_minor", False) and not getattr(settings, "PRODUCT_ANALYTICS_INCLUDE_MINORS", False):
        return False
    return consent_state(user) == _CONSENT_UNASKED


# ---------------------------------------------------------------------------------------------------------------------
# normalisation (pure) - a dimension value is on the whitelist or the event is dropped
# ---------------------------------------------------------------------------------------------------------------------
def _clean(value, limit: int = 40) -> str:
    return _SAFE.sub("", str(value or "").strip().lower())[:limit]


def _norm_section(v) -> Optional[str]:
    s = _clean(v)
    return s if s in SECTIONS else "other"


def _norm_key(v) -> Optional[str]:
    s = _clean(v)
    return s or None


def _norm_type(v) -> Optional[str]:
    from . import notification_ops as ops

    return ops.normalize_type(v)


def _norm_surface(v) -> Optional[str]:
    from . import notification_ops as ops

    return ops.normalize_surface(v)


def _norm_field(v) -> Optional[str]:
    from user_profile.profile_header import COMPLETENESS_ITEMS

    s = _clean(v)
    return s if s in {k for k, _w, _t in COMPLETENESS_ITEMS} else "other"


def _norm_bucket(v) -> Optional[str]:
    s = str(v)
    return s if s in BUCKETS else None


def _norm_stage(v) -> Optional[str]:
    s = _clean(v)
    return s if s in STAGES else None


def _norm_experiment(v) -> Optional[str]:
    s = _clean(v, 60)
    return s if s in experiments.registry() else None


_NORMALISERS: Dict[str, Callable] = {
    "section": _norm_section, "key": _norm_key, "type": _norm_type, "surface": _norm_surface,
    "field": _norm_field, "bucket": _norm_bucket, "stage": _norm_stage, "experiment": _norm_experiment,
}


def normalize_dims(event: str, dims: Sequence) -> Optional[List[str]]:
    """Whitelisted dimension values for `event`, or None when the event / any value is not acceptable."""
    names = EVENT_SPECS.get(event)
    if names is None or len(dims) != len(names):
        return None
    out: List[str] = []
    for name, raw in zip(names, dims):
        if name == "variant":
            exp_key = out[names.index("experiment")]  # `experiment` always precedes `variant` in the specs
            s = _clean(raw, 20)
            if s not in experiments.variants_of(exp_key):
                return None
            out.append(s)
            continue
        val = _NORMALISERS[name](raw)
        if not val:
            return None
        out.append(val)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# storage (cache counters, never raises)
# ---------------------------------------------------------------------------------------------------------------------
def _warn_cache_down() -> None:
    now = time.monotonic()
    if now - _last_warn[0] > 60:
        _last_warn[0] = now
        logger.error("product_analytics: cache unavailable - counters are process-local until it is back", exc_info=True)


def _incr(key: str, by: int = 1, ttl: Optional[int] = None) -> None:
    try:
        cache.add(key, 0, ttl)  # ttl None = never expires (cumulative counters)
        cache.incr(key, by)
    except Exception:
        _warn_cache_down()
        with _local_lock:
            if len(_local) > 20000:
                _local.clear()
            _local[key] = _local.get(key, 0) + by


def _read_many(keys: Iterable[str]) -> Dict[str, int]:
    keys = list(keys)
    out: Dict[str, int] = {}
    if not keys:
        return out
    try:
        for i in range(0, len(keys), 1000):
            for k, v in cache.get_many(keys[i:i + 1000]).items():
                try:
                    out[k] = int(v)
                except (TypeError, ValueError):
                    pass
    except Exception:
        _warn_cache_down()
    with _local_lock:
        for k in keys:
            if k in _local:
                out[k] = out.get(k, 0) + _local[k]
    return out


def reset_local() -> None:
    """Tests only."""
    with _local_lock:
        _local.clear()


def _combo(dims: Sequence[str]) -> str:
    return ":".join(dims) if dims else "-"


def _ckey(event: str, combo: str) -> str:
    return f"pa:c:{event}:{combo}"


def _dkey(day: str, event: str, combo: str) -> str:
    return f"pa:d:{day}:{event}:{combo}"


def _today() -> str:
    return timezone.localdate().strftime("%Y%m%d")


def _index_add(event: str, combo: str) -> None:
    """Remember which (d1,d2) combinations exist so the summary / Prometheus text can enumerate them without a key scan.
    The 24 h `seen` marker keeps this to ~1 cache write per combination per day."""
    try:
        if not cache.add(f"pa:seen:{event}:{combo}", 1, 86400):
            return
        idx_key = f"pa:idx:{event}"
        cur = cache.get(idx_key) or []
        if combo not in cur and len(cur) < MAX_COMBOS_PER_EVENT:
            cur.append(combo)
            cache.set(idx_key, cur, DAILY_TTL_SECONDS)
    except Exception:  # pragma: no cover
        pass


def _index(event: str) -> List[str]:
    try:
        return list(cache.get(f"pa:idx:{event}") or [])
    except Exception:  # pragma: no cover
        return []


def _count(event: str, dims: Sequence[str], n: int = 1) -> None:
    combo = _combo(dims)
    _incr(_ckey(event, combo), n)
    _incr(_dkey(_today(), event, combo), n, DAILY_TTL_SECONDS)
    _index_add(event, combo)


def _drop(reason: str) -> None:
    _incr(f"pa:c:dropped:{reason}", 1)
    _incr(f"pa:d:{_today()}:dropped:{reason}", 1, DAILY_TTL_SECONDS)


# ---------------------------------------------------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------------------------------------------------
def track(user, event: str, *dims) -> bool:
    """Count one `event` for `user` (consent-gated). True = counted. Never raises."""
    try:
        if event not in EVENT_SPECS or not enabled():
            return False
        if not getattr(user, "pk", None):
            return False
        if not allowed(user):
            _drop("no_consent")
            return False
        if len(dims) != len(EVENT_SPECS[event]):
            # wrong number of dimensions = a bug in the CALLER, not bad user data: log it, do not count it as a drop
            logger.warning("product_analytics.track(%s): expected %d dims, got %d", event, len(EVENT_SPECS[event]), len(dims))
            return False
        clean = normalize_dims(event, dims)
        if clean is None:
            _drop("invalid")
            return False
        _count(event, clean)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("product_analytics.track(%s) failed", event)
        return False


def track_client(user, payload) -> bool:
    """One client-reported event `{"event": ..., "section"|"experiment"/"variant": ...}`. Only CLIENT_EVENTS."""
    try:
        if not isinstance(payload, dict):
            return False
        event = payload.get("event")
        if event == EV_SETTINGS_OPENED:
            return track(user, event, payload.get("section"))
        if event == EV_PROFILE_EDIT_OPENED:
            return track(user, event)
        if event == EV_EXPERIMENT_EXPOSURE:
            exp = experiments.get(_clean(payload.get("experiment"), 60))
            # a phone cannot claim a variant it was not given, and copy experiments are measured on the server
            if exp is None or exp["kind"] != experiments.KIND_FLAG:
                return False
            if experiments.assign(exp["key"], user) != _clean(payload.get("variant"), 20):
                return False
            return track(user, event, exp["key"], _clean(payload.get("variant"), 20))
    except Exception:  # noqa: BLE001
        logger.exception("product_analytics.track_client failed")
    return False


# ---- server-side hooks (called from views; all of them swallow every error) ------------------------------------------
def track_settings_changed(user, section: str, data, serializer_fields,
                           section_of: Optional[Callable[[str], str]] = None) -> int:
    """A PATCH was saved: count the FIELD NAMES the request touched (never the values). Only names that the serializer
    itself declares (and that are writable) count, so a client cannot invent label values. Returns how many counted."""
    n = 0
    try:
        keys = []
        for k in list(getattr(data, "keys", lambda: [])()):
            field = serializer_fields.get(k) if hasattr(serializer_fields, "get") else None
            if field is not None and not getattr(field, "read_only", False):
                keys.append(str(k))
        for k in sorted(keys)[:MAX_KEYS_PER_REQUEST]:
            sec = section_of(k) if section_of else section
            n += bool(track(user, EV_SETTINGS_CHANGED, sec, k))
    except Exception:  # noqa: BLE001
        logger.exception("product_analytics.track_settings_changed failed")
    return n


def user_pref_section(key: str) -> str:
    """UserPreference field -> settings section (theme / language / text size are Appearance)."""
    if key in ("theme", "language", "font_scale", "appearance"):
        return "appearance"
    if key == "data_storage":
        return "data"
    if key == "login_alerts":
        return "security"
    return "learning"


def bucket_of(percent) -> str:
    p = int(percent or 0)
    return "100" if p >= 100 else "75_99" if p >= 75 else "50_74" if p >= 50 else "25_49" if p >= 25 else "0_24"


def track_profile_change(user, before: Optional[dict], after: Optional[dict]) -> None:
    """Profile saved: compare the completeness (P1) before -> after. Counts which field got filled, the new progress
    bucket and the moment the profile reaches 100 %. No change => nothing counted."""
    try:
        if not before or not after:
            return
        b_pct, a_pct = int(before.get("percent", 0)), int(after.get("percent", 0))
        if a_pct <= b_pct:
            return
        for field in sorted(set(after.get("done") or ()) - set(before.get("done") or ())):
            track(user, EV_PROFILE_FIELD, field)
        track(user, EV_PROFILE_PROGRESS, bucket_of(a_pct))
        if b_pct < 100 <= a_pct:
            track(user, EV_PROFILE_COMPLETED)
    except Exception:  # noqa: BLE001
        logger.exception("product_analytics.track_profile_change failed")


def record_variant_pushed(user, notification_id, tag: str) -> bool:
    """A copy-experiment push was sent: count `pushed` for the variant and remember row -> tag so the later `opened`
    call is attributed by the SERVER (the phone is not trusted to name a variant)."""
    try:
        key, variant = experiments.split_tag(tag)
        if not key:
            return False
        if track(user, EV_NOTIF_VARIANT, key, variant, "pushed"):
            experiments.remember_tag(notification_id, tag)
            return True
    except Exception:  # noqa: BLE001
        logger.exception("product_analytics.record_variant_pushed failed")
    return False


def record_variant_opened(user, notification_id) -> bool:
    try:
        key, variant = experiments.split_tag(experiments.tag_of(notification_id))
        return bool(key) and track(user, EV_NOTIF_VARIANT, key, variant, "opened")
    except Exception:  # noqa: BLE001
        logger.exception("product_analytics.record_variant_opened failed")
        return False


# ---------------------------------------------------------------------------------------------------------------------
# reading: JSON summary (per day) + derived funnels
# ---------------------------------------------------------------------------------------------------------------------
def _pct(num, den):
    return round(100.0 * num / den, 1) if den else None


def _days(days: int, today=None) -> List[str]:
    today = today or timezone.localdate()
    return [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]


def summary(days: int = 7, today=None) -> dict:
    """Totals over the last `days` days (local dates), by event and dimension, plus the derived funnels."""
    days = max(1, min(int(days or 7), MAX_SUMMARY_DAYS))
    day_list = _days(days, today)
    events: Dict[str, dict] = {}
    for event, names in EVENT_SPECS.items():
        combos = _index(event) or (["-"] if not names else [])
        keys = {(c, d): _dkey(d, event, c) for c in combos for d in day_list}
        got = _read_many(keys.values())
        rows = []
        for c in combos:
            total = sum(got.get(keys[(c, d)], 0) for d in day_list)
            if total:
                rows.append({"dims": dict(zip(names, c.split(":"))) if names else {}, "count": total})
        rows.sort(key=lambda r: -r["count"])
        events[event] = {"total": sum(r["count"] for r in rows), "by": rows}
    dropped_keys = {r: [_dkey(d, "dropped", r) for d in day_list] for r in DROP_REASONS}
    flat = _read_many([k for v in dropped_keys.values() for k in v])
    dropped = {r: sum(flat.get(k, 0) for k in ks) for r, ks in dropped_keys.items()}
    return {
        "window": {"days": days, "start": day_list[-1], "end": day_list[0]},
        "events": events,
        "funnels": funnels(events),
        "dropped": dropped,
        "note": "Counts are of users who gave analytics consent (no ids stored). `dropped.no_consent` = events left out.",
    }


def funnels(events: Dict[str, dict]) -> dict:
    def total(e):
        return events.get(e, {}).get("total", 0)

    def by_dim(e, dim):
        out: Dict[str, int] = {}
        for r in events.get(e, {}).get("by", []):
            out[r["dims"].get(dim, "")] = out.get(r["dims"].get(dim, ""), 0) + r["count"]
        return out

    variants: Dict[str, dict] = {}
    for r in events.get(EV_NOTIF_VARIANT, {}).get("by", []):
        d = r["dims"]
        slot = variants.setdefault(d["experiment"], {}).setdefault(d["variant"], {"pushed": 0, "opened": 0})
        slot[d["stage"]] += r["count"]
    for per_exp in variants.values():
        for slot in per_exp.values():
            slot["open_rate_pct"] = _pct(slot["opened"], slot["pushed"])

    return {
        "settings": {
            "opened": total(EV_SETTINGS_OPENED), "changed": total(EV_SETTINGS_CHANGED),
            "changed_by_section": by_dim(EV_SETTINGS_CHANGED, "section"),
            "changes_per_open": _pct(total(EV_SETTINGS_CHANGED), total(EV_SETTINGS_OPENED)),
        },
        "profile": {
            "edit_opened": total(EV_PROFILE_EDIT_OPENED), "progressed": total(EV_PROFILE_PROGRESS),
            "completed": total(EV_PROFILE_COMPLETED),
            "completed_per_edit_open_pct": _pct(total(EV_PROFILE_COMPLETED), total(EV_PROFILE_EDIT_OPENED)),
            "fields_added": by_dim(EV_PROFILE_FIELD, "field"),
        },
        "notifications": {"opened": total(EV_NOTIF_OPENED), "opened_by_surface": by_dim(EV_NOTIF_OPENED, "surface")},
        "experiments": variants,
    }


# ---------------------------------------------------------------------------------------------------------------------
# Prometheus text (cumulative counters; Grafana uses increase()/rate())
# ---------------------------------------------------------------------------------------------------------------------
def _esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def prometheus_text() -> str:
    lines: List[str] = []
    for event, names in EVENT_SPECS.items():
        metric = f"learnscroll_product_{event}_total"
        combos = _index(event) or (["-"] if not names else [])
        keys = {c: _ckey(event, c) for c in combos}
        got = _read_many(keys.values())
        lines.append(f"# HELP {metric} Consent-gated aggregate count of {event} (no user ids).")
        lines.append(f"# TYPE {metric} counter")
        for c in combos:
            vals = c.split(":") if names else []
            label = ",".join(f'{n}="{_esc(v)}"' for n, v in zip(names, vals))
            lines.append(f"{metric}{{{label}}} {got.get(keys[c], 0)}")
    drop_keys = {r: f"pa:c:dropped:{r}" for r in DROP_REASONS}
    got = _read_many(drop_keys.values())
    lines.append("# HELP learnscroll_product_dropped_total Events NOT counted (no consent / invalid value).")
    lines.append("# TYPE learnscroll_product_dropped_total counter")
    for r, k in drop_keys.items():
        lines.append(f'learnscroll_product_dropped_total{{reason="{r}"}} {got.get(k, 0)}')
    lines.append("# HELP learnscroll_product_analytics_enabled 1 when PRODUCT_ANALYTICS_ENABLED is on.")
    lines.append("# TYPE learnscroll_product_analytics_enabled gauge")
    lines.append(f"learnscroll_product_analytics_enabled {1 if enabled() else 0}")
    lines.append("# HELP learnscroll_experiment_running 1 for every experiment that is switched on.")
    lines.append("# TYPE learnscroll_experiment_running gauge")
    for key, exp in sorted(experiments.registry().items()):
        lines.append(f'learnscroll_experiment_running{{experiment="{_esc(key)}",kind="{exp["kind"]}"}} '
                     f'{1 if exp["enabled"] and experiments.master_switch() else 0}')
    return "\n".join(lines) + "\n"
