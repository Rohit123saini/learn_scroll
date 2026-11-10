"""
S5 -- DPDP Act 2023 data rights.

  1. "Download my data"   -> request_export() queues a job, build_export() writes a ZIP into the default storage,
                             the app downloads it through an authenticated endpoint until `expires_at` (7 days).
  2. "Delete my account"  -> schedule_deletion() starts a grace period (30 days, cancel_deletion() undoes it);
                             run_due_deletions() (Celery beat, daily) runs the real erase in login/account_deletion.py.
  3. Consent history      -> record_consent() appends a ConsentRecord; withdrawing "marketing" also switches off the
                             marketing-like channels (SMS / WhatsApp / content digest) the person had on.

Settings (all optional):
  DATA_EXPORT_EXPIRY_DAYS = 7          DATA_EXPORT_MIN_INTERVAL_HOURS = 24   (one new request per day)
  DATA_EXPORT_MAX_ROWS = 50000         (per section, so one account cannot build an endless ZIP)
  ACCOUNT_DELETION_GRACE_DAYS = 30     (0 = delete at once, the old T0.8 behaviour)
  ACCOUNT_DELETION_REMINDER_DAYS = 3   (one "3 days left" e-mail)
  PRIVACY_NOTICE_VERSION = "2026-10"   (stamped on every consent row)

What goes into the ZIP: the person's own profile, posts, comments, stories, saved posts, likes, follow lists
(usernames only), messages THEY sent (never other people's), coin ledger, preferences, certificates, consent history,
study activity (streak, time spent per day, weekly recaps, badges, test attempts, class attendance),
security activity (sign-in history without IP / device identifiers beyond platform). Media FILES are listed by name /
URL, not copied (they are already on the person's phone or reachable in the app) -- README.txt says so. A section that
fails is skipped and named in `manifest.json` ("skipped_sections") so one bad table never blocks the whole export.

Written by reading the code only -- nothing was compiled or run.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import io
import json
import logging
import uuid
import zipfile
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AccountDeletionRequest, ConsentRecord, DataExportRequest

logger = logging.getLogger(__name__)


# ───────────────────────── settings helpers ─────────────────────────

def export_expiry_days() -> int:
    return max(1, int(getattr(settings, "DATA_EXPORT_EXPIRY_DAYS", 7)))


def export_min_interval() -> timedelta:
    return timedelta(hours=max(0, int(getattr(settings, "DATA_EXPORT_MIN_INTERVAL_HOURS", 24))))


def export_max_rows() -> int:
    return max(100, int(getattr(settings, "DATA_EXPORT_MAX_ROWS", 50000)))


def deletion_grace_days() -> int:
    return max(0, int(getattr(settings, "ACCOUNT_DELETION_GRACE_DAYS", 30)))


def deletion_reminder_days() -> int:
    return max(0, int(getattr(settings, "ACCOUNT_DELETION_REMINDER_DAYS", 3)))


def notice_version() -> str:
    return str(getattr(settings, "PRIVACY_NOTICE_VERSION", "2026-10"))


# ═════════════════════════ 1. DATA EXPORT ═════════════════════════

class ExportRefused(Exception):
    """A new export cannot be queued right now. `.code` is a stable machine code, `.retry_after` seconds (or None)."""

    def __init__(self, code: str, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


def _expire_overdue(user) -> None:
    """Mark READY exports past their expiry as expired and drop their files (cheap, per-user)."""
    now = timezone.now()
    for exp in DataExportRequest.objects.filter(user=user, status=DataExportRequest.STATUS_READY, expires_at__lt=now):
        expire_export(exp)


def expire_export(exp: DataExportRequest) -> None:
    if exp.file_name:
        try:
            default_storage.delete(exp.file_name)
        except Exception:  # pragma: no cover - storage hiccup must not block the sweep
            logger.exception("data export: could not delete %s", exp.file_name)
    exp.file_name = ""
    exp.status = DataExportRequest.STATUS_EXPIRED
    exp.save(update_fields=["file_name", "status"])


def request_export(user) -> DataExportRequest:
    """Queue one export. Refuses while one is running, and more than one per DATA_EXPORT_MIN_INTERVAL_HOURS."""
    _expire_overdue(user)
    active = DataExportRequest.objects.filter(
        user=user, status__in=[DataExportRequest.STATUS_QUEUED, DataExportRequest.STATUS_PROCESSING]
    ).first()
    if active is not None:
        raise ExportRefused("export_in_progress", "Your data export is already being prepared.")
    interval = export_min_interval()
    last = (
        DataExportRequest.objects.filter(user=user)
        .exclude(status=DataExportRequest.STATUS_FAILED)  # a failed run does not count against the person
        .order_by("-requested_at")
        .first()
    )
    if last is not None and interval.total_seconds() > 0:
        wait = last.requested_at + interval - timezone.now()
        if wait.total_seconds() > 0:
            raise ExportRefused(
                "export_too_soon", "You can request a new export once a day.", retry_after=int(wait.total_seconds()) + 1
            )
    exp = DataExportRequest.objects.create(user=user)
    return exp


def latest_export(user) -> DataExportRequest | None:
    _expire_overdue(user)
    return DataExportRequest.objects.filter(user=user).order_by("-requested_at", "-id").first()


def next_export_allowed_at(user):
    """When the person may ask for a NEW export (None = now). A failed run does not count against them."""
    interval = export_min_interval()
    if interval.total_seconds() <= 0:
        return None
    last = (
        DataExportRequest.objects.filter(user=user)
        .exclude(status=DataExportRequest.STATUS_FAILED)
        .order_by("-requested_at")
        .first()
    )
    if last is None:
        return None
    when = last.requested_at + interval
    return when if when > timezone.now() else None


def export_payload(exp: DataExportRequest | None) -> dict | None:
    """One export as the API shows it (no storage key, no file path)."""
    if exp is None:
        return None
    ready = exp.status == DataExportRequest.STATUS_READY and bool(exp.file_name)
    return {
        "id": exp.pk,
        "status": exp.status,
        "requested_at": exp.requested_at.isoformat() if exp.requested_at else None,
        "ready_at": exp.ready_at.isoformat() if exp.ready_at else None,
        "expires_at": exp.expires_at.isoformat() if exp.expires_at else None,
        "size_bytes": exp.size_bytes,
        "download_count": exp.download_count,
        "can_download": ready,
        "download_path": f"/login/auth/data/export/{exp.pk}/download/" if ready else None,
        "failed": exp.status == DataExportRequest.STATUS_FAILED,
    }


# ---- serialising rows -------------------------------------------------------------------------------------------

def _plain(value):
    """JSON-safe value for any Django field value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        return value.total_seconds()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, bytes):
        return None  # never dump binary
    name = getattr(value, "name", None)  # FieldFile
    if isinstance(name, str):
        return name
    return str(value)


def _row(obj, *, skip=()) -> dict:
    out = {}
    for f in obj._meta.concrete_fields:
        if f.name in skip:
            continue
        if f.is_relation and (f.many_to_one or f.one_to_one):
            out[f.name + "_id"] = _plain(getattr(obj, f.attname, None))
        else:
            out[f.name] = _plain(getattr(obj, f.attname if f.is_relation else f.name, None))
    return out


def _newest(qs):
    """Newest first. Several of these tables use a UUID primary key (random order), so prefer `created_at`."""
    names = {f.name for f in qs.model._meta.concrete_fields}
    for name in ("created_at", "created", "sent_at", "generated_at", "marked_at", "timestamp"):
        if name in names:
            return qs.order_by("-" + name)
    return qs.order_by("-pk")


def _rows(qs, *, skip=()) -> list[dict]:
    return [_row(o, skip=skip) for o in list(_newest(qs)[: export_max_rows()])]


PROFILE_FIELDS = (
    "username", "email", "phone", "phone_verified", "first_name", "last_name", "bio", "pronouns", "category_label",
    "links", "location", "verified_reason", "username_changed_at", "is_private", "study_class", "target_exam", "onboarding_role", "date_of_birth", "show_birthday", "is_verified",
    "followers_count", "following_count", "posts_count", "coin", "date_joined", "last_login",
)


def _section_profile(user):
    data = {k: _plain(getattr(user, k, None)) for k in PROFILE_FIELDS}
    data["profile_photo"] = _plain(getattr(user, "profile_photo", None))
    data["cover_photo"] = _plain(getattr(user, "cover_photo", None))  # P1
    return data


def _section_preferences(user):
    out = {}
    try:
        from user_profile.models import UserPreference

        p = UserPreference.objects.filter(user=user).first()
        if p is not None:
            out["app_preferences"] = _row(p)
    except Exception:
        logger.exception("export: preferences")
        raise
    try:
        from user_profile.models import PrivacySettings

        p = PrivacySettings.objects.filter(user=user).first()
        if p is not None:
            out["privacy_settings"] = _row(p)
    except Exception:
        logger.exception("export: privacy settings")
        raise
    try:
        from core.models import NotificationPreference

        p = NotificationPreference.objects.filter(user=user).first()
        if p is not None:
            out["notification_preferences"] = _row(p)
    except Exception:
        logger.exception("export: notification preferences")
        raise
    return out


def _section_posts(user):
    from post.models import Post

    return _rows(Post.objects.filter(user=user, is_deleted=False))


def _section_comments(user):
    from post.models import PostComment

    return _rows(PostComment.objects.filter(user=user, is_deleted=False))


def _section_stories(user):
    from post.models import Story

    return _rows(Story.objects.filter(user=user))


def _section_saved(user):
    from post.models import PostSave

    return _rows(PostSave.objects.filter(user=user))


def _section_likes(user):
    from post.models import PostLike

    return _rows(PostLike.objects.filter(user=user))


def _section_follows(user):
    from user_profile.models import Follow

    def names(qs, field):
        return [
            {"username": getattr(r, field).username, "status": r.status}
            for r in qs.select_related(field)[: export_max_rows()]
        ]

    return {
        "following": names(Follow.objects.filter(follower=user), "following"),
        "followers": names(Follow.objects.filter(following=user), "follower"),
    }


def _section_messages(user):
    # Only what THIS person wrote. Other people's messages are theirs.
    from message.models import Message

    qs = Message.objects.filter(sender=user, is_system_message=False)
    return _rows(qs, skip=("meta",))


def _section_coins(user):
    from user_profile.models import CoinLedger

    return _rows(CoinLedger.objects.filter(user=user))


def _section_certificates(user):
    out = {}
    try:
        from tuitionclass.models import Certificate

        out["class_certificates"] = _rows(Certificate.objects.filter(student=user))
    except Exception:
        logger.exception("export: class certificates")
        raise
    try:
        from testseries.models import TestCertificate

        out["test_certificates"] = _rows(TestCertificate.objects.filter(student=user))
    except Exception:
        logger.exception("export: test certificates")
        raise
    return out


def _section_consents(user):
    return _rows(ConsentRecord.objects.filter(user=user).order_by("-created_at", "-id"))


def _section_security(user):
    from .models import AuthAuditLog

    rows = []
    for r in AuthAuditLog.objects.filter(user=user).order_by("-created_at", "-id")[: export_max_rows()]:
        rows.append(
            {
                "time": _plain(r.created_at), "event": r.event, "outcome": r.outcome,
                "platform": r.platform, "reason": r.reason,
            }
        )
    return rows


def _try_part(out: dict, key: str, fn) -> None:
    """One part of a multi-part file. A part that cannot be read is named in `unavailable` (never silently
    dropped, never fatal for the other parts)."""
    try:
        out[key] = fn()
    except Exception as e:
        logger.exception("export: study activity part %s failed", key)
        out.setdefault("unavailable", []).append({"part": key, "reason": type(e).__name__})


def _section_study_activity(user):
    """S5b -- the person's learning activity: streak, time in the app per day, weekly recaps, badges, test attempts
    and class attendance. Only THEIR rows (never a class-mate's score / attendance)."""

    def streak():
        from user_profile.models import Streak

        s = Streak.objects.filter(user=user).first()
        return _row(s) if s is not None else None

    def daily_usage():
        from user_profile.models import DailyUsage

        qs = DailyUsage.objects.filter(user=user).order_by("-date")[: export_max_rows()]
        return [{"date": _plain(r.date), "seconds": r.seconds, "minutes": round(r.seconds / 60, 1)} for r in qs]

    def weekly_recaps():
        from user_profile.models import WeeklyRecap

        return _rows(WeeklyRecap.objects.filter(user=user).order_by("-week_start"))

    def badges():
        from user_profile.models import UserBadge

        out = []
        for ub in UserBadge.objects.filter(user=user).select_related("badge").order_by("-earned_at")[: export_max_rows()]:
            out.append(
                {
                    "code": ub.badge.code, "title": ub.badge.title, "description": ub.badge.description,
                    "earned_at": _plain(ub.earned_at), "context": _plain(ub.context),
                }
            )
        return out

    def test_attempts():
        from testseries.models import TestAttempt

        # `draft_answers` is the autosave blob (big, and the same answers are on the graded responses) -> left out
        qs = TestAttempt.objects.filter(student=user).order_by("-started_at", "-submitted_at")[: export_max_rows()]
        return [_row(a, skip=("draft_answers",)) for a in qs]

    def class_attendance():
        from tuitionclass.models import SessionAttendance

        return _rows(SessionAttendance.objects.filter(student=user).order_by("-marked_at"))

    out: dict = {}
    _try_part(out, "streak", streak)
    _try_part(out, "daily_usage", daily_usage)
    _try_part(out, "weekly_recaps", weekly_recaps)
    _try_part(out, "badges", badges)
    _try_part(out, "test_attempts", test_attempts)
    _try_part(out, "class_attendance", class_attendance)
    return out


# (file name inside the ZIP, builder)
SECTIONS = (
    ("profile.json", _section_profile),
    ("preferences.json", _section_preferences),
    ("posts.json", _section_posts),
    ("comments.json", _section_comments),
    ("stories.json", _section_stories),
    ("saved_posts.json", _section_saved),
    ("likes.json", _section_likes),
    ("follows.json", _section_follows),
    ("messages_sent.json", _section_messages),
    ("coin_ledger.json", _section_coins),
    ("certificates.json", _section_certificates),
    ("study_activity.json", _section_study_activity),
    ("consent_history.json", _section_consents),
    ("security_activity.json", _section_security),
)

README = """LearnScroll -- your data export
Generated: {when}
Account: @{username}

This ZIP holds the personal data LearnScroll keeps about you: profile, preferences, posts, comments, stories,
saved posts, likes, who you follow / who follows you (usernames only), the messages YOU sent (messages other
people wrote are theirs and are not included), coin history, certificates, your study activity (streak, time
spent in the app per day, weekly recaps, badges, test attempts, class attendance), your consent history and your
sign-in history.

Photos, videos and documents are listed by file name / link, they are not copied into this ZIP.
Each .json file opens in any text editor. This file expires on {expires} -- you can ask for a new one from
Settings > Data & privacy.

Questions or complaints: Settings > Data & privacy > Grievance officer.
"""


def build_export(exp: DataExportRequest) -> DataExportRequest:
    """Build the ZIP and mark the request ready. Safe to call again for a queued / failed request."""
    user = exp.user
    now = timezone.now()
    exp.status = DataExportRequest.STATUS_PROCESSING
    exp.started_at = now
    exp.error = ""
    exp.save(update_fields=["status", "started_at", "error"])

    skipped: list[dict] = []
    counts: dict[str, int] = {}
    buf = io.BytesIO()
    try:
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, builder in SECTIONS:
                try:
                    data = builder(user)
                except Exception as e:  # one bad table must not block the whole export
                    logger.exception("data export %s: section %s failed", exp.pk, name)
                    skipped.append({"file": name, "reason": type(e).__name__})
                    continue
                counts[name] = len(data) if hasattr(data, "__len__") else 1
                zf.writestr(name, json.dumps(data, ensure_ascii=False, indent=2))
            expires = now + timedelta(days=export_expiry_days())
            manifest = {
                "generated_at": now.isoformat(),
                "expires_at": expires.isoformat(),
                "notice_version": notice_version(),
                "row_counts": counts,
                "skipped_sections": skipped,
                "row_limit_per_section": export_max_rows(),
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            zf.writestr("README.txt", README.format(when=now.strftime("%d %b %Y %H:%M UTC"), username=user.username,
                                                    expires=expires.strftime("%d %b %Y")))
        payload = buf.getvalue()
        key = default_storage.save(f"data_exports/u{user.pk}/{uuid.uuid4().hex}.zip", ContentFile(payload))
    except Exception as e:
        logger.exception("data export %s failed", exp.pk)
        exp.status = DataExportRequest.STATUS_FAILED
        exp.error = f"{type(e).__name__}"[:255]
        exp.save(update_fields=["status", "error"])
        return exp

    exp.status = DataExportRequest.STATUS_READY
    exp.file_name = key
    exp.size_bytes = len(payload)
    exp.ready_at = timezone.now()
    exp.expires_at = exp.ready_at + timedelta(days=export_expiry_days())
    exp.save(update_fields=["status", "file_name", "size_bytes", "ready_at", "expires_at"])
    _notify_export_ready(exp)
    return exp


def _notify_export_ready(exp: DataExportRequest) -> None:
    """E-mail (only when the address exists AND is verified-or-login). No link: the file is behind the login."""
    user = exp.user
    email = getattr(user, "email", None)
    if not email:
        return
    try:
        send_mail(
            "Your LearnScroll data export is ready",
            "Your data export is ready. Open the app > Settings > Data & privacy > Download my data.\n"
            f"It stays available until {exp.expires_at:%d %b %Y}.\n\nIf you did not ask for this, change your "
            "password and contact the grievance officer in the app.",
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
            [email],
            fail_silently=True,
        )
        exp.notified_at = timezone.now()
        exp.save(update_fields=["notified_at"])
    except Exception:  # pragma: no cover
        logger.exception("data export: ready e-mail failed")


def open_export_file(exp: DataExportRequest):
    """File object for the ZIP, or None when it is gone / expired / not ready. Counts the download."""
    if exp.status != DataExportRequest.STATUS_READY or not exp.file_name:
        return None
    if exp.expires_at and exp.expires_at < timezone.now():
        expire_export(exp)
        return None
    try:
        f = default_storage.open(exp.file_name, "rb")
    except Exception:
        logger.exception("data export %s: file missing", exp.pk)
        return None
    DataExportRequest.objects.filter(pk=exp.pk).update(download_count=exp.download_count + 1)
    return f


def sweep_exports() -> dict:
    """Beat task body: expire READY exports past `expires_at` and fail exports stuck in processing for > 1 hour."""
    now = timezone.now()
    expired = 0
    for exp in DataExportRequest.objects.filter(status=DataExportRequest.STATUS_READY, expires_at__lt=now):
        expire_export(exp)
        expired += 1
    stuck = DataExportRequest.objects.filter(
        status=DataExportRequest.STATUS_PROCESSING, started_at__lt=now - timedelta(hours=1)
    ).update(status=DataExportRequest.STATUS_FAILED, error="timeout")
    return {"expired": expired, "stuck_failed": stuck}


def purge_user_exports(user) -> int:
    """Used when the account is erased: delete every export file of that person."""
    n = 0
    for exp in DataExportRequest.objects.filter(user=user).exclude(file_name=""):
        try:
            default_storage.delete(exp.file_name)
        except Exception:  # pragma: no cover
            logger.exception("purge export file failed")
        n += 1
    DataExportRequest.objects.filter(user=user).update(file_name="", status=DataExportRequest.STATUS_EXPIRED)
    return n


# ═════════════════════════ 2. DELETE WITH GRACE ═════════════════════════

def pending_deletion(user) -> AccountDeletionRequest | None:
    return AccountDeletionRequest.objects.filter(user=user, status=AccountDeletionRequest.STATUS_PENDING).first()


def schedule_deletion(user) -> AccountDeletionRequest:
    """Start (or return the already running) grace period. Grace 0 is handled by the caller (delete at once)."""
    existing = pending_deletion(user)
    if existing is not None:
        return existing
    when = timezone.now() + timedelta(days=deletion_grace_days())
    try:
        with transaction.atomic():
            return AccountDeletionRequest.objects.create(user=user, scheduled_for=when)
    except IntegrityError:  # two taps at once -> the unique "one pending per user" rule
        return pending_deletion(user)  # type: ignore[return-value]


def cancel_deletion(user) -> bool:
    n = AccountDeletionRequest.objects.filter(user=user, status=AccountDeletionRequest.STATUS_PENDING).update(
        status=AccountDeletionRequest.STATUS_CANCELLED, cancelled_at=timezone.now()
    )
    return n > 0


def notify_deletion_scheduled(req: AccountDeletionRequest) -> None:
    """One e-mail saying \"your account will be deleted on <date>; you can cancel any time before\"."""
    email = getattr(req.user, "email", None)
    if not email:
        return
    try:
        send_mail(
            "Your LearnScroll account is scheduled for deletion",
            f"We received your request to delete your LearnScroll account. It will be erased on "
            f"{req.scheduled_for:%d %b %Y}.\nUntil then your account still works, and you can change your mind: "
            "open the app > Settings > Data & privacy > Cancel deletion.\n\nIf you did not ask for this, sign in, "
            "cancel the deletion and change your password.",
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
            [email],
            fail_silently=True,
        )
    except Exception:  # pragma: no cover
        logger.exception("deletion scheduled e-mail failed")


def deletion_status(user) -> dict:
    req = pending_deletion(user)
    if req is None:
        return {"pending": False}
    left = req.scheduled_for - timezone.now()
    return {
        "pending": True,
        "requested_at": req.requested_at.isoformat(),
        "scheduled_for": req.scheduled_for.isoformat(),
        "days_left": max(0, left.days + (1 if left.seconds > 0 else 0)),
    }


# What is erased / kept: ONE text, shown in the app and returned by the API so they never drift apart.
def erasure_summary() -> dict:
    return {
        "erased": [
            "Your profile details (name, username, e-mail, phone, bio, photo, cover photo, location, date of birth)",
            "Your posts, stories, comments, likes and saved posts",
            "Your followers and following",
            "Your study activity (streak, time spent, weekly recaps, badges) and your app, privacy and notification settings",
            "Your sign-in sessions and push-notification devices",
        ],
        "kept": [
            "Payment, coin and withdrawal records (kept for accounting and tax law, with no personal identifiers)",
            "Messages you sent stay in other people's chats, shown under a deleted account (they are part of those conversations)",
            "Certificates, test attempts and class attendance already issued (linked only to the anonymised account)",
            "Moderation and copyright records, when a law or a legal claim needs them",
            "A sign-in security log with identifiers blanked (until its retention period ends)",
            "Your consent history (to prove what you agreed to and when)",
        ],
        "grace_days": deletion_grace_days(),
    }


def run_due_deletions(now=None, limit: int = 200) -> dict:
    """Beat task body (daily). Erases accounts whose grace ended; sends the "N days left" reminder once."""
    from .account_deletion import blocking_reasons, delete_account

    now = now or timezone.now()
    done = postponed = failed = reminded = 0
    due = AccountDeletionRequest.objects.filter(
        status=AccountDeletionRequest.STATUS_PENDING, scheduled_for__lte=now
    ).select_related("user")[:limit]
    for req in due:
        user = req.user
        try:
            if blocking_reasons(user):
                # money in flight (coin withdrawal): wait a day, never erase under a payout
                AccountDeletionRequest.objects.filter(pk=req.pk).update(scheduled_for=now + timedelta(days=1))
                postponed += 1
                continue
            purge_user_exports(user)
            delete_account(user)
            AccountDeletionRequest.objects.filter(pk=req.pk).update(
                status=AccountDeletionRequest.STATUS_COMPLETED, completed_at=timezone.now()
            )
            try:  # no request / identifier: the row only says "account X was erased when its grace period ended"
                from . import audit

                audit.record(audit.Event.ACCOUNT_DELETED, None, user=user, reason="grace_ended")
            except Exception:  # pragma: no cover - auditing never blocks an erase
                logger.exception("deletion audit row failed")
            done += 1
        except Exception:
            logger.exception("scheduled account deletion failed user_id=%s", req.user_id)
            failed += 1

    rem_days = deletion_reminder_days()
    if rem_days > 0:
        soon = AccountDeletionRequest.objects.filter(
            status=AccountDeletionRequest.STATUS_PENDING,
            scheduled_for__lte=now + timedelta(days=rem_days),
            scheduled_for__gt=now,
            reminder_sent_at__isnull=True,
        ).select_related("user")[:limit]
        for req in soon:
            email = getattr(req.user, "email", None)
            if email:
                try:
                    send_mail(
                        "Your LearnScroll account will be deleted soon",
                        f"Your account is scheduled for deletion on {req.scheduled_for:%d %b %Y}. "
                        "To keep it, open the app and cancel the deletion from Settings > Data & privacy.",
                        getattr(settings, "DEFAULT_FROM_EMAIL", None),
                        [email],
                        fail_silently=True,
                    )
                except Exception:  # pragma: no cover
                    logger.exception("deletion reminder e-mail failed")
            AccountDeletionRequest.objects.filter(pk=req.pk).update(reminder_sent_at=timezone.now())
            reminded += 1
    return {"deleted": done, "postponed": postponed, "failed": failed, "reminded": reminded}


# ═════════════════════════ 3. CONSENT ═════════════════════════

CONSENT_PURPOSES = (
    ConsentRecord.PURPOSE_MARKETING,
    ConsentRecord.PURPOSE_ANALYTICS,
    ConsentRecord.PURPOSE_PERSONALISATION,
)


# where a consent answer may say it came from (ConsentRecord.source is 20 chars): the Settings switch or the first-run card
CONSENT_SOURCES = ("app", "first_run_card")


def record_consent(user, purpose: str, granted: bool, *, source: str = "app") -> ConsentRecord:
    if purpose not in CONSENT_PURPOSES:
        raise ValueError("unknown consent purpose")
    rec = ConsentRecord.objects.create(
        user=user, purpose=purpose, granted=bool(granted), notice_version=notice_version(), source=source[:20]
    )
    if purpose == ConsentRecord.PURPOSE_MARKETING and not granted:
        _apply_marketing_withdrawal(user)
    if purpose == ConsentRecord.PURPOSE_ANALYTICS:
        try:  # X3 - a grant / withdrawal must count from the very next event (product analytics caches consent 10 min)
            from core import product_analytics

            product_analytics.invalidate_consent(user.pk)
        except Exception:  # pragma: no cover - the consent row is the legal record; the cache expires anyway
            logger.exception("analytics consent cache invalidation failed")
    return rec


def _apply_marketing_withdrawal(user) -> None:
    """Take the marketing-like channels off, for real: SMS, WhatsApp and the study digest."""
    try:
        from core.models import NotificationPreference

        NotificationPreference.objects.filter(user=user).update(
            sms_enabled=False, whatsapp_enabled=False, content_digest_frequency="off"
        )
    except Exception:  # pragma: no cover - the consent row is the legal record; the switch-off is best effort
        logger.exception("marketing withdrawal: could not update notification preferences")


def current_consents(user) -> list[dict]:
    """One row per purpose: `granted` is True / False, or None when the person was never asked."""
    latest: dict[str, ConsentRecord] = {}
    for rec in ConsentRecord.objects.filter(user=user).order_by("-created_at", "-id"):
        latest.setdefault(rec.purpose, rec)
    out = []
    for p in CONSENT_PURPOSES:
        rec = latest.get(p)
        out.append(
            {
                "purpose": p,
                "granted": None if rec is None else rec.granted,
                "since": None if rec is None else rec.created_at.isoformat(),
            }
        )
    return out


def consent_history(user, limit: int = 100) -> list[dict]:
    return [
        {
            "purpose": r.purpose, "granted": r.granted, "at": r.created_at.isoformat(),
            "notice_version": r.notice_version, "source": r.source,
        }
        for r in ConsentRecord.objects.filter(user=user).order_by("-created_at", "-id")[:limit]
    ]
