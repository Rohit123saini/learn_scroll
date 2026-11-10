# ============================================================
# SHARE A TUITION CLASS / TEST SERIES INTO THE FEED
#
# A feed post can carry one embedded "item card" (shared_type + shared_id on
# Post). The card is resolved at READ time (always current: price, availability,
# whether the owner allows commission) and never trusts anything the client sent
# beyond the two ids.
#
# COMMISSION (nothing here moves coins; it only hands the viewer's app what it
# needs to attribute through the normal link flow POST /referrals/attribute/):
#   * `ref_code` is the SHARER's referral code, included ONLY when the item's
#     owner allows commission (Classroom.referral_enabled /
#     TestSeries.referral_enabled) and the sharer is not the owner.
#   * So a feed share behaves exactly like sharing a ?ref= URL: first touch wins,
#     expires after REFERRAL_ATTRIBUTION_DAYS, pays on the existing rules (class:
#     daily release; test series: once per buyer). Sharing the app or a profile
#     never produces a ref_code and never pays.
#
# Cross-app imports are lazy (golden rule: post never hard-depends on
# tuitionclass / testseries at import time).
# ============================================================
from decimal import Decimal

SHARE_TYPES = ("classroom", "testseries")


def _cover(request, f):
    try:
        if f and getattr(f, "name", ""):
            url = f.url
            return request.build_absolute_uri(url) if request else url
    except Exception:  # noqa: BLE001
        pass
    return None


def _sharer_code(sharer_id):
    from tuitionclass.models import referral_code_for_user

    return referral_code_for_user(sharer_id)


def _gone(shared_type, shared_id):
    return {"type": shared_type, "id": str(shared_id), "is_unavailable": True}


def _classroom_card(c, sharer_id, request, code_for):
    can_earn = c.referral_active and c.teacher_id != sharer_id
    pct = Decimal(c.referral_commission_percent or 0) if can_earn else Decimal(0)
    return {
        "type": "classroom", "id": str(c.id), "title": c.title,
        "subtitle": c.subject or "", "owner_username": c.teacher.username,
        "cover": _cover(request, c.cover_image), "price_coins": None,
        "can_earn": can_earn and pct > 0, "commission_percent": str(pct),
        "commission_basis": "daily",
        "ref_code": code_for(sharer_id) if can_earn and pct > 0 else None,
        "is_unavailable": False,
    }


def _series_card(s, sharer_id, code_for):
    from testseries.access import series_is_referable, series_referral_percent

    can_earn = series_is_referable(s) and s.creator_id != sharer_id
    return {
        "type": "testseries", "id": str(s.id), "title": s.title,
        "subtitle": s.subject or "", "owner_username": s.creator.username,
        "cover": None, "price_coins": int(s.price_coins) if s.is_paid else 0,
        "can_earn": can_earn,
        "commission_percent": str(series_referral_percent(s)) if can_earn else "0",
        "commission_basis": "per_user",
        "ref_code": code_for(sharer_id) if can_earn else None,
        "is_unavailable": False,
    }


def resolve_shared_item(shared_type, shared_id, sharer_id, request=None):
    """-> card dict, or {"type","id","is_unavailable": True} when it can no
    longer be shown. Never raises."""
    gone = _gone(shared_type, shared_id)
    try:
        if shared_type == "classroom":
            from tuitionclass.models import Classroom

            c = Classroom.objects.select_related("teacher").filter(pk=shared_id, is_active=True).first()
            return gone if c is None else _classroom_card(c, sharer_id, request, _sharer_code)
        if shared_type == "testseries":
            from testseries.models import TestSeries

            s = TestSeries.objects.select_related("creator").filter(
                pk=shared_id, status=TestSeries.Status.PUBLISHED, source=TestSeries.Source.INDIVIDUAL,
            ).first()
            return gone if s is None else _series_card(s, sharer_id, _sharer_code)
    except Exception:  # noqa: BLE001
        pass
    return gone


def resolve_shared_items_bulk(posts, request=None):
    """H2: the cards for a whole feed page -> {post.pk: card}. Same output as calling resolve_shared_item per
    post, but 1 query per item type (instead of 2+ per post) and the sharer's referral code is looked up once
    per distinct sharer. Posts it cannot resolve are simply absent: the caller falls back to resolve_shared_item.
    Never raises."""
    out = {}
    try:
        posts = [p for p in posts if getattr(p, "shared_type", "") in SHARE_TYPES and p.shared_id]
        if not posts:
            return out
        codes = {}

        def code_for(sharer_id):
            if sharer_id not in codes:
                codes[sharer_id] = _sharer_code(sharer_id)
            return codes[sharer_id]

        classrooms, series = {}, {}
        class_ids = {p.shared_id for p in posts if p.shared_type == "classroom"}
        series_ids = {p.shared_id for p in posts if p.shared_type == "testseries"}
        if class_ids:
            from tuitionclass.models import Classroom

            classrooms = {str(c.pk): c for c in
                          Classroom.objects.select_related("teacher").filter(pk__in=class_ids, is_active=True)}
        if series_ids:
            from testseries.models import TestSeries

            series = {str(s.pk): s for s in TestSeries.objects.select_related("creator").filter(
                pk__in=series_ids, status=TestSeries.Status.PUBLISHED, source=TestSeries.Source.INDIVIDUAL)}
        for p in posts:
            if p.shared_type == "classroom":
                c = classrooms.get(str(p.shared_id))
                out[p.pk] = _gone(p.shared_type, p.shared_id) if c is None else _classroom_card(c, p.user_id, request, code_for)
            else:
                s = series.get(str(p.shared_id))
                out[p.pk] = _gone(p.shared_type, p.shared_id) if s is None else _series_card(s, p.user_id, code_for)
    except Exception:  # noqa: BLE001 - e.g. a malformed shared_id: let the per-post path decide per post
        return {}
    return out


def can_share(shared_type, shared_id):
    """Is this item publicly shareable at all? (the card resolver decides what
    the viewer sees; this only gates creating the post)."""
    return not resolve_shared_item(shared_type, shared_id, sharer_id=None).get("is_unavailable")
