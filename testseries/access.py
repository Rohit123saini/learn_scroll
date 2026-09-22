# testseries/access.py
"""
Who may SEE and ATTEMPT a test series?

The problem this fixes
    `TestSeriesViewSet` used to list every published series to every
    logged-in user, and `start()` let anyone attempt any of them — including
    a *campus* series that belongs to one school's section, or a live-class
    series sold to a classroom's students. Series of `source=individual`
    are public by design (that is the marketplace); the other two are not.

The rule
    individual    published -> everyone.            draft -> creator only.
    campus        published -> members of that section, or active staff of
                  that campus.                      draft -> creator only.
    liveclass     published -> that classroom's teacher / staff / pass
                  holders.                          draft -> creator only.
    the creator and platform staff (`is_staff`) can always access.

How membership is resolved WITHOUT breaking the golden rule
    (`testseries` never imports `campus` / `liveclass`)
    `settings.TESTSERIES_CONTEXT_ACCESS` maps a `context_type` to a dotted path
    of a function owned by the other app:

        {"section":   "campus.bridge.user_accessible_testseries_context_ids",
         "classroom": "liveclass.bridge.user_accessible_testseries_context_ids"}

    signature  fn(*, user, context_type) -> Iterable[UUID]  (the context ids the
    user may access). Set a value to `"public"` to make that source
    world-readable (e.g. a live-class marketplace); set
    `TESTSERIES_ENFORCE_CONTEXT_ACCESS = False` to switch the whole check off.

    Fail-CLOSED: if a resolver can't be imported or raises, the user gets
    no access to that source (logged), never accidental access.
"""
from __future__ import annotations

import importlib
import logging
from django.conf import settings
from django.db.models import Q

from .models import TestSeries

logger = logging.getLogger(__name__)

DEFAULT_RESOLVERS = {
    "section": "campus.bridge.user_accessible_testseries_context_ids",
    "classroom": "liveclass.bridge.user_accessible_testseries_context_ids",
}
PUBLIC = "public"

# source -> the `context_type` string its bridge writes onto the series.
_SOURCE_CONTEXT = (
    (TestSeries.Source.CAMPUS, "section"),
    (TestSeries.Source.LIVECLASS, "classroom"),
)


def enforcement_enabled() -> bool:
    return bool(getattr(settings, "TESTSERIES_ENFORCE_CONTEXT_ACCESS", True))


def _resolver_setting(context_type: str):
    mapping = getattr(settings, "TESTSERIES_CONTEXT_ACCESS", None) or {}
    return mapping.get(context_type, DEFAULT_RESOLVERS.get(context_type))


def _load(path: str):
    module_path, _, func_name = path.rpartition(".")
    return getattr(importlib.import_module(module_path), func_name)


def accessible_context_ids(user, context_type: str):
    """`set` of context ids the user may access, the string `"public"` when the
    source is configured world-readable, or an empty set (fail-closed)."""
    target = _resolver_setting(context_type)
    if target == PUBLIC:
        return PUBLIC
    if not target:
        return set()
    try:
        return set(_load(target)(user=user, context_type=context_type))
    except Exception:  # noqa: BLE001 — fail closed, but never silently
        logger.exception("TESTSERIES_CONTEXT_ACCESS resolver %s failed; denying access.", target)
        return set()


def visible_series_q(user) -> Q:
    """Q() for "series this user may see": their own (any status) OR published
    series they are allowed to reach. Compose with `.filter(...)`."""
    if not enforcement_enabled():
        return Q(status=TestSeries.Status.PUBLISHED) | Q(creator=user)
    if getattr(user, "is_staff", False):
        return Q()  # platform staff: everything

    q = Q(creator=user) | Q(status=TestSeries.Status.PUBLISHED, source=TestSeries.Source.INDIVIDUAL)
    for source, context_type in _SOURCE_CONTEXT:
        ids = accessible_context_ids(user, context_type)
        if ids == PUBLIC:
            q |= Q(status=TestSeries.Status.PUBLISHED, source=source)
        elif ids:
            q |= Q(status=TestSeries.Status.PUBLISHED, source=source, context_type=context_type, context_id__in=ids)
    return q


def user_can_access_series(user, series: TestSeries) -> bool:
    """Single-object version of `visible_series_q` (used by start / questions)."""
    if series.creator_id == getattr(user, "id", None):
        return True
    if getattr(user, "is_staff", False):
        return True
    if series.status != TestSeries.Status.PUBLISHED:
        return False
    if not enforcement_enabled() or series.source == TestSeries.Source.INDIVIDUAL:
        return True
    context_type = "section" if series.source == TestSeries.Source.CAMPUS else "classroom"
    ids = accessible_context_ids(user, context_type)
    if ids == PUBLIC:
        return True
    return series.context_id in ids

