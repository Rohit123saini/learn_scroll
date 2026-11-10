"""
core/experiments.py - X3: A/B flags and notification-copy experiments (privacy-safe, NO database, NO migration).

WHAT IT GIVES
    assign(key, user)              -> variant name, or None (experiment off / user outside the rollout / not eligible)
    apply_copy(user, type, t, b)   -> (title, body, tag)  push copy for that user's variant; tag = "key:variant" or ""
    flags_for(user)                -> {key: {"variant": ..., "kind": "flag"|"copy"}}  (GET core/analytics/config/)

HOW A USER LANDS IN A VARIANT
    sha256(f"{salt}:{key}:{user_id}") -> two independent numbers:
        1st  decides "inside the rollout?"    (rollout_pct of users take part, the rest see the normal product)
        2nd  decides which variant           (by `weight`)
    Same user + same experiment + same salt  => always the same variant, on every server and every device, with
    nothing stored. Change `salt` to re-shuffle everybody (start a new round). Nothing here is personal data.

ASSIGNMENT IS NOT TRACKING
    Being shown variant B is a product decision and needs no consent. MEASURING it does: the counters in
    core/product_analytics.py (pushed / opened per variant) only count users whose latest `analytics` consent is
    granted. So the numbers are "of the consenting users" on both sides of the ratio.

SAFETY RAILS (enforced here, not left to config)
    * never on a NEVER_MUTE type (security, payments, child-safety, calls ...): `notif_types` are filtered on load
    * not for minors (settings.EXPERIMENTS_INCLUDE_MINORS, default False)
    * `enabled=False` (the default for the sample) or settings.EXPERIMENTS_ENABLED=False => nobody is in any experiment
    * a broken registry entry is skipped (logged once), it never breaks a push or a request

REGISTRY
    DEFAULT_EXPERIMENTS below + settings.EXPERIMENTS (dict, merged per key, so a deployment can flip `enabled` or
    change `rollout_pct` without a code change). Env shortcut: EXPERIMENTS_ON="key1,key2" switches those keys on.

    {
      "notif_copy_streak_at_risk_v1": {
          "enabled": False,
          "salt": "r1",                    # change to start a new round
          "rollout_pct": 100,              # 0..100
          "notif_types": ["streak_at_risk"],   # empty / missing => plain feature FLAG (no copy change)
          "variants": {
              "control": {"weight": 50},                                   # no copy keys => original text
              "b":       {"weight": 50, "title": "...", "body": "..."},   # {title} / {body} = the original text
          },
      },
    }
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Dict, Optional, Tuple

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

KIND_FLAG = "flag"
KIND_COPY = "copy"

_KEY_RE = re.compile(r"^[a-z0-9_]{3,60}$")
_VARIANT_RE = re.compile(r"^[a-z0-9_]{1,20}$")
MAX_TITLE = 80
MAX_BODY = 240
TAG_TTL_SECONDS = 14 * 86400   # same lifetime as the notification "opened" de-dupe key

DEFAULT_EXPERIMENTS: Dict[str, dict] = {
    # Sample, OFF until a deployment turns it on (EXPERIMENTS_ON=notif_copy_streak_at_risk_v1).
    "notif_copy_streak_at_risk_v1": {
        "enabled": False,
        "salt": "r1",
        "rollout_pct": 100,
        "notif_types": ["streak_at_risk"],
        "variants": {
            "control": {"weight": 50},
            "b": {"weight": 50, "title": "Your streak needs you", "body": "{body}"},
        },
    },
}

_warned: set = set()


def _warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning("experiments: %s", msg)


# ---------------------------------------------------------------------------------------------------------------------
# registry (settings-merged, validated)
# ---------------------------------------------------------------------------------------------------------------------
def _never_mute_types() -> frozenset:
    try:
        from .models import NotificationPreference

        return frozenset(str(t) for t in NotificationPreference.NEVER_MUTE_TYPES)
    except Exception:  # pragma: no cover - model import problem: fail closed below (no copy experiments)
        return frozenset()


def _env_on() -> set:
    import os

    return {k.strip() for k in os.getenv("EXPERIMENTS_ON", "").split(",") if k.strip()}


def master_switch() -> bool:
    return bool(getattr(settings, "EXPERIMENTS_ENABLED", True))


def registry() -> Dict[str, dict]:
    """Validated experiments, merged DEFAULT_EXPERIMENTS + settings.EXPERIMENTS. Never raises."""
    out: Dict[str, dict] = {}
    merged: Dict[str, dict] = {k: dict(v) for k, v in DEFAULT_EXPERIMENTS.items()}
    try:
        for key, cfg in (getattr(settings, "EXPERIMENTS", None) or {}).items():
            if isinstance(cfg, dict):
                merged[key] = {**merged.get(key, {}), **cfg}
    except Exception:  # pragma: no cover
        _warn_once("settings", "settings.EXPERIMENTS is not a dict of dicts - ignored")
    env_on = _env_on()
    never = _never_mute_types()
    for key, cfg in merged.items():
        try:
            clean = _validate(key, cfg, never)
            if clean is None:
                continue
            if key in env_on:
                clean["enabled"] = True
            out[key] = clean
        except Exception:  # noqa: BLE001
            _warn_once(key, f"experiment {key!r} skipped (invalid config)")
    return out


def _validate(key: str, cfg: dict, never_mute: frozenset) -> Optional[dict]:
    if not _KEY_RE.match(str(key)):
        _warn_once(str(key), f"experiment key {key!r} must match [a-z0-9_]{{3,60}}")
        return None
    variants = {}
    for name, v in (cfg.get("variants") or {}).items():
        if not _VARIANT_RE.match(str(name)) or not isinstance(v, dict):
            continue
        try:
            weight = max(0, int(v.get("weight", 1)))
        except (TypeError, ValueError):
            continue
        variants[str(name)] = {
            "weight": weight,
            "title": str(v["title"])[:MAX_TITLE * 2] if v.get("title") else "",
            "body": str(v["body"])[:MAX_BODY * 2] if v.get("body") else "",
        }
    if len(variants) < 2 or not any(v["weight"] > 0 for v in variants.values()):
        _warn_once(str(key), f"experiment {key!r} needs at least 2 variants with a weight")
        return None
    types = [str(t) for t in (cfg.get("notif_types") or [])]
    # safety / payments / calls can never be copy-tested: drop them from the list (an all-locked list => not a copy test)
    types = [t for t in types if t not in never_mute]
    kind = KIND_COPY if types else KIND_FLAG
    if cfg.get("notif_types") and not types:
        _warn_once(str(key), f"experiment {key!r}: every notif_type is locked (never-mute) - not run")
        return None
    try:
        rollout = max(0, min(100, int(cfg.get("rollout_pct", 100))))
    except (TypeError, ValueError):
        rollout = 0
    return {
        "key": str(key), "enabled": bool(cfg.get("enabled", False)), "salt": str(cfg.get("salt", "r1")),
        "rollout_pct": rollout, "notif_types": types, "variants": variants, "kind": kind,
    }


def get(key: str) -> Optional[dict]:
    exp = registry().get(str(key))
    return exp if exp and exp["enabled"] and master_switch() else None


def variants_of(key: str) -> Tuple[str, ...]:
    exp = registry().get(str(key))
    return tuple(exp["variants"]) if exp else ()


# ---------------------------------------------------------------------------------------------------------------------
# assignment (pure + deterministic)
# ---------------------------------------------------------------------------------------------------------------------
def _numbers(salt: str, key: str, user_id) -> Tuple[int, int]:
    digest = hashlib.sha256(f"{salt}:{key}:{user_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % 10000, int.from_bytes(digest[8:16], "big") % 10000


def pick_variant(exp: dict, user_id) -> Optional[str]:
    """The variant of `user_id` in `exp` (a validated registry entry), or None when outside the rollout."""
    in_roll, pick = _numbers(exp["salt"], exp["key"], user_id)
    if in_roll >= exp["rollout_pct"] * 100:
        return None
    total = sum(v["weight"] for v in exp["variants"].values())
    if total <= 0:
        return None
    point = pick % total
    acc = 0
    for name, v in exp["variants"].items():   # dict order = config order = stable
        acc += v["weight"]
        if point < acc:
            return name
    return None  # pragma: no cover


def eligible(user) -> bool:
    if not getattr(user, "pk", None):
        return False
    if getattr(user, "is_minor", False) and not getattr(settings, "EXPERIMENTS_INCLUDE_MINORS", False):
        return False
    return True


def assign(key: str, user) -> Optional[str]:
    exp = get(key)
    if exp is None or not eligible(user):
        return None
    return pick_variant(exp, user.pk)


def flags_for(user) -> Dict[str, dict]:
    """Every running experiment this user is in -> {key: {"variant", "kind"}}. {} for anonymous / minors / all off."""
    out: Dict[str, dict] = {}
    if not master_switch() or not eligible(user):
        return out
    for key, exp in registry().items():
        if not exp["enabled"]:
            continue
        v = pick_variant(exp, user.pk)
        if v is not None:
            out[key] = {"variant": v, "kind": exp["kind"]}
    return out


# ---------------------------------------------------------------------------------------------------------------------
# notification copy
# ---------------------------------------------------------------------------------------------------------------------
def _render(template: str, *, title: str, body: str, limit: int) -> str:
    # Only {title} / {body} are substituted (str.replace, not format(): a stray brace in copy must not raise).
    return template.replace("{title}", title).replace("{body}", body)[:limit].strip()


def copy_experiment_for(notif_type) -> Optional[dict]:
    """The running copy experiment that covers `notif_type` (first one wins), or None."""
    if not master_switch():
        return None
    t = str(notif_type)
    for exp in registry().values():
        if exp["enabled"] and exp["kind"] == KIND_COPY and t in exp["notif_types"]:
            return exp
    return None


def apply_copy(user, notif_type, title: str, body: str) -> Tuple[str, str, str]:
    """(title, body, tag). tag = "key:variant" when the user is in a copy experiment for this type (control included,
    so the control arm is measured too), else "". Never raises: on any problem the ORIGINAL copy comes back."""
    try:
        exp = copy_experiment_for(notif_type)
        if exp is None or not eligible(user):
            return title, body, ""
        variant = pick_variant(exp, user.pk)
        if variant is None:
            return title, body, ""
        v = exp["variants"][variant]
        new_title = _render(v["title"], title=title, body=body, limit=MAX_TITLE) if v["title"] else title
        new_body = _render(v["body"], title=title, body=body, limit=MAX_BODY) if v["body"] else body
        return (new_title or title), (new_body or body), f"{exp['key']}:{variant}"
    except Exception:  # noqa: BLE001
        logger.exception("experiments.apply_copy failed")
        return title, body, ""


# ---------------------------------------------------------------------------------------------------------------------
# notification row -> variant tag (so the "opened" endpoint can attribute an open without trusting the client)
# ---------------------------------------------------------------------------------------------------------------------
def remember_tag(notification_id, tag: str) -> None:
    try:
        if notification_id and tag:
            cache.set(f"exp:tag:{int(notification_id)}", tag, TAG_TTL_SECONDS)
    except Exception:  # pragma: no cover - cache down: the open just is not attributed to a variant
        pass


def tag_of(notification_id) -> str:
    try:
        return str(cache.get(f"exp:tag:{int(notification_id)}") or "")
    except Exception:  # pragma: no cover
        return ""


def split_tag(tag: str) -> Tuple[str, str]:
    """"key:variant" -> (key, variant), only when both still exist in the registry; else ("", "")."""
    key, _, variant = str(tag or "").partition(":")
    if key and variant in variants_of(key):
        return key, variant
    return "", ""
