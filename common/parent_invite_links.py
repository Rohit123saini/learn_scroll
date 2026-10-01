# common/parent_invite_links.py
"""
Ek hi jagah se "parent-add confirmation link" banta hai — campus aur
tuitionclass dono isi helper ko use karte hain, taaki link ka format /
domain kabhi do jagah alag na ho jaaye.

Link me hamesha `code` (message.ParentAccessCode.code — plaintext,
one-time-shareable) hota hai. `campus` ya `classroom` query param
context batata hai ki confirm kis app me hoga (campus ke liye
CampusParentLink banega, tuitionclass ke liye sirf ParentAccessCode
verify hoga — wahan koi extra "link" row ki zaroorat nahi, ParentToken
hi asli access hai).

Settings me `PARENT_INVITE_LINK_BASE` set karo (e.g.
"https://learnscroll.app/parent-link" — ek universal/app link jo apka
Flutter app intercept karta ho). Agar set nahi hai to ek safe default
pe fall back karta hai taaki local/dev me bhi kaam kare.
"""
from urllib.parse import urlencode

from django.conf import settings
from django.utils import timezone


def generate_or_reuse_parent_code(student, label, ttl_days=None):
    """Shared by campus/parent_invite.py + tuitionclass/parent_link_views.py's
    bulk-send views (FIX — this used to be copy-pasted identically in both
    files; any future tweak, e.g. the "don't spam a fresh code every bulk
    send" rule below, had to be remembered in two places and could easily
    drift). One student having multiple *active* codes at once is fine and
    expected (that's what lets multiple parents each hold their own code —
    see ParentAccessCode.MAX_ACTIVE_CODES) — this helper only avoids
    minting a *redundant extra* code on a repeat bulk-send when a
    still-active one already exists, so re-running "send to all" doesn't
    invalidate/duplicate links parents may have already used.

    Returns (code_obj, created: bool).
    """
    # Local import — avoids a hard, always-on dependency from this shared
    # `common` module on the `message` app for callers that only need
    # build_parent_invite_link()/parent_invite_share_text() below.
    from message.models import ParentAccessCode

    existing = (
        ParentAccessCode.objects.filter(student=student, is_active=True)
        .exclude(expires_at__lte=timezone.now())
        .order_by("-created_at")
        .first()
    )
    if existing:
        return existing, False
    kwargs = {"label": label}
    if ttl_days:
        kwargs["ttl_days"] = ttl_days
    return ParentAccessCode.generate_for(student, **kwargs), True


def build_parent_invite_link(*, code: str, campus_id=None, classroom_id=None) -> str:
    base = getattr(settings, "PARENT_INVITE_LINK_BASE", "https://learnscroll.app/parent-link")
    params = {"code": code}
    if campus_id is not None:
        params["campus"] = str(campus_id)
    if classroom_id is not None:
        params["classroom"] = str(classroom_id)
    return f"{base}?{urlencode(params)}"


def parent_invite_share_text(*, student_name: str, link: str) -> str:
    """Student ko bheja jaane wala message — student isko apne parent ko
    WhatsApp/SMS pe forward kar sakta hai. Plaintext code kahin nahi —
    sirf link, jisme code embedded hai (parent ko alag se type nahi karna)."""
    return (
        f"{student_name}, apne parent ko ye link bhejo taaki unka access "
        f"add ho jaaye — click karte hi confirm ho jayega:\n{link}"
    )
