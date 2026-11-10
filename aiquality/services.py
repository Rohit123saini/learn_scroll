"""aiquality/services.py - P-F6: saving a student's feedback, staff review actions, summary numbers.

All rules live here (the views only map the result to HTTP) so they can be tested without a request.
"""
import hashlib
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from . import prompts
from .models import AiCallLog, AiFeedback

REASONS = {c for c, _ in AiFeedback.Reason.choices}
STAFF_STATUSES = {c for c, _ in AiFeedback.Status.choices}


class FeedbackError(Exception):
    def __init__(self, code, message, http=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http = http


def _setting_int(name, default):
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return int(default)


def _setting_float(name, default):
    try:
        return float(getattr(settings, name, default))
    except (TypeError, ValueError):
        return float(default)


def _clean(value, limit):
    s = " ".join(str(value or "").replace("\x00", "").split()) if limit <= 160 else str(value or "").replace("\x00", "").strip()
    return s[:limit]


def _rating(raw):
    if isinstance(raw, str):
        raw = {"up": 1, "down": -1, "none": 0, "clear": 0}.get(raw.strip().lower(), raw)
    try:
        r = int(raw)
    except (TypeError, ValueError):
        raise FeedbackError("invalid_rating", "rating must be up, down or none.")
    if r not in (-1, 0, 1):
        raise FeedbackError("invalid_rating", "rating must be up, down or none.")
    return r


def _as_bool(v):
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def dedupe_key(surface, feature, ref_id, call_id, excerpt):
    ident = ref_id or (str(call_id) if call_id else "") or (
        hashlib.sha1(excerpt.encode("utf-8", "ignore")).hexdigest()[:16] if excerpt else "-")
    return hashlib.sha1(f"{surface}|{feature}|{ident}".encode("utf-8", "ignore")).hexdigest()[:40]


def _call_or_none(raw):
    if not raw:
        return None
    try:
        cid = uuid.UUID(str(raw))
    except ValueError:
        return None
    return AiCallLog.objects.filter(id=cid).first()


def _meta(data):
    meta = data.get("ai_meta")
    return meta if isinstance(meta, dict) else {}


def submit(user, data):
    """Create / update / clear the user's feedback on one AI output -> (AiFeedback | None, auto_removed: bool).

    Rules: rating +1 / -1 (0 clears it); "report wrong answer" implies 👎; one row per (user, output); the prompt version
    is taken from the call row when the app echoed a call_id (the server's own record), otherwise from the app's echo if it
    looks like a version, otherwise from what the server runs now."""
    meta = _meta(data)
    surface = str(data.get("surface") or meta.get("surface") or "").strip().lower()
    feature = str(data.get("feature") or meta.get("feature") or "").strip().lower()
    if surface not in prompts.FEEDBACK_SURFACES or not prompts.known(surface, feature):
        raise FeedbackError("unknown_feature", "This answer cannot be rated.", 404)

    rating = _rating(data.get("rating", 0))
    wrong = _as_bool(data.get("wrong") or data.get("reported_wrong"))
    if wrong:
        rating = -1
    reason = str(data.get("reason") or "").strip().lower()
    if reason and reason not in REASONS:
        raise FeedbackError("invalid_reason", "Unknown reason.")
    if wrong and not reason:
        reason = AiFeedback.Reason.WRONG
    comment = _clean(data.get("comment"), 500)
    excerpt = _clean(data.get("excerpt"), 800)
    ref_id = _clean(data.get("ref_id"), 80)
    if ref_id and not all(ch.isalnum() or ch in "-_.:" for ch in ref_id):
        raise FeedbackError("invalid_ref", "Bad reference.")

    call = _call_or_none(data.get("call_id") or meta.get("call_id"))
    if call is not None and (call.surface, call.feature) != (surface, feature):
        call = None  # a call id from another feature: do not trust it
    key = dedupe_key(surface, feature, ref_id, call.id if call else None, excerpt)

    existing = AiFeedback.objects.filter(user=user, dedupe_key=key).first()
    if rating == 0 and not wrong:
        if existing is not None and not existing.reported_wrong:
            existing.delete()
            return None, False
        if existing is not None:  # a report cannot be taken back by clearing the thumb - keep it as 👎
            return existing, False
        return None, False

    version = call.prompt_version if call is not None else ""
    if not version:
        v = str(meta.get("prompt_version") or data.get("prompt_version") or "").strip()
        version = v if prompts.valid_version(v) else prompts.version_for(surface, feature)
    model = call.model if call is not None else _clean(meta.get("model") or "", 60)

    fields = {
        "rating": rating,
        "reason": reason if rating < 0 else "",
        "comment": comment if rating < 0 else "",
        "reported_wrong": bool(wrong or (existing and existing.reported_wrong and rating < 0)),
    }
    try:
        with transaction.atomic():
            if existing is None:
                fb = AiFeedback.objects.create(
                    user=user, surface=surface, feature=feature, ref_id=ref_id, call=call,
                    prompt_version=version, model=model, excerpt=excerpt, dedupe_key=key, **fields)
            else:
                for k, v in fields.items():
                    setattr(existing, k, v)
                if excerpt and not existing.excerpt:
                    existing.excerpt = excerpt
                if existing.status != AiFeedback.Status.NEW and (wrong or rating < 0):
                    existing.status = AiFeedback.Status.NEW  # new complaint on a reviewed row: look again
                existing.save()
                fb = existing
    except IntegrityError:  # double tap: both requests created the row, the second one just updates it
        fb = AiFeedback.objects.filter(user=user, dedupe_key=key).first()
        if fb is None:
            raise
    removed = _maybe_remove_cached_output(fb) if fb.reported_wrong else False
    return fb, removed


def _maybe_remove_cached_output(fb):
    """A shared cached AI result (feed post study) that enough DIFFERENT students call wrong is dropped, so the next
    student gets a fresh answer instead of the same bad one. Returns True when a cached row was removed."""
    if fb.surface != "post_study" or not fb.ref_id:
        return False
    threshold = _setting_int("AI_QUALITY_AUTO_REMOVE_REPORTS", 3)
    if threshold <= 0:
        return False
    try:
        ref = uuid.UUID(fb.ref_id)
    except ValueError:
        return False
    n = AiFeedback.objects.filter(surface="post_study", ref_id=fb.ref_id, reported_wrong=True).values("user").distinct().count()
    if n < threshold:
        return False
    from post.models import PostAiStudy

    deleted, _ = PostAiStudy.objects.filter(id=ref).delete()
    if deleted:
        AiFeedback.objects.filter(surface="post_study", ref_id=fb.ref_id, status=AiFeedback.Status.NEW).update(
            staff_note=f"auto: cached result removed after {n} wrong-answer reports")
    return bool(deleted)


def mine(user, surface, feature, ref_id):
    key = dedupe_key(surface, feature, _clean(ref_id, 80), None, "")
    return AiFeedback.objects.filter(user=user, dedupe_key=key).first()


# --------------------------------------------------------------------------------------------- staff
def review(fb, staff_user, status=None, note=None, remove_cached=False):
    if status is not None:
        if status not in STAFF_STATUSES:
            raise FeedbackError("invalid_status", "Unknown status.")
        fb.status = status
        fb.reviewed_by = staff_user
        fb.reviewed_at = timezone.now()
    if note is not None:
        fb.staff_note = _clean(note, 500)
    fb.save()
    removed = False
    if remove_cached and fb.surface == "post_study" and fb.ref_id:
        try:
            ref = uuid.UUID(fb.ref_id)
        except ValueError:
            ref = None
        if ref is not None:
            from post.models import PostAiStudy

            removed = bool(PostAiStudy.objects.filter(id=ref).delete()[0])
    return removed


def quality_summary(days=30):
    """Per (surface, feature, prompt_version): votes, 👎 rate, reports, and whether it needs a look."""
    since = timezone.now() - timedelta(days=max(1, min(int(days), 365)))
    fb_rows = (AiFeedback.objects.filter(created_at__gte=since)
               .values("surface", "feature", "prompt_version")
               .annotate(total=Count("id"),
                         up=Count("id", filter=Q(rating=1)),
                         down=Count("id", filter=Q(rating=-1)),
                         wrong=Count("id", filter=Q(reported_wrong=True)),
                         open=Count("id", filter=Q(status=AiFeedback.Status.NEW) & (Q(rating=-1) | Q(reported_wrong=True)))))
    calls = {(r["surface"], r["feature"], r["prompt_version"]): r["n"]
             for r in AiCallLog.objects.filter(created_at__gte=since, ok=True)
             .values("surface", "feature", "prompt_version").annotate(n=Count("id"))}
    min_votes = _setting_int("AI_QUALITY_MIN_VOTES", 10)
    alert_rate = _setting_float("AI_QUALITY_ALERT_DOWN_RATE", 0.30)
    out = []
    for r in fb_rows:
        k = (r["surface"], r["feature"], r["prompt_version"])
        rated = r["up"] + r["down"]
        down_rate = (r["down"] / rated) if rated else 0.0
        out.append({
            "surface": r["surface"], "feature": r["feature"], "prompt_version": r["prompt_version"] or "?",
            "votes": rated, "up": r["up"], "down": r["down"], "reported_wrong": r["wrong"], "open": r["open"],
            "down_rate": round(down_rate, 3), "model_calls": calls.get(k, 0),
            "needs_attention": rated >= min_votes and down_rate >= alert_rate,
        })
    out.sort(key=lambda x: (not x["needs_attention"], -x["down_rate"], -x["votes"]))
    return out
