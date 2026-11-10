"""
user_profile/featured.py - P6 "Featured" slot. NOT RUN.

ONE pinned test series / class / resource under the highlights row of a profile.

    GET    /profile/profile/<username>/featured/    anyone who may see the profile -> {"featured": card | null, "locked": bool}
    PUT    /profile/featured/                       me: {"kind": "test"|"class"|"resource", "target_id": "..."} -> {"featured": card}
    DELETE /profile/featured/                       me: 204 (idempotent)
    GET    /profile/featured/candidates/?kind=      me: what I may pin (max 30, newest first) -> {"kind", "results": [card, ..]}

card = {"kind", "id", "title", "subtitle", "image_url", "meta": {...}}   (meta keys depend on the kind, see `_card_*`)

What may be featured (only PUBLIC things the user made themselves):
  test      TestSeries   creator = me, published, source "individual"  (what Explore / the marketplace shows)
  class     Classroom    teacher = me, active, not deleted, not flagged (what Explore shows; same filter as profile_layout)
  resource  Post         user = me, kind "resource", public, approved, not deleted / archived / still scheduled

Safety
  * the target is re-checked on EVERY read: gone / archived / flagged / made private / copyright-held => the slot reads as
    empty (`featured: null`) - never a card that leads to a 404, never a leak of a private title;
  * setting someone else's thing, an unknown id and a non-public thing are ONE error (`featured_not_found`) - it does not
    say which one it was;
  * visitors: blocked either way -> the view answers 404; private account + not an accepted follower -> `locked: true`, no card;
  * the card carries no price / buyer / viewer data, only what the public page of that item already shows.
"""
from __future__ import annotations

import logging
import uuid

from django.db.models import Count

from .models import ProfileFeatured

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 30
TITLE_CHARS = 80


class FeaturedError(Exception):
    """A rule was broken. Turned into a 400 by the views."""

    def __init__(self, message, code="invalid", field=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.field = field


# --------------------------------------------------------------------------- id handling

def parse_target(kind, raw):
    """Normalise `raw` for `kind` (class -> int, test / resource -> UUID) or None when it cannot be that kind of id."""
    text = str(raw if raw is not None else "").strip()
    if not text or len(text) > 40:
        return None
    try:
        if kind == ProfileFeatured.KIND_CLASS:
            value = int(text)
            return value if value > 0 else None
        return uuid.UUID(text)
    except (ValueError, TypeError, AttributeError):
        return None


def _short(text, limit=TITLE_CHARS):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


# --------------------------------------------------------------------------- what may be featured

def eligible_tests(owner):
    from testseries.models import TestSeries

    return TestSeries.objects.filter(
        creator=owner, status=TestSeries.Status.PUBLISHED, source=TestSeries.Source.INDIVIDUAL,
    )


def eligible_classes(owner):
    from .profile_layout import _public_classrooms  # one definition of "public classroom" for the whole profile

    return _public_classrooms(owner)


def eligible_resources(owner):
    from post.models import Post

    return Post.objects.filter(
        user=owner, kind=Post.KIND_RESOURCE, visibility="public", moderation_status="approved",
        is_deleted=False, is_archived=False, is_scheduled=False,
    )


def eligible_queryset(owner, kind):
    if kind == ProfileFeatured.KIND_TEST:
        return eligible_tests(owner)
    if kind == ProfileFeatured.KIND_CLASS:
        return eligible_classes(owner)
    if kind == ProfileFeatured.KIND_RESOURCE:
        return eligible_resources(owner)
    return None


# --------------------------------------------------------------------------- cards

def _abs(request, url):
    if not url:
        return None
    try:
        return request.build_absolute_uri(url) if request is not None else url
    except Exception:  # noqa: BLE001 - a picture must never break the card
        return None


def _card_test(s, request):
    return {
        "kind": "test", "id": str(s.pk), "title": _short(s.title), "subtitle": _short(s.subject, 60), "image_url": None,
        "meta": {
            "total_marks": int(s.total_marks or 0),
            "duration_minutes": int(s.duration_minutes) if s.duration_minutes else None,
            "is_paid": bool(s.is_paid),
        },
    }


def _card_class(c, request):
    cover = None
    try:
        cover = _abs(request, c.cover_image.url) if c.cover_image else None
    except Exception:  # noqa: BLE001
        cover = None
    return {
        "kind": "class", "id": str(c.pk), "title": _short(c.title), "subtitle": _short(c.subject, 60), "image_url": cover,
        "meta": {
            "rating_avg": float(c.rating_avg or 0), "rating_count": int(c.rating_count or 0),
            "enrolled_count": int(c.enrolled_count or 0),
        },
    }


def _card_resource(p, request):
    title = p.title or p.content or ""
    sub = " - ".join(x for x in (p.subject, p.level) if x)
    files = getattr(p, "files_count", None)
    if files is None:
        files = p.attachments.count()
    # No picture on purpose: resource thumbnails live under the PRIVATE media prefix (common/private_media.py).
    return {
        "kind": "resource", "id": str(p.pk), "title": _short(title) or "Resource", "subtitle": _short(sub, 60),
        "image_url": None, "meta": {"files": int(files)},
    }


_BUILDERS = {"test": _card_test, "class": _card_class, "resource": _card_resource}


def card_for(kind, obj, request):
    return _BUILDERS[kind](obj, request)


def _lookup(owner, kind, target_id):
    qs = eligible_queryset(owner, kind)
    key = parse_target(kind, target_id)
    if qs is None or key is None:
        return None
    if kind == ProfileFeatured.KIND_RESOURCE:
        qs = qs.annotate(files_count=Count("attachments"))
    return qs.filter(pk=key).first()


# --------------------------------------------------------------------------- read

def current_card(owner, request):
    """The owner's featured card, or None when nothing is pinned or the target is no longer public / theirs."""
    row = ProfileFeatured.objects.filter(user=owner).first()
    if row is None:
        return None
    try:
        obj = _lookup(owner, row.kind, row.target_id)
        return card_for(row.kind, obj, request) if obj is not None else None
    except Exception:  # noqa: BLE001 - fail closed: a broken lookup shows no card, never an error on the profile
        logger.exception("featured lookup failed for user %s", owner.pk)
        return None


def candidates(owner, kind, request):
    qs = eligible_queryset(owner, kind)
    if qs is None:
        raise FeaturedError("Unknown kind.", code="featured_kind", field="kind")
    if kind == ProfileFeatured.KIND_RESOURCE:
        qs = qs.annotate(files_count=Count("attachments"))
    order = "-created_at"
    return [card_for(kind, o, request) for o in qs.order_by(order)[:MAX_CANDIDATES]]


# --------------------------------------------------------------------------- write

def set_featured(owner, kind, target_id):
    if kind not in dict(ProfileFeatured.KIND_CHOICES):
        raise FeaturedError("Unknown kind.", code="featured_kind", field="kind")
    obj = _lookup(owner, kind, target_id)
    if obj is None:
        raise FeaturedError(
            "That item was not found, or it is not public, so it cannot be featured.",
            code="featured_not_found", field="target_id",
        )
    ProfileFeatured.objects.update_or_create(user=owner, defaults={"kind": kind, "target_id": str(obj.pk)})
    return obj


def clear_featured(owner):
    ProfileFeatured.objects.filter(user=owner).delete()
