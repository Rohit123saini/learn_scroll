# user_profile/views/_common.py — split out of the old user_profile/views.py (P-C4). Code is moved verbatim.

import logging
from ..models import (
    BlockUser,
    RestrictUser,
)
from django.db.models import Q
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from rest_framework import status


User = get_user_model()
logger = logging.getLogger("user_profile.views")  # same logger name as the old user_profile/views.py


def _wallet_busy_response():
    """503 + Retry-After for CoinLedgerBusy (a wallet/purchase row lock timed
    out). Nothing was written, so retrying is safe — and a payment gateway
    retries a 5xx webhook on its own."""
    response = Response({
        "status": False,
        "message": "The wallet is busy right now. Please retry in a moment.",
    }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    response["Retry-After"] = "2"
    return response


def _refuse_adult_profile_photo(request):
    """-> a 422 Response when an uploaded profile / cover photo is adult content, else None. Never raises (fails open)."""
    try:
        from types import SimpleNamespace

        from common import media_moderation as mm

        if mm.action() != "hold":
            return None
        items = []
        for key in ("profile_photo", "cover_photo"):
            f = request.FILES.get(key)
            if f is not None and hasattr(f, "read"):
                items.append(SimpleNamespace(name=getattr(f, "name", "") or key, content_type=getattr(f, "content_type", ""),
                                             declared_type="image", file=f, path=""))
        hits = [h for h in mm.scan(items) if h.score >= mm.min_score()]
        if not hits:
            return None
        return Response({
            "status": False, "code": "media_not_allowed",
            "message": "This photo can't be used because it looks like adult content. Please choose a different one.",
            "errors": {"profile_photo": ["Photo not allowed."]},
        }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    except Exception:
        logger.exception("profile photo adult-content check failed - skipping it")
        return None


def is_blocked_between(user_a, user_b):
    """
    🔥 NEW: true if either user has blocked the other. Nothing in the
    original code checked this — you could follow, message-search-match,
    and view the full profile of someone who blocked you (or whom you'd
    blocked), which defeats the point of blocking.
    """
    return BlockUser.objects.filter(
        Q(blocker=user_a, blocked=user_b) | Q(blocker=user_b, blocked=user_a)
    ).exists()


def is_restricted_between(user, other):
    """
    TASK 18 — true if `user` has restricted `other`. Deliberately ONE-
    WAY (unlike `is_blocked_between`, which is symmetric): restrict
    only affects what *the restricting user* experiences from `other`,
    never the reverse, and `other` must never be able to detect it from
    this check's result — see RestrictUser's docstring in models.py.

    Other apps (posts, message, notifications) should filter through
    this the same way they'd filter through `is_blocked_between` for
    block, once they're ready to apply restrict's actual effects
    (hiding comments from everyone but their author, muting read-
    receipts/online-status, suppressing notifications) — that
    integration is out of scope for user_profile itself.
    """
    return RestrictUser.objects.filter(user=user, restricted=other).exists()
