"""
post/feed_config.py - T1 item 7: ONE place that answers "which weights / ratios / limits is the feed using
for this user right now?".

Every knob already lives in `settings.py` (env-overridable, no DB model, no migration) as one dict per
stage; this module does not invent a second store, it READS them all through the same accessors the
ranking code uses and applies the viewer's A/B variant (`post/feed_experiment.py`):

    section        settings block         accessor used by the ranking code
    ------------   --------------------   ------------------------------------------------
    mix            FEED_MIX_RATIOS        feed_mix.get_ratios(override)
    limits         FEED_MIX_LIMITS        feed_mix.get_limits()
    seen           FEED_SEEN_LIMITS       feed_mix.get_seen_limits()
    diversity      FEED_DIVERSITY         views.HomeFeedView._serialize_slices  (variant: "diversity")
    author_cap     FEED_AUTHOR_CAP        feed_mix.apply_author_caps            (variant: "author_cap")
    quality        FEED_QUALITY           feed_quality.config_for               (variant: "quality")
    explore        FEED_EXPLORE           feed_explore.get_config               (variant: "explore")
    signals        FEED_SIGNALS           feed_signals.get_config               (variant: "signals")
    context        FEED_CONTEXT           feed_context.get_config               (variant: "context")
    cache          FEED_CANDIDATE_CACHE   feed_cache.get_config

`effective(user_id)` returns all of that + the user's experiment bucket / variant. It is what
`GET /post/<id>/why/` shows to STAFF (stage-wise explanation incl. the exact numbers) and what you print
in a shell to debug "why does user X get a different feed than user Y".
"""
from __future__ import annotations

from typing import Dict

# variant override section name -> key in `effective()`
OVERRIDABLE_SECTIONS = ("mix", "diversity", "author_cap", "quality", "explore", "signals", "context")


def effective(user_id) -> Dict[str, object]:
    from django.conf import settings

    from . import feed_cache, feed_context, feed_experiment, feed_explore, feed_mix, feed_quality, feed_signals

    exp = feed_experiment.resolve(user_id)
    ov = exp["overrides"]
    diversity = dict(getattr(settings, "FEED_DIVERSITY", None) or {})
    diversity.update(ov.get("diversity") or {})
    author_cap = dict(getattr(settings, "FEED_AUTHOR_CAP", None) or {})
    author_cap.update(ov.get("author_cap") or {})
    return {
        "experiment": {"name": exp["experiment"], "bucket": exp["bucket"], "variant": exp["variant"],
                       "overridden_sections": sorted(k for k in ov if k in OVERRIDABLE_SECTIONS)},
        "mix": feed_mix.get_ratios(ov.get("mix")),
        "limits": feed_mix.get_limits(),
        "seen": feed_mix.get_seen_limits(),
        "diversity": diversity,
        "author_cap": author_cap,
        "quality": feed_quality.config_for(ov.get("quality")),
        "explore": feed_explore.get_config(ov.get("explore")),
        "signals": feed_signals.get_config(ov.get("signals")),
        "context": feed_context.get_config(ov.get("context")),
        "cache": feed_cache.get_config(),
    }
