"""
post/feed_experiment.py - T1 Part 3: A/B bucket for the feed.

`experiment_bucket(user_id)` is a stable hash (0..buckets-1) of (experiment
name, user id): the same user always lands in the same bucket, different
experiment names reshuffle. Variants split the bucket range by weight; a
variant carries `overrides` per config section (e.g. {"explore": {"share": 0.15}})
that `section()` merges over the base settings.

Default = ONE variant "default" with no overrides -> behaviour unchanged.
settings.FEED_EXPERIMENT = {"enabled", "name", "buckets", "variants": [{"name", "weight", "overrides"}]}
Pure python apart from reading settings.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "name": "feed_adv_v1",
    "buckets": 100,
    "variants": [{"name": "default", "weight": 100, "overrides": {}}],
}


def get_config() -> dict:
    from django.conf import settings

    cfg = dict(DEFAULT_CONFIG)
    cfg.update(getattr(settings, "FEED_EXPERIMENT", None) or {})
    try:
        cfg["buckets"] = max(1, int(cfg["buckets"]))
    except (TypeError, ValueError):
        cfg["buckets"] = DEFAULT_CONFIG["buckets"]
    return cfg


def experiment_bucket(user_id, name: str = "feed_adv_v1", buckets: int = 100) -> int:
    digest = hashlib.md5(f"{name}:{user_id}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % max(1, int(buckets))


def pick_variant(bucket: int, variants: List[dict], buckets: int) -> dict:
    """Variants own consecutive slices of the bucket range, sized by weight."""
    clean = []
    for v in variants or []:
        try:
            w = float(v.get("weight", 0))
        except (TypeError, ValueError):
            w = 0.0
        if w > 0 and v.get("name"):
            clean.append((v, w))
    if not clean:
        return {"name": "default", "overrides": {}}
    total = sum(w for _, w in clean)
    point = (bucket + 0.5) / buckets * total
    acc = 0.0
    for v, w in clean:
        acc += w
        if point < acc:
            return v
    return clean[-1][0]


def resolve(user_id) -> dict:
    """{"experiment", "bucket", "variant", "overrides"} for one user."""
    cfg = get_config()
    if not cfg["enabled"]:
        return {"experiment": cfg["name"], "bucket": 0, "variant": "default", "overrides": {}}
    bucket = experiment_bucket(user_id, cfg["name"], cfg["buckets"])
    v = pick_variant(bucket, cfg["variants"], cfg["buckets"])
    return {
        "experiment": cfg["name"],
        "bucket": bucket,
        "variant": v["name"],
        "overrides": dict(v.get("overrides") or {}),
    }


def section(base: dict, overrides: dict, name: str) -> dict:
    """`base` config of one section with the variant's overrides applied."""
    out = dict(base)
    out.update((overrides or {}).get(name) or {})
    return out
