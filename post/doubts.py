"""
post/doubts.py - everything behind the upgraded "Ask a doubt" post.

    question          Post.content (+ the question photo as a PostMedia, cropped on the phone) and PostDoubt.ocr_text
                      (text the phone read from the photo: ML Kit), so the doubt is searchable even if the asker typed little.
    structure         PostDoubt: subject / class_level / exam / chapter, `is_urgent`.
    bounty (coins)    optional. Taken from the asker's wallet (user_profile.CoinLedger) when the doubt is posted and HELD:
                          post      SPEND                         ref  doubt_bounty_hold:<post_id>    asker -N
                          solved    DOUBT_BOUNTY (to the answerer) ref  doubt_bounty_pay:<post_id>     answerer +N
                          expired / deleted / own answer chosen
                                    REFUND (to the asker)          ref  doubt_bounty_refund:<post_id>  asker +N
                      Every step has a `reference`, so a retry can never pay twice (CoinLedger.record_transaction is
                      idempotent per (user, reference)), and the PostDoubt row is locked while it is settled.
                      Bounty coins are spendable but never withdrawable (DOUBT_BOUNTY is not in
                      user_profile.fraud.eligible_source_types()).
    best answer       the asker pins an answer -> PostDoubt.solved_at is set ("Solved" badge) and a held bounty is paid to
                      that answerer. Once a bounty is PAID the best answer is locked (no second payout).
    similar doubts    GET /post/doubts/similar/?q=&subject=  (token overlap over question + OCR text, solved ones first)
                      so the asker finds the existing answer instead of posting a duplicate.
    teachers          verified teachers whose subject matches (campus approved SubjectTeacherassigments or
                      UserPreference.focus_subjects) get a `feed_doubt_for_teacher` notification, once per doubt.
    answers           text, a step-wise solution (PostAnswerStep: text and/or picture per step), up to 4 pictures and one
                      short video (PostAnswerAttachment).

Settings (all optional): DOUBT_URGENT_PER_DAY (5), DOUBT_BOUNTY_EXPIRY_DAYS (14), DOUBT_TEACHER_NOTIFY_MAX (50),
DOUBT_ANSWER_VIDEO_MAX_MB (50), DOUBT_ANSWER_IMAGE_MAX_MB (10), DOUBT_MAX_ANSWERS_PER_USER (5).
"""
import json
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from .models import Post, PostAnswer, PostAnswerAttachment, PostAnswerStep, PostDoubt

logger = logging.getLogger(__name__)
User = get_user_model()

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")

MAX_OCR_CHARS = 4000
MAX_ANSWER_CONTENT = 5000
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
VIDEO_EXTENSIONS = {"mp4", "mov", "webm"}
QUESTION_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_QUESTION_PHOTOS = 4

SIMILAR_MIN_CHARS = 8
SIMILAR_MAX_CHARS = 300
SIMILAR_DEFAULT_LIMIT = 5
SIMILAR_MAX_LIMIT = 10
SIMILAR_CANDIDATES = 300
SIMILAR_MIN_COVERAGE = 0.5
SOLVED_BOOST = 0.10
MAX_KEYWORDS = 6

_STOPWORDS = frozenset({
    "the", "is", "are", "a", "an", "of", "to", "in", "on", "and", "or", "for", "what", "how", "why", "this", "that",
    "with", "me", "my", "i", "you", "hai", "hain", "kya", "ka", "ki", "ke", "ko", "se", "mein", "kaise", "kyu", "kyun",
    "ye", "yeh", "wo", "woh", "aur", "nahi", "nhi", "find", "solve", "question", "sawaal", "doubt",
})
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _cfg(name, default):
    return getattr(settings, name, default)


def _clean_text(value, limit):
    if value is None:
        return ""
    text = _CTRL_RE.sub("", str(value))
    return _WS_RE.sub(" ", text).strip()[:limit]


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def _norm(text):
    return _WS_RE.sub(" ", (text or "").strip()).casefold()


def _tokens(text):
    return {t for t in _TOKEN_RE.findall((text or "").casefold()) if len(t) > 1 and t not in _STOPWORDS}


def _ext(upload):
    name = getattr(upload, "name", "") or ""
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _file_url(field, request=None):
    if not field:
        return None
    try:
        url = field.url
    except ValueError:
        return None
    return request.build_absolute_uri(url) if request is not None else url


def _wallet_balance(user):
    """Fresh coin balance from the DB (the request's user object may be a cached copy)."""
    return int(User.objects.filter(pk=user.pk).values_list("coin", flat=True).first() or 0)


def doubt_config(user=None):
    """Numbers the composer / answer sheet need, so the client never hard-codes the limits."""
    return {
        "min_bounty": PostDoubt.MIN_BOUNTY,
        "max_bounty": PostDoubt.MAX_BOUNTY,
        "bounty_expiry_days": int(_cfg("DOUBT_BOUNTY_EXPIRY_DAYS", 14)),
        "urgent_per_day": int(_cfg("DOUBT_URGENT_PER_DAY", 5)),
        "max_question_photos": MAX_QUESTION_PHOTOS,
        "max_ocr_chars": MAX_OCR_CHARS,
        "max_steps": PostAnswerStep.MAX_STEPS,
        "max_step_chars": PostAnswerStep.MAX_TEXT,
        "max_answer_images": PostAnswerAttachment.MAX_IMAGES,
        "max_answer_videos": PostAnswerAttachment.MAX_VIDEOS,
        "max_answer_video_mb": int(_cfg("DOUBT_ANSWER_VIDEO_MAX_MB", 50)),
        "max_answer_image_mb": int(_cfg("DOUBT_ANSWER_IMAGE_MAX_MB", 10)),
        "similar_min_chars": SIMILAR_MIN_CHARS,
        "wallet_balance": _wallet_balance(user) if user is not None and getattr(user, "is_authenticated", False) else 0,
    }


# ---------------------------------------------------------------------------
# Posting a doubt
# ---------------------------------------------------------------------------
def clean_doubt_settings(raw, user):
    """Validate the `doubt_settings` JSON of POST /post/create/ (post_type "doubt").
    Returns the cleaned dict; raises serializers.ValidationError({"doubt_settings": ...}) for the client."""
    if raw in (None, ""):
        raw = {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise serializers.ValidationError({"doubt_settings": "doubt_settings valid JSON object hona chahiye."})
    if not isinstance(raw, dict):
        raise serializers.ValidationError({"doubt_settings": "doubt_settings valid JSON object hona chahiye."})

    cleaned = {
        "subject": _clean_text(raw.get("subject"), 80),
        "class_level": _clean_text(raw.get("class_level"), 40),
        "exam": _clean_text(raw.get("exam"), 60),
        "chapter": _clean_text(raw.get("chapter"), 120),
        "is_urgent": _truthy(raw.get("is_urgent")),
        "ocr_text": _clean_text(raw.get("ocr_text"), MAX_OCR_CHARS),
        "bounty_coins": 0,
    }

    try:
        bounty = int(raw.get("bounty_coins") or 0)
    except (TypeError, ValueError):
        raise serializers.ValidationError({"doubt_settings": "bounty_coins number hona chahiye."})
    if bounty < 0:
        raise serializers.ValidationError({"doubt_settings": "bounty_coins negative nahi ho sakta."})
    if bounty:
        if bounty < PostDoubt.MIN_BOUNTY or bounty > PostDoubt.MAX_BOUNTY:
            raise serializers.ValidationError({
                "doubt_settings": f"Bounty {PostDoubt.MIN_BOUNTY} se {PostDoubt.MAX_BOUNTY} coins ke beech honi chahiye."})
        if not cleaned["subject"]:
            raise serializers.ValidationError({"doubt_settings": "Bounty lagane ke liye subject chunna zaroori hai."})
        balance = _wallet_balance(user)
        if balance < bounty:
            raise serializers.ValidationError({
                "doubt_settings": f"Wallet me sirf {balance} coins hain, bounty {bounty} coins ki hai."})
        cleaned["bounty_coins"] = bounty

    if cleaned["is_urgent"]:
        limit = int(_cfg("DOUBT_URGENT_PER_DAY", 5))
        since = timezone.now() - timedelta(days=1)
        used = PostDoubt.objects.filter(post__user=user, is_urgent=True, created_at__gte=since).count()
        if used >= limit:
            raise serializers.ValidationError({
                "doubt_settings": f"Ek din me sirf {limit} urgent doubts post ho sakte hain."})
    return cleaned


def validate_question_photos(files):
    """A doubt post carries question PHOTOS only (cropped on the phone), never a video / document."""
    if len(files) > MAX_QUESTION_PHOTOS:
        raise serializers.ValidationError({"media_files": f"Doubt me max {MAX_QUESTION_PHOTOS} photos ho sakti hain."})
    for f in files:
        if _ext(f) not in QUESTION_PHOTO_EXTENSIONS:
            raise serializers.ValidationError({"media_files": "Doubt me sirf photo (jpg / png / webp) lag sakti hai."})


def _hold_bounty(post, amount):
    """Take `amount` coins from the asker. True when held; False (logged) when the wallet could not be charged -
    the doubt is still posted, just without a bounty (nothing was taken)."""
    from user_profile.models import CoinLedger, CoinLedgerBusy

    try:
        CoinLedger.objects.record_transaction(
            user=post.user, transaction_type=CoinLedger.TransactionType.SPEND, amount=-amount,
            reference=f"doubt_bounty_hold:{post.pk}", description="Doubt bounty (held until a best answer is chosen)",
            metadata={"post_id": str(post.pk), "kind": "doubt_bounty_hold"},
        )
        return True
    except (ValueError, CoinLedgerBusy):
        logger.warning("doubt bounty hold failed for post %s (%s coins)", post.pk, amount, exc_info=True)
        return False


def create_doubt(post, cleaned=None):
    """Create the PostDoubt row of a new doubt post and hold its bounty. Call inside the creating transaction."""
    cleaned = cleaned or {}
    info = PostDoubt.objects.create(
        post=post, subject=cleaned.get("subject", ""), class_level=cleaned.get("class_level", ""),
        exam=cleaned.get("exam", ""), chapter=cleaned.get("chapter", ""),
        is_urgent=bool(cleaned.get("is_urgent")), ocr_text=cleaned.get("ocr_text", ""),
    )
    amount = int(cleaned.get("bounty_coins") or 0)
    if amount and _hold_bounty(post, amount):
        info.bounty_coins = amount
        info.bounty_status = PostDoubt.BOUNTY_HELD
        info.save(update_fields=["bounty_coins", "bounty_status"])
    return info


def doubt_payload(post, best_exists=None):
    """The `doubt` block of a post card / detail (None for a post that is not a doubt).
    `best_exists`: pass the already-known "has a pinned best answer" to avoid a query for legacy rows."""
    if getattr(post, "post_type", "") != "doubt":
        return None
    try:
        info = post.doubt_info
    except PostDoubt.DoesNotExist:
        info = None
    if best_exists is None:
        best_exists = False
    solved_at = info.solved_at if info else None
    return {
        "subject": info.subject if info else "",
        "class_level": info.class_level if info else "",
        "exam": info.exam if info else "",
        "chapter": info.chapter if info else "",
        "is_urgent": bool(info and info.is_urgent),
        # `solved_at` is set when the asker pins a best answer; doubts pinned before the upgrade have no row / no
        # timestamp, so a pinned answer alone also means "solved".
        "is_solved": bool(solved_at or best_exists),
        "solved_at": solved_at,
        "bounty_coins": info.bounty_coins if info else 0,
        "bounty_status": info.bounty_status if info else PostDoubt.BOUNTY_NONE,
    }


# ---------------------------------------------------------------------------
# Best answer -> solved -> bounty
# ---------------------------------------------------------------------------
class BestAnswerLocked(Exception):
    """The bounty was already paid for this doubt: the best answer can not be changed any more."""


def _lock_info(post):
    """The PostDoubt row of `post`, locked. Doubts that predate the upgrade get an empty row on first use."""
    info = PostDoubt.objects.select_for_update().filter(post_id=post.pk).first()
    if info is None:
        try:
            with transaction.atomic():
                PostDoubt.objects.create(post=post)
        except IntegrityError:
            pass  # a concurrent request created it
        info = PostDoubt.objects.select_for_update().get(post_id=post.pk)
    return info


def _pay_bounty(info, post, answerer):
    from user_profile.models import CoinLedger

    CoinLedger.objects.record_transaction(
        user=answerer, transaction_type=CoinLedger.TransactionType.DOUBT_BOUNTY, amount=info.bounty_coins,
        reference=f"doubt_bounty_pay:{post.pk}", description="Doubt bounty - best answer",
        metadata={"post_id": str(post.pk), "kind": "doubt_bounty_pay"},
    )
    info.bounty_status = PostDoubt.BOUNTY_PAID
    info.bounty_paid_to = answerer


def _refund_bounty(info, post, why=""):
    from user_profile.models import CoinLedger

    CoinLedger.objects.record_transaction(
        user=post.user, transaction_type=CoinLedger.TransactionType.REFUND, amount=info.bounty_coins,
        reference=f"doubt_bounty_refund:{post.pk}", description=f"Doubt bounty refunded{(' - ' + why) if why else ''}",
        metadata={"post_id": str(post.pk), "kind": "doubt_bounty_refund", "why": why},
    )
    info.bounty_status = PostDoubt.BOUNTY_REFUNDED


def toggle_best_answer(answer):
    """Pin `answer` as the best answer (or unpin it when it already is). The caller checked that the requester is the
    asker. Returns {"state": "pinned"|"unpinned", "is_solved", "bounty_paid", "bounty_refunded", "bounty_coins"}.
    Raises BestAnswerLocked (bounty already paid) and user_profile.models.CoinLedgerBusy (wallet lock timeout; nothing
    was changed - the view answers 503)."""
    post = answer.post
    paid = refunded = False
    with transaction.atomic():
        info = _lock_info(post)
        answer = PostAnswer.objects.select_for_update().select_related("user").get(pk=answer.pk)
        if info.bounty_status == PostDoubt.BOUNTY_PAID:
            raise BestAnswerLocked("Bounty pay ho chuki hai, ab best answer badla nahi ja sakta.")

        if answer.is_best_answer:
            answer.is_best_answer = False
            answer.save(update_fields=["is_best_answer", "updated_at"])
            info.solved_at = None
            info.save(update_fields=["solved_at"])
            state = "unpinned"
        else:
            PostAnswer.objects.filter(post=post, is_best_answer=True).update(is_best_answer=False)
            answer.is_best_answer = True
            answer.save(update_fields=["is_best_answer", "updated_at"])
            info.solved_at = timezone.now()
            if info.bounty_status == PostDoubt.BOUNTY_HELD:
                if answer.user_id == post.user_id:
                    _refund_bounty(info, post, "asker chose their own answer")  # nobody to pay: coins go back
                    refunded = True
                else:
                    _pay_bounty(info, post, answer.user)
                    paid = True
            info.save()
            state = "pinned"
        coins = info.bounty_coins
        solved = info.solved_at is not None
    return {"state": state, "is_solved": solved, "bounty_paid": paid, "bounty_refunded": refunded,
            "bounty_coins": coins, "answer": answer}


def refund_stale_bounties(now=None, batch_size=100):
    """Celery beat (hourly): give a held bounty back to the asker when the doubt got no best answer within
    DOUBT_BOUNTY_EXPIRY_DAYS, or the post was deleted / rejected. Returns how many were refunded."""
    from user_profile.models import CoinLedgerBusy

    now = now or timezone.now()
    cutoff = now - timedelta(days=int(_cfg("DOUBT_BOUNTY_EXPIRY_DAYS", 14)))
    due = list(
        PostDoubt.objects.filter(bounty_status=PostDoubt.BOUNTY_HELD, solved_at__isnull=True)
        .filter(Q(post__is_deleted=True) | Q(post__moderation_status="rejected")
                | Q(post__is_scheduled=False, post__created_at__lt=cutoff))
        .values_list("pk", flat=True)[:batch_size]
    )
    refunded = 0
    for pk in due:
        try:
            with transaction.atomic():
                info = PostDoubt.objects.select_for_update().select_related("post__user").get(pk=pk)
                if info.bounty_status != PostDoubt.BOUNTY_HELD or info.solved_at is not None:
                    continue  # settled meanwhile
                post = info.post
                gone = post.is_deleted or post.moderation_status == "rejected"
                _refund_bounty(info, post, "post removed" if gone else "no best answer in time")
                info.save(update_fields=["bounty_status"])
            refunded += 1
            _notify_refund(post, info)
        except CoinLedgerBusy:
            logger.warning("refund_stale_bounties: wallet busy for doubt %s, retried next run", pk)
        except Exception:
            logger.exception("refund_stale_bounties: failed for doubt %s", pk)
    return refunded


# ---------------------------------------------------------------------------
# Notifications (never raise)
# ---------------------------------------------------------------------------
def _notify(recipient, notif_type_name, title, message, data, actor=None):
    try:
        from core.models import Notification
        from core.services import create_notification

        create_notification(
            recipient, getattr(Notification.NotifType, notif_type_name), title, message, data=data, actor=actor)
    except Exception:
        logger.exception("doubt notification %s failed", notif_type_name)


def notify_best_answer(post, answer, result):
    """Tell the answerer their answer was chosen (and that the bounty is theirs)."""
    if answer.user_id == post.user_id:
        return
    coins = result.get("bounty_coins") or 0
    title = "Your answer was marked best"
    message = f"+{coins} coins bounty mil gayi." if result.get("bounty_paid") and coins else "Doubt solve ho gaya."
    _notify(answer.user, "GENERIC", title, message,
            {"post_id": str(post.pk), "answer_id": str(answer.pk), "kind": "doubt_best_answer", "bounty_coins": coins},
            actor=post.user)


def _notify_refund(post, info):
    _notify(post.user, "GENERIC", "Doubt bounty refunded", f"{info.bounty_coins} coins wapas aa gaye.",
            {"post_id": str(post.pk), "kind": "doubt_bounty_refund", "bounty_coins": info.bounty_coins})


# ---------------------------------------------------------------------------
# Teachers of the subject
# ---------------------------------------------------------------------------
def _subjects_match(wanted, have):
    a, b = _norm(wanted), _norm(have)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and short in long_


def matching_teacher_ids(post, info, limit):
    """User ids of the teachers a doubt of `info.subject` should reach, best matches first, at most `limit`.
      1. staff with an APPROVED campus subject-teacher assignment for that subject (their school approved them);
      2. verified teachers (onboarding role teacher + verified flag) who listed the subject in their study profile.
    Never the asker, never someone in a block relationship with the asker."""
    if not _norm(info.subject):
        return []
    from user_profile.services import blocked_user_ids, teacher_verified_field

    excluded = set(blocked_user_ids(post.user_id)) | {post.user_id}
    ordered = []

    def _add(ids):
        for uid in ids:
            if uid not in excluded and uid not in ordered:
                ordered.append(uid)

    try:
        from campus.models import SubjectTeacherassigments

        rows = (
            SubjectTeacherassigments.objects
            .filter(status="approved", is_deleted=False, subject__name__iexact=info.subject.strip(),
                    staff__is_active=True, staff__user__is_active=True)
            .values_list("staff__user_id", flat=True).distinct()[:500]
        )
        _add(rows)
    except Exception:
        logger.warning("doubt teachers: campus lookup failed", exc_info=True)

    try:
        from user_profile.models import UserPreference

        verified = User.objects.filter(is_active=True, onboarding_role="teacher", **{teacher_verified_field(): True})
        prefs = (
            UserPreference.objects.filter(user__in=verified)
            .exclude(focus_subjects=[]).values_list("user_id", "focus_subjects")[:5000]
        )
        _add(uid for uid, subjects in prefs
             if any(_subjects_match(info.subject, s) for s in (subjects or []) if isinstance(s, str)))
    except Exception:
        logger.warning("doubt teachers: study-profile lookup failed", exc_info=True)
    return ordered[:limit]


def notify_teachers_for_doubt(post_id):
    """Tell the matching teachers about a new doubt, once (claimed with `teachers_notified_at`). Returns how many.
    Runs from post.tasks.notify_teachers_new_doubt once the post is visible: approved, not scheduled, public."""
    post = Post.objects.select_related("user", "doubt_info").filter(pk=post_id).first()
    if (post is None or post.post_type != "doubt" or post.is_deleted or post.is_scheduled or post.is_archived
            or post.moderation_status != "approved" or post.visibility != "public"):
        return 0
    info = getattr(post, "doubt_info", None)
    if info is None or not info.subject:
        return 0
    claimed = PostDoubt.objects.filter(pk=info.pk, teachers_notified_at__isnull=True).update(
        teachers_notified_at=timezone.now())
    if not claimed:
        return 0

    ids = matching_teacher_ids(post, info, int(_cfg("DOUBT_TEACHER_NOTIFY_MAX", 50)))
    if not ids:
        return 0
    title = f"{'Urgent doubt' if info.is_urgent else 'New doubt'} - {info.subject}"
    snippet = _clean_text(post.content, 110) or "Question photo ke saath doubt"
    if info.bounty_status == PostDoubt.BOUNTY_HELD and info.bounty_coins:
        snippet += f"  ({info.bounty_coins} coins bounty)"
    data = {"post_id": str(post.pk), "subject": info.subject, "is_urgent": info.is_urgent,
            "bounty_coins": info.bounty_coins if info.bounty_status == PostDoubt.BOUNTY_HELD else 0}
    for uid in ids:
        _notify(uid, "FEED_DOUBT_FOR_TEACHER", title, snippet, data, actor=post.user)
    return len(ids)


# ---------------------------------------------------------------------------
# Similar doubts (stop duplicates)
# ---------------------------------------------------------------------------
def normalize_query(raw):
    return _WS_RE.sub(" ", raw or "").strip()[:SIMILAR_MAX_CHARS]


def clamp_limit(raw):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return SIMILAR_DEFAULT_LIMIT
    return max(1, min(value, SIMILAR_MAX_LIMIT))


def find_similar(user, query, subject="", limit=SIMILAR_DEFAULT_LIMIT, request=None):
    """Existing, visible doubts that look like `query` (question text and / or OCR text), best first.
    Candidates are narrowed in SQL by keyword, then scored in Python (works the same on SQLite / Postgres and for
    Hindi text). Solved doubts get a boost: they already contain the answer."""
    q_tokens = _tokens(query)
    if not q_tokens:
        return []
    keywords = sorted(q_tokens, key=len, reverse=True)[:MAX_KEYWORDS]
    match = Q()
    for kw in keywords:
        match |= Q(content__icontains=kw) | Q(title__icontains=kw) | Q(doubt_info__ocr_text__icontains=kw)

    qs = Post.objects.filter(
        post_type="doubt", is_deleted=False, is_scheduled=False, is_archived=False,
        moderation_status="approved", visibility="public",
    ).filter(match)
    subject = _clean_text(subject, 80)
    if subject:
        qs = qs.filter(doubt_info__subject__iexact=subject)
    if user is not None and getattr(user, "is_authenticated", False):
        from user_profile.services import blocked_user_ids

        blocked = blocked_user_ids(user)
        if blocked:
            qs = qs.exclude(user_id__in=blocked)
    candidates = list(qs.select_related("doubt_info").prefetch_related("media").order_by("-created_at")[:SIMILAR_CANDIDATES])
    if not candidates:
        return []

    best_ids = {
        a.post_id: a for a in PostAnswer.objects.filter(
            post_id__in=[p.pk for p in candidates], is_best_answer=True)
    }
    needed = 1 if len(q_tokens) == 1 else 2
    scored = []
    for post in candidates:
        info = getattr(post, "doubt_info", None)
        text = " ".join(filter(None, [post.title, post.content, info.ocr_text if info else ""]))
        d_tokens = _tokens(text)
        shared = len(q_tokens & d_tokens)
        if shared < needed:
            continue
        coverage = shared / len(q_tokens)
        if coverage < SIMILAR_MIN_COVERAGE:
            continue
        score = round(0.7 * coverage + 0.3 * (shared / len(q_tokens | d_tokens)), 3)
        solved = bool((info and info.solved_at) or post.pk in best_ids)
        rank = score + (SOLVED_BOOST if solved else 0.0)
        scored.append((rank, post.answers_count, post.created_at, post, score, solved))
    scored.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)

    out = []
    for _rank, _answers, _created, post, score, solved in scored[:limit]:
        info = getattr(post, "doubt_info", None)
        photo = None
        media = sorted(post.media.all(), key=lambda m: m.display_order)
        if media and media[0].media_type in ("image", "gif"):
            photo = _file_url(media[0].thumb_320 or media[0].file, request)
        best = best_ids.get(post.pk)
        out.append({
            "id": str(post.pk),
            "content": _clean_text(post.content, 200),
            "subject": info.subject if info else "",
            "class_level": info.class_level if info else "",
            "chapter": info.chapter if info else "",
            "is_urgent": bool(info and info.is_urgent),
            "is_solved": solved,
            "answers_count": post.answers_count,
            "photo": photo,
            "best_answer": {"id": str(best.pk), "content": _clean_text(best.content, 160)} if best else None,
            "is_mine": bool(user is not None and post.user_id == getattr(user, "pk", None)),
            "score": score,
            "created_at": post.created_at,
        })
    return out


# ---------------------------------------------------------------------------
# Answers: text + step-wise solution + pictures + one short video
# ---------------------------------------------------------------------------
def _check_image(upload, label):
    if _ext(upload) not in IMAGE_EXTENSIONS:
        raise serializers.ValidationError({"images": f"{label}: sirf jpg / png / webp photo allowed hai."})
    limit_mb = int(_cfg("DOUBT_ANSWER_IMAGE_MAX_MB", 10))
    if upload.size > limit_mb * 1024 * 1024:
        raise serializers.ValidationError({"images": f"{label}: photo {limit_mb}MB se badi nahi ho sakti."})


def _parse_steps(raw):
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise serializers.ValidationError({"steps": "steps valid JSON list hona chahiye."})
    if not isinstance(raw, list):
        raise serializers.ValidationError({"steps": "steps valid JSON list hona chahiye."})
    return raw


def clean_answer_input(data, files):
    """Validate the body of POST /post/<id>/answers/ (JSON or multipart).
       content        text (optional when steps / pictures / video are sent)
       steps          JSON list [{"text": "..."}, ...] (max 20); a step's picture is the file `step_image_<index>`
       answer_images  up to 4 pictures      answer_video  one short video (mp4 / mov / webm)
    Returns {"content", "steps": [{"text", "image"}], "images": [...], "video": file|None}."""
    content = _clean_answer_text(data.get("content"), MAX_ANSWER_CONTENT, "content")
    raw_steps = _parse_steps(data.get("steps"))
    if len(raw_steps) > PostAnswerStep.MAX_STEPS:
        raise serializers.ValidationError({"steps": f"Max {PostAnswerStep.MAX_STEPS} steps allowed hain."})

    steps = []
    for idx, item in enumerate(raw_steps):
        text = item.get("text") if isinstance(item, dict) else item
        text = _clean_answer_text(text, PostAnswerStep.MAX_TEXT, "steps", label=f"Step {idx + 1}")
        image = files.get(f"step_image_{idx}") if files is not None else None
        if image is not None:
            _check_image(image, f"Step {idx + 1}")
        if not text and image is None:
            raise serializers.ValidationError({"steps": f"Step {idx + 1} khaali hai (text ya photo do)."})
        steps.append({"text": text, "image": image})

    images = list(files.getlist("answer_images")) if files is not None else []
    if len(images) > PostAnswerAttachment.MAX_IMAGES:
        raise serializers.ValidationError({"images": f"Max {PostAnswerAttachment.MAX_IMAGES} photos allowed hain."})
    for n, image in enumerate(images, 1):
        _check_image(image, f"Photo {n}")

    videos = list(files.getlist("answer_video")) if files is not None else []
    if len(videos) > PostAnswerAttachment.MAX_VIDEOS:
        raise serializers.ValidationError({"video": "Ek answer me sirf ek video lag sakta hai."})
    video = videos[0] if videos else None
    if video is not None:
        if _ext(video) not in VIDEO_EXTENSIONS:
            raise serializers.ValidationError({"video": "Video mp4 / mov / webm hona chahiye."})
        limit_mb = int(_cfg("DOUBT_ANSWER_VIDEO_MAX_MB", 50))
        if video.size > limit_mb * 1024 * 1024:
            raise serializers.ValidationError({"video": f"Video {limit_mb}MB se bada nahi ho sakta."})

    if not (content or steps or images or video):
        raise serializers.ValidationError({"content": "Answer khaali nahi ho sakta (text, steps, photo ya video do)."})

    try:
        from common.moderation import screen_text

        for text in [content] + [s["text"] for s in steps]:
            flagged, reason = screen_text(text, "post")
            if flagged and reason == "profanity":
                raise serializers.ValidationError({"content": "Answer me abusive language allowed nahi hai."})
    except serializers.ValidationError:
        raise
    except Exception:  # noqa: BLE001 - the text screen is best effort, never a reason to refuse an answer
        logger.warning("doubt answer text screen failed", exc_info=True)
    return {"content": content, "steps": steps, "images": images, "video": video}


def _clean_answer_text(value, limit, field, label=""):
    """H23 - answer text goes through the same sanitiser as a caption (post/rich_text.py): HTML / script / javascript:
    links, control + bidi-override characters out, NFC, blank-line cap, link / mention / formula caps. Line breaks stay
    (a step-wise solution is written in lines). Over-long text is REFUSED (never silently cut mid-formula)."""
    from .rich_text import clean_content

    if value is None or not str(value).strip():
        return ""
    try:
        return clean_content(value, max_length=limit)
    except serializers.ValidationError as exc:
        from .rich_text import validation_message

        msg = validation_message(exc)
        raise serializers.ValidationError({field: f"{label}: {msg}" if label else msg})


def _clean_text_keep_lines(value, limit):
    """Like _clean_text but keeps line breaks (a step-wise solution is written in lines)."""
    if value is None:
        return ""
    text = _CTRL_RE.sub("", str(value)).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def check_answer_quota(post, user):
    limit = int(_cfg("DOUBT_MAX_ANSWERS_PER_USER", 5))
    if PostAnswer.objects.filter(post=post, user=user).count() >= limit:
        raise serializers.ValidationError(
            {"detail": f"Ek doubt par aap max {limit} answers de sakte hain."})


def create_answer(post, user, cleaned):
    """Save the answer with its steps and attachments in one transaction."""
    with transaction.atomic():
        answer = PostAnswer.objects.create(post=post, user=user, content=cleaned["content"])
        for position, step in enumerate(cleaned["steps"]):
            PostAnswerStep.objects.create(answer=answer, position=position, text=step["text"], image=step["image"])
        position = 0
        for image in cleaned["images"]:
            PostAnswerAttachment.objects.create(
                answer=answer, kind=PostAnswerAttachment.KIND_IMAGE, file=image, position=position,
                file_size_bytes=image.size)
            position += 1
        if cleaned["video"] is not None:
            PostAnswerAttachment.objects.create(
                answer=answer, kind=PostAnswerAttachment.KIND_VIDEO, file=cleaned["video"], position=position,
                file_size_bytes=cleaned["video"].size)
    return answer


def answer_preview_text(answer):
    """One line for a notification: the text, else the first step, else what kind of answer it is."""
    if answer.content:
        return answer.content[:200]
    first = answer.steps.all()[:1]
    for step in first:
        if step.text:
            return step.text[:200]
    return "Step-wise solution / photo / video ke saath answer"
