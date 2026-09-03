"""
liveclass/models.py

Online live-class platform.

App structure assumed:
    login/            -> custom User model (AbstractUser) already exists here
    liveclass/         -> this new app (classrooms, scheduling, passes, sessions)

Flow (open marketplace — ANY authenticated user can be a "teacher" for their
own classroom, and ANY authenticated user can be a "student" who buys a pass
for someone else's classroom. There is no separate teacher/student role on
the User model; a person is a teacher only in the context of the Classroom
they created):
    Any user creates a Classroom (becomes its teacher)
      -> defines one or more ClassSchedule (recurrence rules: daily/weekly/monthly/
         yearly/specific date/weekday/weekend)
      -> ClassSession rows are the actual generated occurrences (what students join)
      -> Classroom's teacher creates ClassPass(es) (daily/weekly/monthly/yearly/free, priced in coins)
      -> Any other user raises a ClassJoinRequest against one of those passes
         (NOT a direct purchase — see ClassJoinRequestViewSet in views.py)
      -> The classroom's teacher/co-teacher/moderator accepts or rejects it.
         Only on acceptance is a PassPurchase actually created and coins
         debited from the requester's User.coin balance (coin economy only —
         the purchase is created ONLY if the requester has enough coins at
         accept-time; see ClassJoinRequestViewSet.accept in views.py). Until
         then the requester holds no pass and is not a participant.
      -> PassPurchase.is_valid() gates join access to ClassSession, and
         Classroom.has_access() gates everything else classroom-detail-ish
         (schedule, sessions, materials, notices, holidays, doubts). A user
         with no valid pass can only ever see a classroom's public listing
         info (title/description/subject/language/cover) plus its reviews.
      -> SessionParticipant logs who actually joined which session (attendance)
"""

import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.contrib.postgres.indexes import GinIndex
from django.db import IntegrityError as DjangoIntegrityError
from django.db import models, transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone
from django.utils.deconstruct import deconstructible

from login.models import User

# ---------------------------------------------------------------------------
# CLASSROOM LIST CACHE VERSIONING
#
# The public Explore/search list (ClassroomViewSet.list, see views.py) is by
# far the highest-traffic read in this app — every visitor hits it, often
# while just browsing before ever buying a pass — yet it was hitting the DB
# (search/filter/order/paginate over every Classroom row) on every single
# request. Caching the rendered page is the obvious fix, but a flat TTL
# cache would show stale ratings/enrolled-counts/newly-posted classrooms for
# up to the TTL window, which is a bad tradeoff for a marketplace listing.
#
# Instead: a single cache-versioning counter. Every request builds its cache
# key as f"...:{version}:{query_string}"; the view never needs to know which
# rows changed, it just needs to know that SOMETHING did. Any change that
# could affect what the list looks like bumps the version once, which
# invalidates every previously-cached page in O(1) — no per-classroom key
# tracking, no partial-invalidation bugs.
#
# bump_classroom_list_cache_version() is called from a single Classroom
# post_save/post_delete receiver at the bottom of this file. That alone
# covers every case that matters: direct API edits, AND refresh_rating() /
# refresh_enrolled_count() / sync_flag_status() below, since all three work
# by calling self.save(update_fields=[...]) on the Classroom itself, which
# already fires Classroom's own post_save signal — no extra wiring needed
# at each call site.
# ---------------------------------------------------------------------------
CLASSROOM_LIST_CACHE_VERSION_KEY = "liveclass:classroom_list:cache_version"


def get_classroom_list_cache_version() -> int:
    version = cache.get(CLASSROOM_LIST_CACHE_VERSION_KEY)
    if version is None:
        version = 1
        cache.set(CLASSROOM_LIST_CACHE_VERSION_KEY, version, timeout=None)
    return version


def bump_classroom_list_cache_version() -> None:
    try:
        cache.incr(CLASSROOM_LIST_CACHE_VERSION_KEY)
    except ValueError:
        # Key doesn't exist yet (cold cache / cache was cleared) — seed it.
        cache.set(CLASSROOM_LIST_CACHE_VERSION_KEY, 2, timeout=None)


# ---------------------------------------------------------------------------
# NOTE (fix — classroom stats realtime push): classroom_detail_screen.dart
# used to refresh enrolled_count/rating with a plain `Timer.periodic(30s)`
# GET, since there was no realtime push for it the way there is for
# join-request badges (see LiveClassUserSocket / broadcast_to_user). This
# gives it one, following the exact same pattern: a per-CLASSROOM channel
# group (as opposed to broadcast_to_user's per-user one, or
# broadcast_to_session's per-live-session one) — `ClassroomConsumer` on a
# `ws/liveclass/classroom/<id>/` route, mirroring `UserConsumer` /
# `ws/liveclass/user/` in consumers.py/routing.py, and a `broadcast_to_classroom()`
# in realtime.py mirroring that module's existing `broadcast_to_user()`.
#
# Hooked at the two places Classroom.rating_avg/rating_count/enrolled_count
# actually change (refresh_rating/refresh_enrolled_count below) rather than
# at every call site that triggers them (PassPurchase/ClassroomReview save
# signals at the bottom of this file) — same one-place-covers-everything
# reasoning the cache-versioning block above already uses for this exact
# pair of methods.
#
# Best-effort / never-block-the-save, same contract as views.py's
# _safe_broadcast_to_user — a channel-layer hiccup degrades to "no live
# push, the screen's fallback poll (now a much longer interval, kept only
# as a correctness backstop) still catches it" rather than ever breaking
# the actual rating/enrollment recompute this runs after.
# ---------------------------------------------------------------------------
def _broadcast_classroom_stats(classroom) -> None:
    try:
        from .realtime import broadcast_to_classroom

        broadcast_to_classroom(
            classroom.id,
            "classroom.stats",
            {
                "classroom_id": classroom.id,
                "rating_avg": float(classroom.rating_avg),
                "rating_count": classroom.rating_count,
                "enrolled_count": classroom.enrolled_count,
            },
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to push realtime stats for classroom %s "
            "(broadcast_to_classroom missing/unreachable?) — recompute "
            "still succeeds; frontend falls back to its backstop poll.",
            classroom.id,
        )


# ---------------------------------------------------------------------------
# PER-CLASSROOM NOTICE LIST CACHE VERSIONING
#
# NOTE (perf — fix): NoticeViewSet.list is read far more often than it's
# written (every enrolled student re-checks a classroom's notice board
# regularly; a teacher posts a handful of notices total). It was hitting
# the DB on every single request with no caching at all — same shape of gap
# as the Classroom Explore/search list before it got the version-based
# cache above, just scoped to one classroom's notices instead of the whole
# platform. Same pattern reused here rather than inventing a new one: one
# version counter PER CLASSROOM (not global — bumping one classroom's
# notices shouldn't invalidate every other classroom's cached notice page),
# bumped from a single Notice post_save/post_delete receiver below.
# ---------------------------------------------------------------------------
def _notice_list_cache_version_key(classroom_id) -> str:
    return f"liveclass:notice_list:cache_version:{classroom_id}"


def get_notice_list_cache_version(classroom_id) -> int:
    key = _notice_list_cache_version_key(classroom_id)
    version = cache.get(key)
    if version is None:
        version = 1
        cache.set(key, version, timeout=None)
    return version


def bump_notice_list_cache_version(classroom_id) -> None:
    key = _notice_list_cache_version_key(classroom_id)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 2, timeout=None)


# ---------------------------------------------------------------------------
# NOTE (fix): none of the FileField/ImageField columns below (cover_image,
# class materials, assignment attachments/submissions, certificate files)
# had any size limit. An authenticated user could upload an arbitrarily
# large file to any of these — slow requests, unbounded storage/bandwidth
# cost, and an easy denial-of-service vector against disk/object-storage
# quota. @deconstructible so Django can serialize this into migrations
# (a plain closure/lambda can't be).
#
# NOTE (fix — file-type spoofing): the four plain FileFields below (material,
# assignment attachment, assignment submission, certificate) had NO
# extension restriction at all beyond the size cap — a student could upload
# a renamed .exe/.php/.js/.sh as their "assignment submission" or a teacher
# could do the same as "class material", which then sits in storage and
# gets served back to every other student/teacher who opens it (a stored-
# malware / drive-by vector, not something the size cap touches at all).
# cover_image is already safe on this front for free: Django's ImageField
# actually opens the upload with Pillow to confirm it's a real, decodable
# image (a renamed .exe fails that outright, and SVG is rejected too since
# Pillow can't decode it — closing the classic SVG/XSS upload vector as a
# side effect). The other four are plain FileFields with no such built-in
# check, so each now also gets a FileExtensionValidator scoped to what that
# field is actually supposed to hold — safelist, not a blocklist, so a
# brand-new dangerous extension can't slip through just because nobody
# thought to blocklist it yet.
# ---------------------------------------------------------------------------
DOCUMENT_MEDIA_EXTENSIONS = [
    "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx",
    "png", "jpg", "jpeg", "gif", "webp",
    "mp4", "mov", "webm",
    "zip",
]


@deconstructible
class MaxFileSizeValidator:
    def __init__(self, max_mb: int):
        self.max_mb = max_mb

    def __call__(self, file):
        limit_bytes = self.max_mb * 1024 * 1024
        if file.size > limit_bytes:
            raise ValidationError(f"File too large — max {self.max_mb}MB.")

    def __eq__(self, other):
        return isinstance(other, MaxFileSizeValidator) and self.max_mb == other.max_mb


# ---------------------------------------------------------------------------
# 1. CLASSROOM
# ---------------------------------------------------------------------------
class Classroom(models.Model):
    """A teacher's course/batch container. One classroom can run many sessions."""

    class ClassroomType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        ORGANISATION = "organisation", "Organisation"

    # Whether this classroom is run by a single individual teacher or by an
    # organisation (a coaching institute, school, company, etc.) with
    # multiple staff members behind it. When ORGANISATION, organisation_name
    # is required and the classroom's "staff" (see ClassroomStaff below) is
    # shown on the detail page as the org's team, not just co-teachers.
    classroom_type = models.CharField(
        max_length=20, choices=ClassroomType.choices, default=ClassroomType.INDIVIDUAL
    )
    organisation_name = models.CharField(
        max_length=150, blank=True,
        help_text="Required when classroom_type is 'organisation'.",
    )

    teacher = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="classrooms_teaching",
        # No limit_choices_to / role restriction on purpose: this is an open
        # marketplace — ANY authenticated user can create a classroom and
        # thereby become its teacher. "Teacher" is not a fixed User role,
        # it's just whoever owns this particular Classroom row.
    )

    title = models.CharField(max_length=150)
    subject = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    cover_image = models.ImageField(
        upload_to="classroom_covers/", null=True, blank=True, validators=[MaxFileSizeValidator(5)]
    )

    # language the class is taught in — used as a search/filter facet on the
    # classroom listing page (e.g. "Hindi", "English", "Hinglish")
    language = models.CharField(max_length=50, default="English", blank=True)

    # feature toggles for the live room (frontend reads these to enable UI)
    whiteboard_enabled = models.BooleanField(default=True)
    screen_share_enabled = models.BooleanField(default=True)
    chat_enabled = models.BooleanField(default=True)
    recording_enabled = models.BooleanField(default=True)
    # NOTE (fix — captions wiring, item 3): per-classroom opt-in for
    # speech-to-text captions (tasks.transcribe_recording), mirroring
    # recording_enabled's own shape. Default False (opt-in, not opt-on)
    # so transcription cost is only ever incurred for classrooms that
    # explicitly asked for captions — see LiveKitWebhookView's
    # egress_ended handler (views.py), which checks this flag before
    # queueing transcribe_recording.delay().
    captions_enabled = models.BooleanField(default=False)

    max_participants = models.PositiveIntegerField(
        default=100, validators=[MinValueValidator(1)]
    )

    # ---- cached stats (denormalized for fast list/search pages; kept in
    # sync by the signal receivers at the bottom of this file, so the
    # classroom list API never has to run an aggregate query per row) ----
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    rating_count = models.PositiveIntegerField(default=0)
    enrolled_count = models.PositiveIntegerField(
        default=0, help_text="Distinct students currently holding an active, unexpired pass."
    )
    # ---------------------------------------------------------------------
    # FEATURE (share): denormalized counter, bumped once per ClassroomShare
    # row (see that model below) regardless of channel — in-app or
    # external. Cached here for the same reason rating_avg/enrolled_count
    # are: the Explore card and the teacher's own stats view need this on
    # every list render, and counting ClassroomShare rows per-row would be
    # an extra aggregate query per classroom on the highest-traffic read
    # path in this app.
    # ---------------------------------------------------------------------
    share_count = models.PositiveIntegerField(
        default=0, help_text="Total number of times this classroom has been shared, any channel."
    )

    # ---------------------------------------------------------------------
    # FEATURE (refer & earn — class-level referral commission): distinct
    # from the flat, one-time signup Referral model below (13B) — that one
    # pays a fixed bonus for bringing a brand-new USER onto the platform.
    # This one pays out on a per-CLASSROOM basis, ongoing, for as long as
    # the referred student's pass keeps getting charged.
    #
    # Off by default and fully opt-in per classroom: the teacher turns it
    # on and sets the cut (see referral_commission_percent) from their own
    # classroom settings (ClassroomSerializer exposes both as normal
    # writable fields, gated by the existing "only the teacher can edit
    # this classroom" check in ClassroomViewSet.perform_update — no new
    # endpoint needed for that half). Once on, ClassroomViewSet.refer_link
    # (views.py) hands out a shareable link to ANY authenticated user —
    # reusing the same referral_code_for_user()/referral_code_to_user_id()
    # helpers the signup-referral feature already has (13B below), just
    # combined with this classroom's id in the link instead of a bare
    # code. See ClassJoinRequest.referred_by for how that link is turned
    # back into an attributed purchase, and PassPurchase.charge_for_session
    # for how the actual coins get paid out.
    # ---------------------------------------------------------------------
    referral_enabled = models.BooleanField(
        default=False,
        help_text=(
            "When on, any authenticated user can generate a shareable referral link for this "
            "classroom (see classrooms/{id}/refer-link/) and earn a daily commission on any "
            "pass purchase that comes in through it."
        ),
    )
    referral_commission_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text=(
            "% of each day's released class-earning charge (see PassPurchase.charge_for_session) "
            "that gets paid to whoever referred the student — deducted OUT OF the teacher's own "
            "share, never added on top by the platform. The teacher sets this rate themselves and "
            "funds it out of their own earnings. Only takes effect while referral_enabled=True."
        ),
    )

    is_active = models.BooleanField(default=True)

    # ---------------------------------------------------------------------
    # NOTE (fix): anti-abuse — a teacher used to be able to create a
    # classroom, sell passes for coins, then immediately DELETE it
    # (perform_destroy did a bare instance.delete(), CASCADE and all — no
    # age check, no check for students still holding a paid, unexpired
    # pass). That let someone collect coins and vanish with zero trace and
    # zero way for a student to get their coins back. Fixed with:
    #   - is_deleted/deleted_at: real deletion is now a SOFT delete (see
    #     ClassroomViewSet.perform_destroy) — the row, and its full
    #     purchase/report history, stays intact for dispute resolution.
    #   - can_be_deleted() below: blocks deletion until the classroom is at
    #     least MIN_AGE_BEFORE_DELETE_DAYS old AND no student is currently
    #     holding a paid, unexpired, un-refunded pass for it. A teacher who
    #     wants to shut down early must use classrooms/{id}/close/, which
    #     refunds every such pass first (see ClassroomViewSet.close).
    #   - is_flagged: set automatically once a classroom collects enough
    #     pending ClassroomReport rows (see _auto_flag_classroom below) —
    #     drops it out of the public Explore listing until platform staff
    #     review it, without touching the teacher's own access to it.
    # ---------------------------------------------------------------------
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    is_flagged = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # A classroom can only be (soft-)deleted once it's at least this old —
    # see can_be_deleted().
    MIN_AGE_BEFORE_DELETE_DAYS = 30

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["teacher", "is_active"]),
            models.Index(fields=["-rating_avg"]),
            models.Index(fields=["language"]),
            models.Index(fields=["classroom_type"]),
            models.Index(fields=["is_deleted"]),
            # NOTE (perf — fix): ClassroomViewSet.get_queryset's ?search=
            # does `title__icontains` / `subject__icontains` /
            # `description__icontains`, each of which is a sequential scan
            # over every Classroom row — fine at hundreds of rows, a real
            # cost once the platform has thousands. A trigram GIN index lets
            # Postgres use an index scan for icontains/substring matches
            # instead. Requires the `pg_trgm` extension (enabled via a
            # migration — see migrations/0XXX_trigram_search_indexes.py in
            # the same change) and Postgres as the DB backend; this index is
            # silently ignored (not an error) on any other backend, so it's
            # safe to leave in Meta.indexes even if a non-Postgres DB is
            # ever used in a test/dev environment.
            GinIndex(fields=["title"], name="classroom_title_trgm", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["subject"], name="classroom_subject_trgm", opclasses=["gin_trgm_ops"]),
            GinIndex(
                fields=["description"], name="classroom_desc_trgm", opclasses=["gin_trgm_ops"]
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.teacher})"

    def has_access(self, user: User) -> bool:
        """Does this user currently hold a VALID (unexpired, not-over-the-
        max_classes-cap) pass for this classroom? This is the tight gate —
        it's what actually lets someone into the live room (see
        _has_room_access in views.py) and what counts them in
        enrolled_count. Teacher always passes.

        NOTE (fix — max_classes cap silently did nothing): this used to
        stop at status/expires_at/is_active and never looked at
        max_classes/classes_attended at all — a duplicate, narrower copy
        of the checks PassPurchase.is_valid() already does, minus the cap
        one. A "5-class pack" pass (max_classes=5) therefore let a student
        join unlimited sessions, because the ONE place that actually gates
        room entry never excluded a purchase that had used up its cap.
        Excluding it here (rather than switching to a Python is_valid()
        loop) keeps this a single indexed query instead of N.
        """
        if user.id == self.teacher_id:
            return True
        # NOTE (fix — ClassroomBan had no teeth): a banned student's pass is
        # already reversed/refunded when the ban is issued (see
        # ClassroomViewSet.ban), but this check is what stops a banned
        # student from ever getting back in even in an edge case that
        # refund missed (e.g. a pass purchased in the brief window between
        # the ban query and the refund transaction committing).
        if self.bans.filter(student_id=user.id).exists():
            return False
        return (
            PassPurchase.objects.filter(
                student=user,
                class_pass__classroom=self,
                status=PassPurchase.Status.SUCCESS,
                expires_at__gt=timezone.now(),
                is_active=True,
            )
            .exclude(
                class_pass__max_classes__isnull=False,
                classes_attended__gte=models.F("class_pass__max_classes"),
            )
            .exists()
        )

    def is_enrolled(self, user: User) -> bool:
        """Has this user EVER successfully purchased a pass for this
        classroom — active OR expired? Broader than has_access() on purpose:
        a lapsed pass should still unlock the classroom's general content
        (materials, notices, holidays, schedule/session listing, doubts,
        assignments, certificates, reviews) — everything except actually
        entering a live session, which stays gated behind has_access() /
        _has_room_access(). A user who never held any pass (or whose only
        request is still pending/rejected/cancelled) gets False here, and is
        limited to the classroom's public listing info plus its reviews.
        Teacher always passes."""
        if user.id == self.teacher_id:
            return True
        return PassPurchase.objects.filter(
            student=user,
            class_pass__classroom=self,
            status=PassPurchase.Status.SUCCESS,
            is_active=True,
        ).exists()

    def can_be_deleted(self) -> tuple[bool, str]:
        """Guard used by ClassroomViewSet.perform_destroy. Returns
        (allowed, reason_if_not). Two independent conditions must both
        hold — see the NOTE (fix) above the field definitions for why:
          1. The classroom must be at least MIN_AGE_BEFORE_DELETE_DAYS old.
          2. No student may currently hold a paid, unexpired, un-refunded
             pass for it (free passes don't block deletion — no coins are
             at stake there).
        """
        age = timezone.now() - self.created_at
        if age < timezone.timedelta(days=self.MIN_AGE_BEFORE_DELETE_DAYS):
            days_left = self.MIN_AGE_BEFORE_DELETE_DAYS - age.days
            return False, (
                f"A classroom can only be deleted {self.MIN_AGE_BEFORE_DELETE_DAYS} days after "
                f"it's created — {days_left} day(s) left. Use the 'close' action instead if you "
                f"want to stop running it now; that refunds every active student immediately."
            )
        blocking = PassPurchase.objects.filter(
            class_pass__classroom=self,
            status=PassPurchase.Status.SUCCESS,
            is_active=True,
            expires_at__gt=timezone.now(),
            coins_spent__gt=0,
        ).count()
        if blocking:
            return False, (
                f"{blocking} student(s) still hold a paid, active pass for this classroom. "
                f"Refund them (see classrooms/{{id}}/close/ or pass-purchases/{{id}}/refund/) "
                f"or wait for their passes to expire before deleting."
            )
        return True, ""

    def sync_flag_status(self):
        """Recompute is_flagged from CURRENT pending report count.

        NOTE (fix — permanent-hide bug): _auto_flag_classroom (signal below)
        only ever sets is_flagged=True; nothing ever flipped it back to
        False. A classroom that crossed AUTO_FLAG_THRESHOLD once stayed
        hidden from Explore forever — even after platform staff reviewed
        every report and dismissed them all as unfounded, there was no API
        path back to visibility, only a manual DB edit. Called from
        ClassroomReportViewSet.review() after every status change so the
        flag always reflects the live pending count: drops back to False
        the moment pending reports fall below the threshold again (e.g.
        staff dismisses the reports, or resolves them via
        action_taken -> the underlying issue is fixed and new reports never
        arrive), and re-flags if pending reports climb back up.
        """
        pending_count = self.reports.filter(status=ClassroomReport.Status.PENDING).count()
        should_be_flagged = pending_count >= ClassroomReport.AUTO_FLAG_THRESHOLD
        if should_be_flagged != self.is_flagged:
            self.is_flagged = should_be_flagged
            self.save(update_fields=["is_flagged"])

    def refresh_rating(self):
        """Recompute rating_avg/rating_count from ClassroomReview rows.
        Called automatically by the post_save/post_delete signal below;
        exposed here too so a management command can bulk-repair drift."""
        agg = self.reviews.aggregate(avg=models.Avg("rating"), count=models.Count("id"))
        self.rating_avg = round(agg["avg"] or 0, 2)
        self.rating_count = agg["count"] or 0
        self.save(update_fields=["rating_avg", "rating_count"])
        _broadcast_classroom_stats(self)

    def refresh_enrolled_count(self):
        """Recompute enrolled_count = distinct students with a currently valid pass.
        Called on every PassPurchase save (see signal below). Because validity
        also depends on expires_at, a periodic scheduled task should call this
        for classrooms with soon-expiring passes to keep the count from going
        stale between purchases."""
        self.enrolled_count = (
            PassPurchase.objects.filter(
                class_pass__classroom=self,
                status=PassPurchase.Status.SUCCESS,
                is_active=True,
                expires_at__gt=timezone.now(),
            )
            .values("student_id")
            .distinct()
            .count()
        )
        self.save(update_fields=["enrolled_count"])
        _broadcast_classroom_stats(self)

    def weekly_timing_summary(self):
        """Human-friendly 'when does this class run' string built from all
        active schedules, e.g. 'Mon, Wed, Fri 6:00 PM (60 min)'. Used by the
        classroom list/detail API so the frontend doesn't have to stitch
        ClassSchedule rows together itself."""
        parts = []
        for sched in self.schedules.filter(is_active=True):
            if sched.recurrence_type == ClassSchedule.RecurrenceType.WEEKLY and sched.days_of_week:
                days = ", ".join(d.title() for d in sched.days_of_week)
            else:
                days = sched.get_recurrence_type_display()
            parts.append(f"{days} {sched.start_time:%I:%M %p} ({sched.duration_minutes} min)")
        return " | ".join(parts) if parts else "No active schedule set"

    def upcoming_holidays(self, days_ahead: int = 30):
        """Off-days (festival/leave exceptions) coming up for this classroom,
        used to render a 'class off on these dates' notice to students."""
        today = timezone.now().date()
        return self.holidays.filter(date__gte=today, date__lte=today + timezone.timedelta(days=days_ahead))

    def record_share(self) -> None:
        """Bump the denormalized share_count. Called once per ClassroomShare
        row created (see ClassroomViewSet.share in views.py) — kept as a
        tiny F()-expression update (not read-modify-write) so two shares
        landing at the same instant can't stomp on each other the way a
        plain `self.share_count += 1; self.save()` would."""
        Classroom.objects.filter(pk=self.pk).update(share_count=models.F("share_count") + 1)
        self.refresh_from_db(fields=["share_count"])

    def share_urls(self) -> tuple[str, str]:
        """(web_url, deep_link) for this classroom, used by
        ClassroomViewSet.share to hand the client something it can either
        open in-app (deep_link) or drop into any outside-the-app share
        target — WhatsApp, SMS, email, copy-link, etc. — where a
        recipient without the app installed still lands somewhere useful
        (web_url).

        Both base pieces are getattr(settings, ..., default) with sane
        fallbacks, same pattern CHUNKED_UPLOAD_TMP_ROOT uses in
        chunked_upload_views.py — this works out of the box in dev and is
        meant to be overridden in settings.py/.env per environment
        (staging vs production web domain, custom URL scheme per app
        flavor) without touching this code.
        """
        from django.conf import settings

        web_base = getattr(settings, "LIVECLASS_WEB_BASE_URL", "https://app.example.com").rstrip("/")
        deep_link_scheme = getattr(settings, "LIVECLASS_DEEP_LINK_SCHEME", "liveclass")
        web_url = f"{web_base}/classroom/{self.id}"
        deep_link = f"{deep_link_scheme}://classroom/{self.id}"
        return web_url, deep_link

    def referral_urls(self, referrer: User) -> tuple[str, str]:
        """(web_url, deep_link) variants of share_urls() above, tagged with
        `referrer`'s referral code as a `?ref=` param — used by
        ClassroomViewSet.refer_link, and read back by
        ClassJoinRequestViewSet.perform_create (via referral_code_to_user_id)
        to attribute the resulting join request/purchase to `referrer`. Only
        meaningful while referral_enabled=True; callers are responsible for
        that check (this method doesn't raise on it, so it stays cheap and
        side-effect-free to call from anywhere, including a notification
        template)."""
        web_url, deep_link = self.share_urls()
        code = referral_code_for_user(referrer.id)
        return f"{web_url}?ref={code}", f"{deep_link}?ref={code}"


# ---------------------------------------------------------------------------
# 2. SCHEDULE (recurrence rule -> generates ClassSession rows)
# ---------------------------------------------------------------------------
class ClassSchedule(models.Model):
    class RecurrenceType(models.TextChoices):
        SPECIFIC_DATE = "specific_date", "Specific Date"
        DAILY = "daily", "Daily"
        WEEKDAY = "weekday", "Weekdays (Mon-Fri)"
        WEEKEND = "weekend", "Weekends (Sat-Sun)"
        WEEKLY = "weekly", "Weekly (custom days)"
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="schedules")

    recurrence_type = models.CharField(max_length=20, choices=RecurrenceType.choices)

    # Canonical, lowercase 3-letter weekday codes — the ONLY values
    # days_of_week is allowed to contain. Single source of truth shared by
    # ClassScheduleSerializer.validate() (normalizes + validates incoming
    # data) and tasks.py's _WEEKDAY_CODE lookup (matches recurrence dates
    # against it). Both sides MUST agree on this exact casing/format, or a
    # WEEKLY schedule silently generates zero sessions forever with no
    # error anywhere (see tasks.py NOTE (fix) on _dates_for_schedule) —
    # that's what having two independently-typed copies of this list
    # previously caused.
    WEEKDAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

    # used when recurrence_type == WEEKLY (custom day picker), e.g. ["mon","wed","fri"]
    days_of_week = models.JSONField(default=list, blank=True)

    # used when recurrence_type == MONTHLY, e.g. day-of-month 1-31
    day_of_month = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(31)]
    )

    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)  # null = runs indefinitely

    start_time = models.TimeField()
    duration_minutes = models.PositiveIntegerField(default=60)

    timezone = models.CharField(max_length=50, default="Asia/Kolkata")

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_date", "start_time"]

    def __str__(self):
        return f"{self.classroom.title} - {self.get_recurrence_type_display()}"

    def is_off_on(self, date) -> bool:
        """True if `date` is marked as a holiday for this schedule specifically,
        or classroom-wide (ClassHoliday.schedule is null = applies to every
        schedule). Whatever job generates ClassSession rows from this
        recurrence rule should call this and skip the date if True."""
        return ClassHoliday.objects.filter(classroom_id=self.classroom_id, date=date).filter(
            models.Q(schedule_id=self.id) | models.Q(schedule__isnull=True)
        ).exists()


# ---------------------------------------------------------------------------
# 3. SESSION (an actual, joinable live-class occurrence)
# ---------------------------------------------------------------------------
class ClassSession(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        LIVE = "live", "Live"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="sessions")
    schedule = models.ForeignKey(
        ClassSchedule, on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions"
    )

    room_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField()

    actual_start = models.DateTimeField(null=True, blank=True)
    actual_end = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)

    recording_url = models.URLField(blank=True)
    # FEATURE: recording engine wiring. `recording_url` above already
    # existed as a place to STORE the final link, and Classroom.recording_enabled
    # already exists as a per-classroom toggle — but nothing anywhere ever
    # called LiveKit's Egress API, so recording_url could never actually get
    # populated; the toggle was a setting with no engine behind it. This
    # holds the in-flight LiveKit egress job's ID between start_recording()
    # and stop_recording() (see views.py) — needed to call stop_egress()
    # later, and to match an incoming `egress_ended` webhook (see
    # LiveKitWebhookView) back to the right session so recording_url gets
    # filled in once the file finishes uploading. Blank = not currently
    # recording.
    egress_id = models.CharField(max_length=64, blank=True)

    # NOTE (fix — whiteboard/spotlight persistence): whiteboard_snapshot
    # already existed on this model but nothing ever read/wrote it — the
    # whiteboard lived only in each connected client's memory, synced
    # peer-to-peer over the LiveKit data channel. That's fine for a
    # brand-new joiner (they get caught up peer-to-peer by whoever's
    # already in the room), but breaks the moment nobody left in the room
    # still holds the strokes — e.g. everyone disconnects and the host
    # reconnects alone, or a student joins before anyone with the current
    # board has (re)joined. spotlight_identity is the same story for the
    # host's pinned/spotlighted tile. Both are now written by dedicated
    # actions on ClassSessionViewSet (whiteboard()/spotlight() in
    # views.py) instead of the generic PATCH, so a non-host participant
    # can still autosave whiteboard strokes without needing full
    # classroom-manage permissions. See those actions' docstrings for the
    # permission split.
    whiteboard_snapshot = models.JSONField(null=True, blank=True)
    spotlight_identity = models.CharField(max_length=64, blank=True)  # LiveKit identity (str(user_id)) of the pinned tile, "" = none

    # FEATURE (captions — see tasks.transcribe_recording/poll_transcription_job):
    # once recording_url is filled in by the egress webhook above, and
    # Classroom.captions_enabled is on, a transcription job is queued
    # against that file. This holds the job's state/result — provider is
    # AWS Transcribe (see tasks.py); transcription_job_name below tracks
    # the specific in-flight job.
    class CaptionStatus(models.TextChoices):
        NONE = "none", "Not Requested"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    caption_status = models.CharField(max_length=12, choices=CaptionStatus.choices, default=CaptionStatus.NONE)
    caption_url = models.URLField(blank=True)  # points at a .vtt/.srt file once READY
    # NOTE (fix — captions wiring, item 3): AWS Transcribe job name for
    # the in-flight transcription job started by tasks.transcribe_recording
    # — needed so the periodic tasks.poll_transcription_job can look up
    # job status later (job name includes a random suffix, so it can't be
    # re-derived from session_id alone once the job's already running).
    # Blank = no transcription job currently in flight for this session.
    transcription_job_name = models.CharField(max_length=128, blank=True)

    # NEW (Pass 14 — post-session engagement report). Computed once by
    # tasks.build_engagement_report (queued from cleanup_on_session_end's
    # on_commit hook in signals.py, alongside the existing LiveKit-teardown/
    # poll-close/participant-checkout steps) the moment a session reaches
    # COMPLETED, then just read back by ClassSessionViewSet.engagement_report
    # — never recomputed per-request, so a teacher re-opening the report a
    # dozen times doesn't re-run five aggregate queries a dozen times.
    # Null until that task runs (or for a session that never went LIVE).
    # See ClassSession.compute_engagement_report() below for the shape.
    engagement_report = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scheduled_start"]
        indexes = [models.Index(fields=["classroom", "status", "scheduled_start"])]

    def __str__(self):
        return f"{self.classroom.title} @ {self.scheduled_start:%d-%b-%Y %H:%M}"

    def is_joinable(self, is_host: bool = False) -> bool:
        """Allow joining as long as the session itself is still a live
        occurrence (not explicitly ended/cancelled).

        NOTE (fix — time-window entry restriction removed): this used to
        block a student from entering more than 10 minutes before
        scheduled_start or after scheduled_end, and only exempted the
        host/staff from that window. Product decision: everyone should be
        able to open the room at any time while the session is still
        SCHEDULED/LIVE — a student arriving early or a bit late should
        still get in, not a "not joinable right now" wall. The actual
        scheduled_start/scheduled_end are still sent to the client on
        every session payload (ClassSessionSerializer), so the frontend
        can show "Session starts at <local time>" / "Scheduled to end at
        <local time>" as an informational banner instead of a hard block.
        `is_host` is kept as a parameter (unused now) so existing call
        sites (_perform_join, ClassSessionViewSet.join()) don't need to
        change their calls.
        """
        return self.status in (self.Status.SCHEDULED, self.Status.LIVE)

    def compute_engagement_report(self) -> dict:
        """Aggregate, don't join-explode: each metric is its own small
        query against a table indexed on session_id (participants/
        chat_messages/live_polls all already carry that index — see each
        model's Meta) rather than one giant multi-table join, so this
        stays cheap even for a long-running session with thousands of
        chat rows. Pure computation, no save — see build_engagement_report
        in tasks.py for who calls this and persists the result onto
        `engagement_report`.
        """
        participants = list(self.participants.filter(role=SessionParticipant.Role.STUDENT))
        attendee_count = len(participants)

        durations = []
        for p in participants:
            ended = p.left_at or self.actual_end or timezone.now()
            if p.joined_at and ended > p.joined_at:
                durations.append((ended - p.joined_at).total_seconds())
        avg_watch_seconds = int(sum(durations) / len(durations)) if durations else 0

        scheduled_seconds = max((self.scheduled_end - self.scheduled_start).total_seconds(), 1)

        chat_qs = self.chat_messages.filter(is_deleted=False)
        chat_message_count = chat_qs.count()
        distinct_chatters = chat_qs.values("sender_id").distinct().count()

        polls = list(self.polls.all())
        poll_count = len(polls)
        total_poll_responses = PollResponse.objects.filter(poll__session=self).count()
        avg_responses_per_poll = round(total_poll_responses / poll_count, 1) if poll_count else 0

        hands_raised = SessionParticipant.objects.filter(session=self, hand_raised_at__isnull=False).count()

        return {
            "attendee_count": attendee_count,
            "avg_watch_seconds": avg_watch_seconds,
            "avg_watch_percent": round(min(avg_watch_seconds / scheduled_seconds, 1.0) * 100, 1),
            "chat_message_count": chat_message_count,
            "distinct_chatters": distinct_chatters,
            "poll_count": poll_count,
            "total_poll_responses": total_poll_responses,
            "avg_responses_per_poll": avg_responses_per_poll,
            "hands_raised_count": hands_raised,
            "computed_at": timezone.now().isoformat(),
        }


# ---------------------------------------------------------------------------
# 4. PASS (pricing plan a teacher sets up for a classroom)
# ---------------------------------------------------------------------------
class ClassPass(models.Model):
    class PassType(models.TextChoices):
        FREE = "free", "Free"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="passes")

    pass_type = models.CharField(max_length=20, choices=PassType.choices)
    title = models.CharField(max_length=100, blank=True)  # e.g. "Weekend Batch - Monthly"

    price = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
        help_text="Price in coins (User.coin). 0 = free pass.",
    )
    validity_days = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="How many days this pass stays valid after purchase",
    )

    # optional cap: e.g. a "5-class pack"; null = unlimited within validity window
    max_classes = models.PositiveIntegerField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    # FIX (frontend cross-check — Pass 14 gifting): pass_management_screen.dart
    # (Dart) already ships a per-pass "Allow gifting" toggle and
    # PassGiftClaimScreen already lets a student receive a gifted pass, but
    # nothing on this model or PassGiftViewSet.perform_create ever actually
    # gated gifting by it — every active pass was giftable regardless of what
    # a teacher set here. Defaults to True (opt-out) so existing passes keep
    # today's de-facto "always giftable" behavior after this migrates.
    # NOTE: adding this field requires a migration
    # (`manage.py makemigrations liveclass`) before it takes effect.
    allow_gifting = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["price"]

    def __str__(self):
        return f"{self.classroom.title} - {self.get_pass_type_display()} ({self.price} coins)"


# ---------------------------------------------------------------------------
# 5. PASS PURCHASE (student's ownership of a pass -> gates access)
# ---------------------------------------------------------------------------
class PassPurchase(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    class PaymentMethod(models.TextChoices):
        # No "online"/card/UPI method — this platform runs on an internal coin
        # economy only. A pass is ALWAYS paid for out of User.coin, except
        # when the effective price is 0 (a free pass, or a coupon that zeroes
        # the price), in which case it's just marked FREE with no coin debit.
        COIN_WALLET = "coin_wallet", "Coin Wallet"
        FREE = "free", "Free"

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="pass_purchases")
    class_pass = models.ForeignKey(ClassPass, on_delete=models.CASCADE, related_name="purchases")
    coupon = models.ForeignKey(
        "Coupon", on_delete=models.SET_NULL, null=True, blank=True, related_name="redemptions"
    )

    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.COIN_WALLET
    )
    amount_paid = models.DecimalField(max_digits=8, decimal_places=2)
    coins_spent = models.PositiveIntegerField(default=0)
    transaction_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    purchased_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    classes_attended = models.PositiveIntegerField(default=0)  # for max_classes packs
    is_active = models.BooleanField(default=True)

    # -----------------------------------------------------------------
    # NOTE (fix — "pay only for classes actually held"): a pass used to be
    # settled in one shot — the full coins_spent left the student's wallet
    # AND landed in the teacher's wallet the moment ClassJoinRequestViewSet
    # .accept() ran (see _charge_and_create_purchase in views.py). That's
    # fine if the teacher runs every single class for the whole
    # validity_days window, but the moment they stop mid-way — go quiet
    # for the rest of a yearly pass, close the classroom, whatever — the
    # student has already paid for months of classes nobody ever held, and
    # getting that back depended entirely on a manual refund clawing coins
    # back OUT of a teacher who may well have already spent/withdrawn them
    # (see the clamp in the old _refund_purchase). Actual loss for the
    # student, not just a UX gap.
    #
    # Fixed by turning a pass into an escrow instead of a lump-sum
    # transfer: coins_spent still leaves the student's wallet up front
    # (unchanged — a coin sitting in the student's own wallet doesn't
    # protect them any better, since coupon/balance checks already ran
    # once at accept() time), but it is NOT credited to the teacher all at
    # once. It's released to the teacher one calendar day at a time, and
    # ONLY for a day this pass's classroom actually held a class (a
    # ClassSession that reached COMPLETED) — see charge_for_session()
    # below, fired by the post_save signal on ClassSession near the
    # bottom of this file. A day with no class (holiday, teacher no-show,
    # cancelled session) never charges anything, no matter how much
    # validity-day time passes. Whatever hasn't been released yet
    # (remaining_balance) is exactly what reverse() refunds — see below.
    #
    #   per_day_rate     — snapshot of coins_spent / validity_days at
    #                       purchase time (see _charge_and_create_purchase
    #                       in views.py). Frozen on the purchase itself so
    #                       a later price change to the ClassPass can
    #                       never retroactively change what an existing
    #                       purchase charges per day.
    #   coins_released    — running total already released to the
    #                       teacher, one PassDailyCharge row at a time.
    #                       Capped at coins_spent — see charge_for_session().
    #   last_charge_date  — last calendar date this purchase has been
    #                       charged up to (or attempted); lets
    #                       sync_missed_charges() report progress without
    #                       re-deriving it from PassDailyCharge each time.
    # -----------------------------------------------------------------
    per_day_rate = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    coins_released = models.PositiveIntegerField(default=0)
    last_charge_date = models.DateField(null=True, blank=True)
    # NOTE (visibility fix): once reverse() runs, remaining_balance drops to
    # 0 immediately (that coin has already moved to the student's wallet) —
    # so "how much did I actually get refunded?" became unanswerable from
    # the purchase row after the fact, only reconstructable by cross-
    # referencing CoinTransaction.reference_id. Snapshot it here instead so
    # a student's purchase-history card can show the number directly.
    refunded_amount = models.PositiveIntegerField(default=0)
    refunded_at = models.DateTimeField(null=True, blank=True)

    # -----------------------------------------------------------------
    # FEATURE (refer & earn): the same per-day escrow release that pays
    # the teacher (see charge_for_session below) also pays a commission
    # to whoever referred this student — IF they came in through a
    # referral link and the classroom had referrals on at accept()-time.
    # UPDATE: the commission is now deducted OUT OF the teacher's own
    # per-day release, never added on top by the platform — see
    # charge_for_session's teacher_amount/referral_amount split. It only
    # exists at all because the teacher opted in via
    # Classroom.referral_enabled and chose the cut themselves via
    # Classroom.referral_commission_percent.
    # Set once, at purchase-creation time, from ClassJoinRequest.referred_by
    # + Classroom.referral_commission_percent — see
    # _charge_and_create_purchase in views.py.
    #
    #   referred_by                 — who gets the commission. Null =
    #                                  no referral on this purchase.
    #   referral_commission_percent — snapshot of the classroom's rate at
    #                                  purchase time, same "frozen so a
    #                                  later settings change never
    #                                  retroactively changes an existing
    #                                  purchase" reasoning as per_day_rate.
    #   referral_per_day_rate       — per_day_rate's cut, at that snapshot
    #                                  percent, pre-computed so
    #                                  charge_for_session doesn't have to
    #                                  redo the percentage math every day.
    #   referral_coins_released     — running total already paid to the
    #                                  referrer, mirrors coins_released.
    # -----------------------------------------------------------------
    referred_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="referred_purchases",
    )
    referral_commission_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    referral_per_day_rate = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    referral_coins_released = models.PositiveIntegerField(default=0)

    # -----------------------------------------------------------------
    # NEW (Pass 15 — auto-renew passes, subscription-style recurring
    # access). Off by default — a student opts IN per purchase via
    # PassPurchaseViewSet.toggle_auto_renew. When on, and this purchase
    # expires, renew() (below) is expected to be called by a scheduled
    # sweep (e.g. tasks.run_auto_renewals, checking `auto_renew=True,
    # status=SUCCESS, expires_at__lte=now`) to buy a fresh PassPurchase
    # for the SAME class_pass/student, chained via renewed_into/
    # renewed_from so a student's purchase history reads as one
    # continuous subscription rather than a series of unrelated buys.
    #
    #   auto_renew     — the student's own opt-in switch. Cleared
    #                     automatically by renew() if a renewal attempt
    #                     ever fails for insufficient balance (never
    #                     silently retried forever — see renew()).
    #   renewed_from    — the purchase this one was auto-renewed FROM, if
    #                     any. Null for a purchase that was bought
    #                     directly (join-request accept, gift claim).
    #   renewal_failed_at — set (and auto_renew cleared) the moment a
    #                     renewal attempt fails for lack of coins, so the
    #                     app can prompt the student to top up instead of
    #                     silently losing access with no explanation.
    # -----------------------------------------------------------------
    auto_renew = models.BooleanField(default=False)
    renewed_from = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="renewed_into"
    )
    renewal_failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-purchased_at"]
        indexes = [
            models.Index(fields=["student", "status", "expires_at"]),
            models.Index(fields=["referred_by"]),
            # NEW (Pass 15): the exact shape tasks.run_auto_renewals sweeps
            # — every SUCCESS purchase with auto_renew on, ordered by
            # who's expiring soonest.
            models.Index(fields=["auto_renew", "status", "expires_at"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.class_pass} (expires {self.expires_at:%d-%b-%Y})"

    def is_valid(self) -> bool:
        if self.status != self.Status.SUCCESS or not self.is_active:
            return False
        if timezone.now() > self.expires_at:
            return False
        if self.class_pass.max_classes and self.classes_attended >= self.class_pass.max_classes:
            return False
        return True

    @property
    def remaining_balance(self) -> int:
        """Coins still sitting in escrow for this purchase — already
        debited from the student, not yet released to the teacher. This
        is exactly what reverse() below refunds, and it's what makes
        stopping mid-pass safe for the student: the teacher only ever
        walks away with what they've actually earned one class at a time
        (coins_released), never with days nobody taught."""
        return max(int(self.coins_spent) - int(self.coins_released), 0)

    @property
    def referral_total_amount(self) -> int:
        """Lifetime commission cap for this purchase's referral — the same
        proportion of coins_spent that referral_per_day_rate is of
        per_day_rate, so the referrer's total never exceeds
        referral_commission_percent of what the student actually paid,
        no matter how many days end up being charged."""
        if not self.referred_by_id or not self.referral_commission_percent:
            return 0
        return int(
            (Decimal(self.coins_spent) * self.referral_commission_percent / 100)
            .to_integral_value(rounding=ROUND_HALF_UP)
        )

    @property
    def referral_remaining_balance(self) -> int:
        """Mirrors remaining_balance, but for the referral commission
        escrow — how much is still left to release to the referrer."""
        return max(self.referral_total_amount - int(self.referral_coins_released), 0)

    def charge_for_session(self, session: "ClassSession") -> "PassDailyCharge | None":
        """Release this purchase's per-day charge to the teacher for the
        calendar date `session` (a COMPLETED session of this purchase's
        own classroom) falls on — but only once per date, and only if
        that date is within this purchase's validity window and there's
        still balance left to release.

        Idempotent by design: PassDailyCharge has a unique (purchase,
        date) constraint, so calling this twice for the same date (e.g.
        the post_save signal firing again on an unrelated field update
        while status is already COMPLETED, or sync_missed_charges()
        overlapping the signal) safely no-ops the second time via
        get_or_create rather than double-charging.

        Must be called with this purchase already row-locked
        (select_for_update) by the caller, inside transaction.atomic() —
        see _charge_passes_for_completed_session below.
        """
        if self.status != self.Status.SUCCESS or not self.is_active:
            return None
        if self.class_pass.classroom_id != session.classroom_id:
            return None

        charge_date = timezone.localtime(session.actual_end or session.scheduled_start).date()
        purchase_start = timezone.localtime(self.purchased_at).date()
        purchase_end = timezone.localtime(self.expires_at).date()
        if not (purchase_start <= charge_date <= purchase_end):
            return None  # session fell outside this pass's validity window

        remaining = self.remaining_balance
        if remaining <= 0:
            return None  # fully released already — nothing left in escrow

        charge, created = PassDailyCharge.objects.get_or_create(
            purchase=self,
            date=charge_date,
            defaults={
                "session": session,
                # Last chargeable day may leave a fractional per_day_rate
                # remainder (validity_days doesn't divide coins_spent
                # evenly) — hand the escrow's full remainder to the
                # teacher on that final day instead of stranding a coin or
                # two in limbo forever.
                "amount": min(
                    int(self.per_day_rate.to_integral_value(rounding="ROUND_HALF_UP")) or 1,
                    remaining,
                ),
            },
        )
        if not created:
            return charge  # already charged for this date — no-op

        # -------------------------------------------------------------
        # FEATURE (refer & earn — UPDATE: teacher-funded commission, not
        # platform-funded): the referrer's cut is now computed FIRST and
        # taken OUT of charge.amount before the teacher is paid — the
        # platform never tops this up out of its own pocket. The teacher
        # is the one who opted into referral_enabled and chose
        # referral_commission_percent for their own classroom (see
        # Classroom.referral_enabled/referral_commission_percent — teacher-
        # only, gated by ClassroomViewSet.perform_update), so it's the
        # teacher's own escrow release that funds the commission, same as
        # any real-world referral/affiliate arrangement a business owner
        # sets up out of their own margin.
        # Capped so a teacher's day can never go negative: referral_amount
        # can take at most charge.amount, even if referral_per_day_rate
        # would technically allow more (can only happen from a rounding
        # edge on the very last, fractional-remainder day — see the
        # get_or_create defaults above).
        # -------------------------------------------------------------
        referral_amount = 0
        if self.referred_by_id and self.referral_remaining_balance > 0:
            referral_amount = min(
                int(self.referral_per_day_rate.to_integral_value(rounding="ROUND_HALF_UP")),
                self.referral_remaining_balance,
                charge.amount,
            )

        teacher_amount = charge.amount - referral_amount

        teacher = type(self.class_pass.classroom.teacher).objects.select_for_update().get(
            pk=self.class_pass.classroom.teacher_id
        )
        if teacher_amount > 0:
            teacher.coin += teacher_amount
            teacher.save(update_fields=["coin"])
            CoinTransaction.objects.create(
                user=teacher,
                txn_type=CoinTransaction.TxnType.CREDIT,
                reason=CoinTransaction.Reason.CLASS_EARNING,
                amount=teacher_amount,
                balance_after=teacher.coin,
                reference_id=f"passpurchase:{self.id}:day:{charge_date.isoformat()}",
            )

        self.coins_released = min(self.coins_released + charge.amount, self.coins_spent)
        self.last_charge_date = charge_date
        update_fields = ["coins_released", "last_charge_date"]

        if referral_amount > 0:
            referrer = type(self.referred_by).objects.select_for_update().get(pk=self.referred_by_id)
            referrer.coin += referral_amount
            referrer.save(update_fields=["coin"])
            CoinTransaction.objects.create(
                user=referrer,
                txn_type=CoinTransaction.TxnType.CREDIT,
                reason=CoinTransaction.Reason.CLASS_REFERRAL_COMMISSION,
                amount=referral_amount,
                balance_after=referrer.coin,
                reference_id=f"passpurchase:{self.id}:referral:day:{charge_date.isoformat()}",
            )
            self.referral_coins_released = min(
                self.referral_coins_released + referral_amount, self.referral_total_amount
            )
            update_fields.append("referral_coins_released")
            charge.referral_amount = referral_amount
            charge.save(update_fields=["referral_amount"])

        self.save(update_fields=update_fields)
        return charge

    def sync_missed_charges(self) -> int:
        """Catch-up sweep: charge this purchase for every COMPLETED
        session of its classroom that falls inside its validity window
        and hasn't been charged yet. The post_save signal on ClassSession
        (below) is what normally does this in real time the moment a
        class ends; this exists as a safety net for whatever that signal
        might have missed (a stuck task, a session marked COMPLETED by
        some other path), and is called opportunistically whenever a
        student actually shows up (see ClassSessionViewSet.join in
        views.py) — so from the student's side, the pass only ever loses
        money for days a class actually happened, whether they were there
        to see it happen live or not. Returns how many new charges were
        created.
        """
        if self.remaining_balance <= 0:
            return 0
        sessions = ClassSession.objects.filter(
            classroom_id=self.class_pass.classroom_id,
            status=ClassSession.Status.COMPLETED,
        ).order_by("scheduled_start")
        charged = 0
        for session in sessions:
            with transaction.atomic():
                locked = PassPurchase.objects.select_for_update().get(pk=self.pk)
                result = locked.charge_for_session(session)
            if result is not None:
                charged += 1
            self.refresh_from_db(fields=["coins_released", "last_charge_date", "status", "is_active"])
            if self.remaining_balance <= 0:
                break
        return charged

    def reverse(self, notify: bool = True) -> None:
        """Cancel this pass and refund whatever's LEFT in escrow
        (remaining_balance) back to the student — never the full
        coins_spent, since coins_released has already legitimately gone
        to the teacher for classes they actually held, and never more
        than what's left to give back. This is what both the teacher
        (closing a classroom / refunding one student — see
        ClassroomViewSet.close and PassPurchaseViewSet.refund) and the
        student themselves (PassPurchaseViewSet.cancel — a student no
        longer has to just keep paying for a pass they don't want to
        finish using) end up calling. No teacher clawback needed at all:
        unlike the old lump-sum design, the teacher was never given money
        for classes that hadn't happened yet, so there's nothing to claw
        back.

        Caller must already hold a row lock on self (select_for_update)
        and run inside transaction.atomic() — same contract the old
        module-level _refund_purchase() in views.py used to have; this
        method replaces it.
        """
        refund = self.remaining_balance
        if refund > 0:
            student = type(self.student).objects.select_for_update().get(pk=self.student_id)
            student.coin += refund
            student.save(update_fields=["coin"])
            CoinTransaction.objects.create(
                user=student,
                txn_type=CoinTransaction.TxnType.CREDIT,
                reason=CoinTransaction.Reason.REFUND,
                amount=refund,
                balance_after=student.coin,
                reference_id=f"passpurchase:{self.id}",
            )

        self.status = self.Status.REFUNDED
        self.is_active = False
        self.refunded_amount = refund
        self.refunded_at = timezone.now()
        self.save(update_fields=["status", "is_active", "refunded_amount", "refunded_at"])

        # NOTE (fix — coupon slot permanently burned by an unused pass):
        # _charge_and_create_purchase() increments Coupon.used_count the
        # moment a purchase is created, but nothing ever gave that slot
        # back on reverse(). A student could buy with a limited coupon and
        # cancel a second later, permanently eating one redemption of a
        # coupon that never actually paid the teacher anything — squeezing
        # out a genuine student later.
        #
        # Only give the slot back if coins_released is still 0 — i.e. this
        # purchase never survived to see even one taught day charged
        # against it. Once even a single day has been released to the
        # teacher, the coupon did its job (discounted a real transaction
        # that paid out), so its use stays counted even if the student
        # cancels the remainder later — same principle reverse() already
        # applies to coins_released itself (earned money is never clawed
        # back on cancel).
        if self.coupon_id and self.coins_released == 0:
            Coupon.objects.filter(pk=self.coupon_id).update(
                used_count=Greatest(F("used_count") - 1, 0)
            )

        if notify:
            try:
                from .tasks import notify_purchase_refunded

                notify_purchase_refunded.delay(self.id)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Failed to queue refund notification for purchase %s", self.id
                )

    def renew(self) -> "PassPurchase | None":
        """NEW (Pass 15 — auto-renew passes). Buys a fresh PassPurchase for
        the exact same (student, class_pass) this one was for — meant to be
        called once this purchase has actually expired (or is about to),
        by a scheduled sweep (see the auto_renew field's docstring above).

        Deliberately mirrors _charge_and_create_purchase() in views.py
        (same coin-debit-then-create shape, same per_day_rate math) rather
        than importing it, so models.py never has to import from views.py
        — but WITHOUT coupon/referral carry-over: a renewal is a fresh
        subscription cycle at the class_pass's current plain price, same
        "coupons are a one-time self-purchase discount, not a standing
        entitlement" reasoning PassGift already applies to gifting.

        Returns the new PassPurchase on success. On failure (insufficient
        coins, or the classroom/pass having gone inactive since), clears
        auto_renew on THIS purchase (so the sweep doesn't keep retrying a
        subscription that can't renew), stamps renewal_failed_at, and
        returns None — never raises, since a scheduled sweep processing
        many purchases can't let one student's empty wallet blow up the
        whole run.

        Caller does not need to pre-lock self — this method takes its own
        row lock internally, same contract as charge_for_session/reverse
        expect of THEIR callers, just self-contained here since renew()
        is normally invoked standalone per-purchase by the sweep rather
        than nested inside a larger already-locked transaction.
        """
        class_pass = self.class_pass
        student = self.student

        def _fail() -> None:
            PassPurchase.objects.filter(pk=self.pk).update(
                auto_renew=False, renewal_failed_at=timezone.now()
            )
            try:
                from .tasks import notify_auto_renew_failed

                notify_auto_renew_failed.delay(self.id)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Failed to queue auto-renew-failed notification for purchase %s", self.id
                )

        if not class_pass.is_active or not class_pass.classroom.is_active or class_pass.classroom.is_deleted:
            _fail()
            return None

        coins_spent = int(class_pass.price.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

        try:
            with transaction.atomic():
                if coins_spent > 0:
                    locked_student = type(student).objects.select_for_update().get(pk=student.pk)
                    if locked_student.coin < coins_spent:
                        raise ValidationError("Insufficient coin balance for renewal.")
                    locked_student.coin -= coins_spent
                    locked_student.save(update_fields=["coin"])
                    CoinTransaction.objects.create(
                        user=locked_student,
                        txn_type=CoinTransaction.TxnType.DEBIT,
                        reason=CoinTransaction.Reason.PASS_AUTO_RENEWED,
                        amount=coins_spent,
                        balance_after=locked_student.coin,
                        reference_id=f"class_pass:{class_pass.id}:renewal_of:{self.id}",
                    )
                    payment_method = PassPurchase.PaymentMethod.COIN_WALLET
                else:
                    payment_method = PassPurchase.PaymentMethod.FREE

                per_day_rate = Decimal(coins_spent) / class_pass.validity_days
                new_purchase = PassPurchase.objects.create(
                    student=student,
                    class_pass=class_pass,
                    payment_method=payment_method,
                    amount_paid=Decimal(coins_spent),
                    coins_spent=coins_spent,
                    per_day_rate=per_day_rate,
                    status=PassPurchase.Status.SUCCESS,
                    expires_at=timezone.now() + timezone.timedelta(days=class_pass.validity_days),
                    auto_renew=True,  # subscription continues by default; student can turn it off again
                    renewed_from=self,
                )
        except (ValidationError, DjangoIntegrityError):
            _fail()
            return None

        # This purchase's own cycle is over — leave its escrow/coins_released
        # history exactly as it is (audit trail), just stop it being picked
        # up by the sweep again (only the newest link in the chain has
        # auto_renew=True).
        PassPurchase.objects.filter(pk=self.pk).update(auto_renew=False)

        try:
            from .tasks import notify_pass_auto_renewed

            notify_pass_auto_renewed.delay(new_purchase.id)
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to queue auto-renew notification for purchase %s", new_purchase.id
            )

        return new_purchase


# ---------------------------------------------------------------------------
# 5A2. PASS DAILY CHARGE (audit trail — one row per calendar day a pass was
#      actually charged, i.e. per day this classroom held a class while the
#      pass was valid; see PassPurchase.charge_for_session above)
# ---------------------------------------------------------------------------
class PassDailyCharge(models.Model):
    """Ledger row proving exactly which day a PassPurchase's escrow was
    tapped and why (linked back to the ClassSession that triggered it).
    The unique (purchase, date) constraint is what makes the whole
    per-day-charging feature idempotent and safe to re-run — it's the
    actual guard against a class somehow charging the same pass twice for
    the same day, not just a nice-to-have audit log."""

    purchase = models.ForeignKey(PassPurchase, on_delete=models.CASCADE, related_name="daily_charges")
    session = models.ForeignKey(
        ClassSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="pass_charges"
    )
    date = models.DateField()
    amount = models.PositiveIntegerField()
    # FEATURE (refer & earn): commission paid to purchase.referred_by for
    # this same date, alongside `amount` going to the teacher — 0 when the
    # purchase wasn't referred, or once its referral commission is fully
    # released. Kept on this same ledger row rather than a parallel table
    # since it's the same trigger, same date, same idempotency guard.
    referral_amount = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["purchase", "date"], name="one_charge_per_purchase_per_day")
        ]

    def __str__(self):
        return f"{self.purchase} - {self.date} ({self.amount} coin)"


# ---------------------------------------------------------------------------
# 5A3. GIFTING A PASS (Pass 14) — one platform user pays for a pass and
# hands it to another platform user, instead of buying it for themselves.
# ---------------------------------------------------------------------------
class PassGift(models.Model):
    """A gift is its own row, separate from PassPurchase, because the coin
    debit and the actual pass grant happen at DIFFERENT times: the gifter
    pays the moment they send the gift (so they can't back out after the
    recipient already sees it), but the PassPurchase (and its escrow
    clock — validity_days, per_day_rate, etc.) is only created once the
    recipient actually claims it — same "don't start a clock the
    recipient hasn't agreed to yet" reasoning as a gift card not
    activating until redeemed. See `_create_gift`/`claim`/`cancel` on
    PassGiftViewSet in views.py for the coin-flow details.

    SCOPING NOTE: a gift does not stack with a coupon or a referral link
    — the gifter pays the class_pass's plain price in full, and the
    resulting PassPurchase (once claimed) has no coupon/referred_by set.
    Coupons/referrals are a self-purchase discount/commission mechanic;
    mixing them into gifting would mean deciding whose referral credit
    a gift counts against (the gifter's or the recipient's), which is a
    product decision this pass deliberately leaves for later rather than
    guessing.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Claim"
        CLAIMED = "claimed", "Claimed"
        CANCELLED = "cancelled", "Cancelled By Gifter"
        EXPIRED = "expired", "Expired Unclaimed"

    # How long an unclaimed gift stays open before the recipient can no
    # longer claim it (and tasks.expire_unclaimed_gifts sweeps it back to
    # the gifter) — used by PassGiftViewSet.perform_create to stamp
    # expires_at. Kept as a class constant (not a settings.py value) since
    # it's a product decision about this one flow, not deployment config.
    CLAIM_WINDOW_DAYS = 7

    gifter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="passes_gifted")
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="passes_gifted_to_me")
    class_pass = models.ForeignKey(ClassPass, on_delete=models.CASCADE, related_name="gifts")

    coins_spent = models.PositiveIntegerField(help_text="Frozen at gift time — the class_pass's price then.")
    gift_message = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # Resulting purchase, once claimed — null until then. SET_NULL so a
    # later purchase deletion (there isn't one today, but defensively)
    # can never cascade-delete the gift's own audit trail.
    purchase = models.ForeignKey(
        PassPurchase, on_delete=models.SET_NULL, null=True, blank=True, related_name="from_gift"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    # Unclaimed gifts don't sit open forever — same "escrowed coin, not
    # free money" reasoning as PassPurchase itself: the gifter's coins are
    # already debited (see PassGiftViewSet.create), so an abandoned gift
    # needs a claim deadline and a refund path, not an indefinite hold.
    expires_at = models.DateTimeField()
    claimed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "status"]), models.Index(fields=["status", "expires_at"])]

    def __str__(self):
        return f"{self.gifter} -> {self.recipient}: {self.class_pass} ({self.get_status_display()})"

    def refund_to_gifter(self) -> None:
        """Give the gifter their coins back — used by both cancel() (the
        gifter changes their mind before the recipient claims) and the
        expiry sweep (tasks.expire_unclaimed_gifts). Caller must hold a
        row lock on self and run inside transaction.atomic(), same
        contract as PassPurchase.reverse()."""
        gifter = type(self.gifter).objects.select_for_update().get(pk=self.gifter_id)
        gifter.coin += self.coins_spent
        gifter.save(update_fields=["coin"])
        CoinTransaction.objects.create(
            user=gifter,
            txn_type=CoinTransaction.TxnType.CREDIT,
            reason=CoinTransaction.Reason.GIFT_CANCELLED_REFUND,
            amount=self.coins_spent,
            balance_after=gifter.coin,
            reference_id=f"passgift:{self.id}",
        )


# ---------------------------------------------------------------------------
# 5B. CLASS JOIN REQUEST (student's request to enroll -> teacher approval
#     gate -> ONLY on acceptance is a PassPurchase created and coins charged)
# ---------------------------------------------------------------------------
class ClassJoinRequest(models.Model):
    """The only door into a classroom for a non-owner/non-staff user.

    A student never buys a pass directly. They raise a request against a
    specific ClassPass; the classroom's teacher/co-teacher/moderator then
    accepts or rejects it. Coins are debited and the PassPurchase row (the
    thing Classroom.has_access() actually checks) is created ONLY inside
    the accept() action — so nobody is charged, and nobody becomes a
    participant, without the teacher's say-so first.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="join_requests")
    class_pass = models.ForeignKey(ClassPass, on_delete=models.CASCADE, related_name="join_requests")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="classroom_join_requests")

    coupon_code = models.CharField(max_length=30, blank=True)
    message = models.CharField(
        max_length=255, blank=True, help_text="Optional note from the student to the teacher."
    )

    # FEATURE (refer & earn): who the student's ?ref= code resolved to when
    # they raised this request — see ClassJoinRequestViewSet.perform_create,
    # which decodes it via referral_code_to_user_id() and only sets this at
    # all when the classroom had referral_enabled=True at request time.
    # Carried onto the resulting PassPurchase (see referred_by there) by
    # accept() so the actual per-day commission has something to key off;
    # SET_NULL rather than CASCADE so a referrer's account being deleted
    # later doesn't take the join-request/purchase history down with it.
    referred_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="referred_join_requests",
    )

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    # Set only when status becomes ACCEPTED — links straight to the
    # PassPurchase that accept() created, so the frontend/audit trail can
    # trace exactly which purchase this request resulted in.
    pass_purchase = models.OneToOneField(
        PassPurchase, on_delete=models.SET_NULL, null=True, blank=True, related_name="join_request"
    )
    decided_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="join_requests_decided"
    )
    decision_note = models.CharField(max_length=255, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    requested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["classroom", "status"]),
            models.Index(fields=["student", "status"]),
        ]
        constraints = [
            # A student can only ever have ONE pending request open against a
            # given classroom at a time — stops them from spamming requests
            # (or racing two accepts) while one is already awaiting a decision.
            models.UniqueConstraint(
                fields=["classroom", "student"],
                condition=models.Q(status="pending"),
                name="one_pending_join_request_per_student_per_classroom",
            )
        ]

    def __str__(self):
        return f"{self.student} -> {self.classroom.title} ({self.get_status_display()})"


# ---------------------------------------------------------------------------
# 6B. BREAKOUT ROOM (host splits a live session into smaller numbered rooms)
#
# FEATURE (backend for live_session_screen.dart's breakout-rooms UI): the
# Flutter side already assumed this exact shape (see that file's header
# comment) — this is the Django side of it. A BreakoutRoom row only exists
# while a breakout is actually running; ClassSessionViewSet.breakout_close
# deletes every row for the session to return everyone to the main room in
# one shot (SessionParticipant.breakout_room below is SET_NULL, so that
# single delete also clears every participant's assignment for free).
# ---------------------------------------------------------------------------
class BreakoutRoom(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="breakout_rooms")
    room_number = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["room_number"]
        unique_together = ("session", "room_number")

    def __str__(self):
        return f"Room {self.room_number} — {self.session}"


# ---------------------------------------------------------------------------
# 6. SESSION PARTICIPANT (attendance / who joined which live session)
# ---------------------------------------------------------------------------
class SessionParticipant(models.Model):
    class Role(models.TextChoices):
        HOST = "host", "Host/Teacher"
        STUDENT = "student", "Student"

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="session_participations")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STUDENT)

    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    # NOTE (fix — moderation had no teeth): kick() in views.py used to only
    # remove the participant from the LiveKit room + mark left_at — nothing
    # stopped that same user from calling /join/ or /token/ again a second
    # later and walking straight back into the room. A teacher removing a
    # disruptive student had no way to actually keep them out. Set once by
    # kick(); checked by join()/token() to refuse re-entry to THIS session
    # (doesn't affect other sessions of the same classroom/schedule).
    kicked_at = models.DateTimeField(null=True, blank=True)

    # FEATURE: hand-raise. The only "I have something to say right now"
    # signal in the whole app used to be the async ClassQuery (a written
    # doubt, meant to be answered whenever) — nothing captured the live,
    # in-the-moment "wait, let me ask" gesture every video-call app has.
    # Set/cleared by the student themselves (sessions/{id}/hand/), or
    # cleared by the host after acknowledging
    # (sessions/{id}/hand/{user_id}/lower/). Null = hand down; a timestamp
    # both marks "raised" and gives the host a natural raised-longest-first
    # ordering for the queue.
    hand_raised_at = models.DateTimeField(null=True, blank=True)

    # FEATURE: breakout rooms. Null = currently in the main room (either no
    # breakout is running at all, or this participant hasn't been assigned
    # to a sub-room yet). Set/cleared by
    # ClassSessionViewSet.breakout_assign; also cleared in bulk whenever the
    # BreakoutRoom it points to is deleted (breakout_close, or the session
    # itself being torn down) since that's on_delete=SET_NULL.
    breakout_room = models.ForeignKey(
        BreakoutRoom, on_delete=models.SET_NULL, null=True, blank=True, related_name="participants"
    )

    class Meta:
        unique_together = ("session", "user", "joined_at")
        ordering = ["joined_at"]

    def __str__(self):
        return f"{self.user} in {self.session} ({self.role})"


# ---------------------------------------------------------------------------
# 7. CLASS MATERIALS (notes, PPT/PDF, links shared by teacher)
# ---------------------------------------------------------------------------
class ClassMaterial(models.Model):
    class MaterialType(models.TextChoices):
        PDF = "pdf", "PDF"
        PPT = "ppt", "Presentation"
        DOC = "doc", "Document"
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        LINK = "link", "External Link"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="materials")
    session = models.ForeignKey(
        ClassSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="materials"
    )
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="uploaded_materials")

    title = models.CharField(max_length=150)
    material_type = models.CharField(max_length=10, choices=MaterialType.choices)
    file = models.FileField(
        upload_to="class_materials/", null=True, blank=True,
        validators=[MaxFileSizeValidator(100), FileExtensionValidator(DOCUMENT_MEDIA_EXTENSIONS)],
    )
    external_link = models.URLField(blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.title} ({self.classroom.title})"


# ---------------------------------------------------------------------------
# 8. LIVE CHAT (persisted messages during a session)
# ---------------------------------------------------------------------------
class ChatMessage(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="chat_messages")
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_messages")

    message = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)  # soft-delete for moderation

    # NEW (Pass 13) — pin a chat message/announcement so it doesn't get
    # lost in scroll. Deliberately ONE pinned message per session, not a
    # list: `ChatMessageViewSet.pin()` unpins any previously-pinned
    # message for the same session in the same call, so `is_pinned=True`
    # is never ambiguous about which one is "the" pinned message — a
    # `unique_together`/partial-unique-index couldn't express "at most
    # one True per session" portably across DB backends, so this is
    # enforced in the view instead (see its docstring).
    is_pinned = models.BooleanField(default=False)
    pinned_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="pinned_chat_messages"
    )
    pinned_at = models.DateTimeField(null=True, blank=True)

    # NEW (Pass 14 — chat safety: reports + profanity filter). See
    # moderation.py's `screen_message()` for the actual word-list check —
    # kept out of this file so the model stays free of moderation policy.
    # `is_flagged` is set automatically the moment a message trips the
    # filter (ChatMessageViewSet.perform_create) OR the moment a report
    # against it is filed (ChatMessageReportViewSet.create, see below) —
    # either path makes the message show up in a moderator's flagged-chat
    # queue without a moderator having to know which trigger fired.
    is_flagged = models.BooleanField(default=False)
    flagged_reason = models.CharField(max_length=100, blank=True)

    # NEW (reply feature) — one-level "reply/quote" reference, same shape as
    # WhatsApp's reply-to rather than a full nested-thread tree: a message
    # can quote exactly one earlier message in the SAME session (enforced in
    # ChatMessageSerializer.validate, since that needs the in-flight
    # `session` value from attrs, not just `self.instance`). SET_NULL (not
    # CASCADE) so deleting/soft-deleting the original doesn't take the reply
    # down with it — the quote just renders as "original message deleted"
    # (see ChatMessageSerializer.get_reply_to_detail). `related_name="replies"`
    # lets a message look up everything that quoted it, if that's ever needed
    # for a "view thread" UI, without a separate join table.
    reply_to = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies"
    )

    class Meta:
        ordering = ["sent_at"]
        indexes = [models.Index(fields=["session", "sent_at"]), models.Index(fields=["is_flagged"])]

    def __str__(self):
        return f"{self.sender}: {self.message[:30]}"


# ---------------------------------------------------------------------------
# NEW (Pass 14 — per-message chat reports). One row per (message, reporter)
# report — a student flagging one message as abusive/spam/off-topic for a
# moderator to review, same "queue + review action" shape as
# ClassroomReport (see ClassroomReportViewSet.review in views.py), just
# scoped to a single chat message instead of a whole classroom.
# ---------------------------------------------------------------------------
class ChatMessageReport(models.Model):
    class Reason(models.TextChoices):
        ABUSIVE = "abusive", "Abusive / Harassment"
        SPAM = "spam", "Spam / Advertising"
        OFF_TOPIC = "off_topic", "Off-topic"
        INAPPROPRIATE = "inappropriate", "Inappropriate Content"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIONED = "actioned", "Actioned"  # moderator deleted the message / warned the sender
        DISMISSED = "dismissed", "Dismissed"

    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_reports_filed")
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.OTHER)
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="chat_reports_reviewed"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # One report per (message, reporter) — same "can't spam-report the
        # same message to inflate the queue" reasoning as ChatReaction's
        # unique_together above, re-filing is a PATCH-style upsert, not a
        # new row (see ChatMessageReportViewSet.create).
        unique_together = ("message", "reporter")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"Report on message {self.message_id} by {self.reporter} ({self.get_reason_display()})"


class ChatReaction(models.Model):
    """NEW (Pass 12) — lightweight emoji reaction on a chat message. Kept
    deliberately separate from ChatMessage itself (not e.g. a JSONField
    counter on the message row) so a reaction can be added/changed/removed
    without ever touching — or needing a lock on — the message row, and so
    `unique_together` can cheaply enforce "one reaction per user per
    message" at the DB level instead of in application code.

    ONE reaction per (message, user), upsertable — matches the same
    "vote can be changed" shape as PollResponse/LivePollViewSet.vote()
    above (update_or_create), not a Slack-style "many different emoji per
    user" model. This app's chat is a lightweight engagement signal, not
    a full reaction system — deliberately the simplest version that still
    lets a student's tap reflect their latest reaction rather than
    silently failing on the unique constraint or stacking duplicates.
    """

    class Reaction(models.TextChoices):
        THUMBS_UP = "thumbs_up", "👍"
        HEART = "heart", "❤️"
        LAUGH = "laugh", "😂"

    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_reactions")
    reaction = models.CharField(max_length=10, choices=Reaction.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("message", "user")
        indexes = [models.Index(fields=["message"])]

    def __str__(self):
        return f"{self.user} reacted {self.reaction} to message {self.message_id}"


class ChatMessageRead(models.Model):
    """NEW (read receipts) — WhatsApp-style "seen by" for live-session chat.

    Deliberately its own row-per-(message, user) table, same reasoning as
    `ChatReaction` right above: a receipt can be recorded without touching or
    locking the `ChatMessage` row itself, and `unique_together` gives us "one
    receipt per user per message" for free at the DB level instead of an
    application-level check-then-write race.

    This is distinct from `SessionReadState.last_read_chat_message_id`
    elsewhere in this file: that field is a single per-user *watermark* used
    only to drive the unread-count BADGE on the sessions list (see
    `ClassSessionViewSet.unread_counts`/`mark_read`) — it has no idea who
    else has read a given message. This table is the other direction: given
    ONE message, who (and when) has actually seen it — what a sender taps a
    message to check, same as WhatsApp/Slack read receipts. The two features
    share the same underlying "user has seen up to here" idea but serve
    different UI (a badge vs. a per-message seen-by list) and are kept as
    separate tables so neither has to shoehorn the other's access pattern.

    Append/upsert-only — a read receipt never gets un-set (you can't
    "un-read" a message), so unlike `ChatReaction` there is no DELETE path.
    """

    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_message_reads")
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("message", "user")
        indexes = [models.Index(fields=["message"])]
        ordering = ["read_at"]

    def __str__(self):
        return f"{self.user} read message {self.message_id} at {self.read_at}"


class SessionReaction(models.Model):
    """NEW (persistence fix) — durable log for the in-session emoji
    reactions (👍❤️😂👏🎉🙌) fired from live_session_screen.dart's
    `_sendReaction`/`_addFloatingReaction`. Previously a PURE LiveKit
    data-channel broadcast (see that file's `_kSignalTopic` signaling) —
    nothing was ever written to the database, so the moment every
    participant left the room the entire reaction history for that
    session was gone, and a client reconnecting mid-session started its
    running `_reactionTotalCount` badge back at zero even though the
    class had already reacted a hundred times.

    Deliberately an APPEND-ONLY log (one row per tap), not a per-user
    upsert like `ChatReaction` above — the point of this feature is the
    burst/stream over time (and a running total), not "what is each
    user's current reaction", so there's no `unique_together` here.
    Live delivery to already-connected peers is UNCHANGED (still the
    LiveKit data channel, for lowest latency) — this table is the
    missing persistence layer underneath it, written by
    `ClassSessionViewSet.reactions()` (POST) in views.py alongside the
    existing data-channel broadcast, not instead of it.
    """

    class Reaction(models.TextChoices):
        THUMBS_UP = "thumbs_up", "👍"
        HEART = "heart", "❤️"
        LAUGH = "laugh", "😂"
        CLAP = "clap", "👏"
        PARTY = "party", "🎉"
        RAISED_HANDS = "raised_hands", "🙌"

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="reaction_log")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="session_reactions")
    reaction = models.CharField(max_length=20, choices=Reaction.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["session", "created_at"])]

    def __str__(self):
        return f"{self.user} reacted {self.reaction} in {self.session}"


class SessionCaption(models.Model):
    """NEW (persistence fix) — durable transcript line for the LIVE
    caption feature (`speech_to_text` on-device STT, see
    live_session_screen.dart's `_startCaptionListenBurst`/
    `_addCaptionLine`). Each device still only ever recognizes ITS OWN
    mic — that on-device-STT limitation is unchanged and is documented
    at length in that file's header (no per-frame hook into remote
    participants' decoded audio in Flutter/LiveKit) — but every device
    already produces its own finished lines and tags them with the
    speaker's name before broadcasting over the LiveKit data channel.
    Previously that broadcast was the ONLY place a line ever went:
    nothing was persisted, the on-screen feed was capped to the last 3
    lines and self-expired after 6 seconds (see `_addCaptionLine`), and
    a participant who joined mid-session or reconnected saw nothing
    that was said before they arrived.

    This table is that missing persistence layer: every device's own
    recognized line is now ALSO POSTed to the server
    (`ClassSessionViewSet.captions()` POST) and kept as a row here,
    which (a) survives the session ending, and (b) lets a client fetch
    the transcript-so-far (`captions()` GET) — so even though the
    recognition itself is still per-own-voice, the aggregate transcript
    the server accumulates covers EVERY participant's own speech, not
    just whoever's LiveKit data-channel packet happened to reach a
    given listener live. A genuinely server-side, cross-participant
    live transcript would need real server-side STT on each
    participant's audio track (a much bigger, separate change — see the
    existing async whole-recording transcription path instead,
    `Classroom.captions_enabled` / `ClassSession.caption_url` /
    `tasks.transcribe_recording`, which already covers the mixed
    recording after the session ends).
    """

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="caption_log")
    speaker = models.ForeignKey(User, on_delete=models.CASCADE, related_name="session_captions")
    text = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["session", "created_at"])]

    def __str__(self):
        return f"{self.speaker}: {self.text[:40]}"


class SessionReadState(models.Model):
    """NEW (Pass 13) — one row per (session, user): the watermark this
    user has "seen up to" for a session's chat and polls. Deliberately
    ONE row covering both, not two separate models — "I reopened this
    session's tab" naturally clears both unread badges together, and a
    single upsert (see ClassSessionViewSet.mark_read below) is cheaper
    than two.

    NULL on either field means "never seen any" (not "seen the first
    one") — so `id > NULL` semantics need an explicit `is None` branch
    in the unread-count query rather than relying on SQL's own NULL
    comparison, which is always UNKNOWN/false and would otherwise (very
    subtly) undercount a first-ever visit as zero unread instead of
    "all of them".
    """

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="read_states")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="session_read_states")
    last_read_chat_message_id = models.PositiveIntegerField(null=True, blank=True)
    last_seen_poll_id = models.PositiveIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("session", "user")

    def __str__(self):
        return f"{self.user} read-state for {self.session}"


# ---------------------------------------------------------------------------
# 9. LIVE POLLS (teacher engagement tool during a session)
# ---------------------------------------------------------------------------
class LivePoll(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="polls")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_polls")

    question = models.CharField(max_length=255)
    options = models.JSONField(default=list)  # e.g. ["Option A", "Option B", "Option C"]

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.question


class PollResponse(models.Model):
    poll = models.ForeignKey(LivePoll, on_delete=models.CASCADE, related_name="responses")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="poll_responses")
    selected_option_index = models.PositiveSmallIntegerField()
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("poll", "student")

    def __str__(self):
        return f"{self.student} -> {self.poll} [{self.selected_option_index}]"


class PollTemplate(models.Model):
    """NEW (Pass 13) — quick-poll templates: a teacher who reuses the same
    "Samajh aaya? Yes/No"-style poll every session saves it once here and
    fires it in one tap via LivePollViewSet.quick_create() instead of
    retyping the question/options each time.

    Scoped to a classroom (not global/platform-wide) — a teacher's poll
    templates are their own classroom's shorthand, not a shared library
    across every classroom on the platform; `_can_manage_classroom` (the
    same boundary already used for Assignment/Notice/ClassHoliday) gates
    create/update/delete in PollTemplateViewSet.
    """

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="poll_templates")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_poll_templates")

    question = models.CharField(max_length=255)
    options = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.question} ({self.classroom.title})"


# ---------------------------------------------------------------------------
# 10. ASSIGNMENTS / HOMEWORK
# ---------------------------------------------------------------------------
class Assignment(models.Model):
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="assignments")
    session = models.ForeignKey(
        ClassSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="assignments"
    )

    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    attachment = models.FileField(
        upload_to="assignments/", null=True, blank=True,
        validators=[MaxFileSizeValidator(50), FileExtensionValidator(DOCUMENT_MEDIA_EXTENSIONS)],
    )

    due_date = models.DateTimeField()
    max_score = models.PositiveIntegerField(default=100)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date"]

    def __str__(self):
        return f"{self.title} ({self.classroom.title})"


class AssignmentSubmission(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="assignment_submissions")

    file = models.FileField(
        upload_to="assignment_submissions/",
        validators=[MaxFileSizeValidator(50), FileExtensionValidator(DOCUMENT_MEDIA_EXTENSIONS)],
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    score = models.PositiveIntegerField(null=True, blank=True)
    feedback = models.TextField(blank=True)
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("assignment", "student")
        ordering = ["-submitted_at"]

    def is_late(self) -> bool:
        return self.submitted_at > self.assignment.due_date

    def __str__(self):
        return f"{self.student} -> {self.assignment}"


# ---------------------------------------------------------------------------
# 11. RATINGS & REVIEWS (trust signal on a classroom)
# ---------------------------------------------------------------------------
class ClassroomReview(models.Model):
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="reviews")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="classroom_reviews")

    rating = models.PositiveSmallIntegerField(choices=[(i, str(i)) for i in range(1, 6)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("classroom", "student")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.classroom.title} - {self.rating}★ by {self.student}"


# ---------------------------------------------------------------------------
# 11B. WISHLIST ("save for later" — browsing interest, no payment/enrollment
# implied. Deliberately separate from PassPurchase: a student should be able
# to bookmark a classroom they're considering without committing coins,
# same UX pattern as Udemy/Byju's-style "save to wishlist".)
# ---------------------------------------------------------------------------
class ClassroomWishlist(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="wishlisted_classrooms")
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="wishlisted_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "classroom")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.user} \u2665 {self.classroom.title}"


# ---------------------------------------------------------------------------
# 11C. CLASSROOM SHARE (word-of-mouth: in-app + outside-the-app sharing)
#
# WHY THIS EXISTS: there was no "share this class" feature at all — a
# student who liked a classroom had no way to send it to a friend, whether
# that friend is already on the platform (in-app) or not (outside the app,
# via WhatsApp/SMS/email/copy-link). One row per share attempt, whatever
# the channel, so:
#   - a teacher can see how much word-of-mouth their classroom is getting
#     (see ClassroomViewSet.share-stats), broken down by channel
#   - shared_with (nullable) lets an in-app share also drive a Notification
#     to a specific platform user — see ClassroomViewSet.share in views.py
#   - it's a natural place to hang basic anti-spam (e.g. a per-user daily
#     share cap) later without redesigning anything, if that ever becomes
#     necessary
# ---------------------------------------------------------------------------
class ClassroomShare(models.Model):
    class Channel(models.TextChoices):
        IN_APP = "in_app", "In-App"
        WHATSAPP = "whatsapp", "WhatsApp"
        SMS = "sms", "SMS"
        EMAIL = "email", "Email"
        COPY_LINK = "copy_link", "Copy Link"
        OTHER = "other", "Other"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="shares")
    shared_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="classroom_shares_made")
    # Only set for an IN_APP share to a specific platform user — every
    # outside-the-app channel (WhatsApp/SMS/email/copy-link/other) hands
    # the link off to something outside this app entirely, so there's no
    # platform user row to point at.
    shared_with = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True, related_name="classroom_shares_received"
    )
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.OTHER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["classroom", "-created_at"]),
            models.Index(fields=["shared_by", "-created_at"]),
        ]

    def __str__(self):
        target = self.shared_with or self.get_channel_display()
        return f"{self.shared_by} shared {self.classroom.title} -> {target}"


# ---------------------------------------------------------------------------
# 12. COUPONS (discounts on ClassPass purchases)
# ---------------------------------------------------------------------------
class Coupon(models.Model):
    classroom = models.ForeignKey(
        Classroom, on_delete=models.CASCADE, related_name="coupons", null=True, blank=True
    )  # null = usable across all of this teacher's classrooms (set via created_by)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="coupons_created")

    code = models.CharField(max_length=30, unique=True)
    discount_percent = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    discount_amount = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )

    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField()
    max_uses = models.PositiveIntegerField(null=True, blank=True)  # null = unlimited
    used_count = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.code

    def is_valid(self) -> bool:
        now = timezone.now()
        if not self.is_active or not (self.valid_from <= now <= self.valid_until):
            return False
        if self.max_uses and self.used_count >= self.max_uses:
            return False
        return True


# ---------------------------------------------------------------------------
# 13. COIN WALLET TRANSACTIONS (ledger for User.coin balance)
# ---------------------------------------------------------------------------
class CoinTransaction(models.Model):
    class TxnType(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    class Reason(models.TextChoices):
        PASS_PURCHASE = "pass_purchase", "Pass Purchase"
        CLASS_EARNING = "class_earning", "Class Earning"
        REFERRAL_BONUS = "referral_bonus", "Referral Bonus"
        # FEATURE (refer & earn — class-level, see Classroom.referral_enabled
        # above): distinct from REFERRAL_BONUS, which is a flat one-time
        # payout for bringing a brand-new USER onto the platform (see the
        # Referral model, 13B below). This one is the ongoing, per-day cut
        # of a specific classroom's escrow release — see
        # PassPurchase.charge_for_session.
        CLASS_REFERRAL_COMMISSION = "class_referral_commission", "Class Referral Commission"
        REFUND = "refund", "Refund"
        # NEW (Pass 14 — gifting a pass): distinct from PASS_PURCHASE/REFUND
        # so a wallet-history screen can tell "I bought this for myself" and
        # "I bought this for someone else" apart, and so a recipient's
        # claimed gift never looks like a REFUND (they never paid anything).
        GIFT_SENT = "gift_sent", "Pass Gifted To Someone"
        GIFT_CANCELLED_REFUND = "gift_cancelled_refund", "Unclaimed Gift Refunded"
        # NEW (Pass 15 — auto-renew passes): distinct from PASS_PURCHASE so
        # a wallet-history screen can tell "I bought this" apart from "this
        # renewed itself" — see PassPurchase.renew() below.
        PASS_AUTO_RENEWED = "pass_auto_renewed", "Pass Auto-Renewed"
        ADMIN_ADJUSTMENT = "admin_adjustment", "Admin Adjustment"
        TOPUP = "topup", "Wallet Top-up"
        WITHDRAWAL = "withdrawal", "Withdrawal"  # debited the moment a CoinWithdrawal request is made
        WITHDRAWAL_REVERSED = "withdrawal_reversed", "Withdrawal Reversed"  # credited back on reject/cancel

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="coin_transactions")

    txn_type = models.CharField(max_length=10, choices=TxnType.choices)
    reason = models.CharField(max_length=32, choices=Reason.choices)
    amount = models.PositiveIntegerField()
    balance_after = models.PositiveIntegerField()

    reference_id = models.CharField(max_length=100, blank=True)  # e.g. PassPurchase id
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} {self.txn_type} {self.amount} ({self.reason})"


# ---------------------------------------------------------------------------
# 13A2. COIN PURCHASE (real-money -> coin top-up)
#
# GAP THIS CLOSES: CoinTransaction.Reason.TOPUP has existed as an enum
# choice since the ledger was first written, but nothing anywhere ever
# created one — there was no payment gateway integration, no order/verify
# flow, and critically no way to RETRY a failed top-up (a student whose
# payment failed had no recovery path except contacting support). This is
# that missing feature: a gateway-agnostic order -> verify -> credit flow,
# modeled closely on how Razorpay/similar Indian PGs work (order created
# server-side, client completes payment, gateway posts a signed
# confirmation back), but the actual `_verify_signature`/`_create_order`
# calls are provider stubs — wire in real Razorpay/Cashfree/etc. keys via
# settings before going live (see CoinPurchaseViewSet in views.py).
# ---------------------------------------------------------------------------
class CoinPurchase(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="coin_purchases")
    coins = models.PositiveIntegerField()
    amount_inr = models.DecimalField(max_digits=8, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    # Gateway-side identifiers. order_id is ours (created up front, always
    # present); gateway_payment_id/gateway_signature only get filled in once
    # the client actually completes payment and we verify it.
    order_id = models.CharField(max_length=100, unique=True)
    gateway_payment_id = models.CharField(max_length=100, blank=True)
    gateway_signature = models.CharField(max_length=255, blank=True)

    # NOTE: a FAILED purchase is never mutated back to PENDING/SUCCESS —
    # retrying creates a brand-new CoinPurchase row (new order_id) that
    # points back here, so the failure stays on the record instead of
    # being overwritten. See CoinPurchaseViewSet.retry in views.py.
    retry_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="retries"
    )
    failure_reason = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status", "-created_at"])]

    def __str__(self):
        return f"{self.user} - {self.coins} coins ({self.get_status_display()})"

    def mark_success(self, gateway_payment_id: str, gateway_signature: str) -> None:
        """Credits the wallet exactly once. Idempotent by construction: a
        gateway webhook can legitimately fire more than once for the same
        payment (retries on their side, or the client's own verify call
        racing a webhook) — only ever act if this row is still PENDING,
        inside a row lock, so a duplicate call is a safe no-op rather than
        a double-credit."""
        if self.status != self.Status.PENDING:
            return
        user = type(self.user).objects.select_for_update().get(pk=self.user_id)
        user.coin += self.coins
        user.save(update_fields=["coin"])
        CoinTransaction.objects.create(
            user=user,
            txn_type=CoinTransaction.TxnType.CREDIT,
            reason=CoinTransaction.Reason.TOPUP,
            amount=self.coins,
            balance_after=user.coin,
            reference_id=f"coinpurchase:{self.id}",
        )
        self.status = self.Status.SUCCESS
        self.gateway_payment_id = gateway_payment_id
        self.gateway_signature = gateway_signature
        self.verified_at = timezone.now()
        self.save(update_fields=["status", "gateway_payment_id", "gateway_signature", "verified_at"])

    def mark_failed(self, reason: str = "") -> None:
        if self.status != self.Status.PENDING:
            return
        self.status = self.Status.FAILED
        self.failure_reason = reason[:255]
        self.save(update_fields=["status", "failure_reason"])


# ---------------------------------------------------------------------------
# 13B. REFERRAL PROGRAM
#
# GAP THIS CLOSES: CoinTransaction.Reason.REFERRAL_BONUS has existed as an
# enum choice since the coin ledger was first written, but nothing anywhere
# ever created a transaction with that reason — there was no referral code,
# no redeem endpoint, no Referral model. This is that missing feature.
#
# Design: no new field needed on User (the `login` app's User model is out
# of scope for this app to migrate) — a referral code is just the user's id
# obfuscated well enough that it isn't a guessable sequential integer in the
# app's UI, decoded back to a user id on redeem. Not cryptographic secrecy
# (a determined person could brute-force it), just enough to stop a plain
# "?ref=124" counter from being trivially incremented/guessed by a casual
# user copy-pasting someone else's link.
# ---------------------------------------------------------------------------
_REFERRAL_CODE_SALT = 7919  # arbitrary fixed prime; only needs to be stable across restarts


def referral_code_for_user(user_id: int) -> str:
    """user id -> short shareable code, e.g. 42 -> 'R1B3'. Deterministic and
    reversible (see referral_code_to_user_id) — no DB column needed."""
    return "R" + format(user_id * _REFERRAL_CODE_SALT, "X")


def referral_code_to_user_id(code: str) -> "int | None":
    """Inverse of referral_code_for_user(). Returns None for a malformed
    code instead of raising, so callers can treat it as 'invalid code'."""
    if not code or not code.upper().startswith("R"):
        return None
    try:
        value = int(code[1:], 16)
    except ValueError:
        return None
    if value % _REFERRAL_CODE_SALT != 0:
        return None
    return value // _REFERRAL_CODE_SALT


class Referral(models.Model):
    """One row per successfully-redeemed referral. `referred` is a
    OneToOne, not a plain FK — a user can be referred (i.e. redeem a
    referral code as the NEW user) at most once ever, which is what
    prevents someone farming REFERRAL_BONUS by redeeming repeatedly.
    There's no matching cap on `referrer` — a single existing user is
    meant to refer many people."""

    referrer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="referrals_made")
    referred = models.OneToOneField(User, on_delete=models.CASCADE, related_name="referral_used")
    bonus_amount = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.referrer} referred {self.referred}"


# ---------------------------------------------------------------------------
# 13C. COIN WITHDRAWAL (cash-out of a real, earned coin balance to a bank
#      account / UPI id — the off-ramp this app was missing)
#
# GAP THIS CLOSES: CoinTransaction.Reason.CLASS_EARNING already credits a
# teacher's User.coin balance one calendar day at a time as classes actually
# happen (see PassPurchase.charge_for_session above), and REFERRAL_BONUS
# credits it too — but until now there was NO way for that balance to ever
# leave the platform as real money. A teacher could earn coins forever and
# never be able to withdraw them. This model + CoinWithdrawalViewSet (views.py)
# is that missing off-ramp. Coin TOP-UP (real money -> coins, i.e. buying
# coins) is deliberately OUT of scope here per product decision — this only
# covers coins -> real money, the direction that was actually missing.
#
# Design mirrors PassPurchase.reverse()'s pattern: debit eagerly at REQUEST
# time (so a user can't request the same coins twice while a request is
# pending, without needing a separate "coins on hold" bookkeeping field),
# refund on reject/cancel, no further coin movement on approve/mark_paid
# (the coins already left the wallet the moment the request was made — all
# that's left is the actual bank/UPI transfer, done OUTSIDE this app by
# finance/admin, then recorded here for audit).
# ---------------------------------------------------------------------------
class CoinWithdrawal(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"  # admin approved, payout not sent yet
        REJECTED = "rejected", "Rejected"  # coins refunded back to wallet
        PAID = "paid", "Paid"  # payout actually sent (external to this app)
        CANCELLED = "cancelled", "Cancelled"  # user cancelled their own request

    class PayoutMethod(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        UPI = "upi", "UPI"

    # Coins earned genuinely have a real INR value the moment they're
    # credited via CLASS_EARNING (they're what a student actually paid for a
    # pass) — COIN_TO_INR_RATE is the single conversion constant the whole
    # withdrawal flow (validation, serializer display, admin payout amount)
    # reads from, so it only ever needs to change in one place.
    COIN_TO_INR_RATE = 1  # 1 coin == this many INR; adjust to match the actual coin pricing used when passes are priced
    MIN_WITHDRAWAL_COINS = 100  # below this, a bank/UPI transfer typically costs more in fees than the payout itself

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="coin_withdrawals")

    coins = models.PositiveIntegerField()
    amount_inr = models.DecimalField(max_digits=10, decimal_places=2)  # coins * COIN_TO_INR_RATE, snapshotted at request time so a later rate change never rewrites history

    payout_method = models.CharField(max_length=20, choices=PayoutMethod.choices)
    # Bank: {"account_holder", "account_number", "ifsc"}. UPI: {"upi_id"}.
    # Validated by CoinWithdrawalSerializer against payout_method at request
    # time — kept as JSON (not separate columns) so adding a payout method
    # later (e.g. a wallet provider) never needs a migration.
    payout_details = models.JSONField()

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    admin_note = models.TextField(blank=True)  # rejection reason, or any payout note
    external_reference = models.CharField(max_length=100, blank=True)  # bank UTR / UPI txn id, filled in on mark_paid

    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="coin_withdrawals_reviewed"
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [models.Index(fields=["status", "requested_at"])]

    def __str__(self):
        return f"{self.user} withdraw {self.coins} coins ({self.status})"

    @classmethod
    def create_request(cls, user, coins: int, payout_method: str, payout_details: dict) -> "CoinWithdrawal":
        """The only supported way to create a withdrawal request — debits
        the coins from the wallet in the SAME transaction as creating the
        row, so the balance check and the debit can never race each other
        (two concurrent requests both reading a stale `user.coin` and both
        passing validation). Caller (the view) does the permission check;
        this does the money-moving + row-locking.
        """
        if coins < cls.MIN_WITHDRAWAL_COINS:
            raise ValidationError(
                f"Minimum withdrawal is {cls.MIN_WITHDRAWAL_COINS} coins."
            )
        with transaction.atomic():
            locked_user = type(user).objects.select_for_update().get(pk=user.pk)
            if locked_user.coin < coins:
                raise ValidationError(
                    f"Insufficient balance — you have {locked_user.coin} coins, requested {coins}."
                )
            locked_user.coin -= coins
            locked_user.save(update_fields=["coin"])
            CoinTransaction.objects.create(
                user=locked_user,
                txn_type=CoinTransaction.TxnType.DEBIT,
                reason=CoinTransaction.Reason.WITHDRAWAL,
                amount=coins,
                balance_after=locked_user.coin,
                reference_id="withdrawal:pending",  # backfilled to the real id right after creation, below
            )
            withdrawal = cls.objects.create(
                user=locked_user,
                coins=coins,
                amount_inr=coins * cls.COIN_TO_INR_RATE,
                payout_method=payout_method,
                payout_details=payout_details,
            )
            CoinTransaction.objects.filter(
                user=locked_user, reference_id="withdrawal:pending"
            ).order_by("-created_at").update(reference_id=f"withdrawal:{withdrawal.id}")
        return withdrawal

    def _refund_coins(self, reason_note: str) -> None:
        """Shared by reject() and cancel() — gives the debited coins back.
        Caller must already hold a row lock on self (select_for_update) and
        run inside transaction.atomic(), same contract as PassPurchase.reverse().
        """
        user = type(self.user).objects.select_for_update().get(pk=self.user_id)
        user.coin += self.coins
        user.save(update_fields=["coin"])
        CoinTransaction.objects.create(
            user=user,
            txn_type=CoinTransaction.TxnType.CREDIT,
            reason=CoinTransaction.Reason.WITHDRAWAL_REVERSED,
            amount=self.coins,
            balance_after=user.coin,
            reference_id=f"withdrawal:{self.id}",
        )
        if reason_note:
            self.admin_note = reason_note

    def approve(self, admin_user) -> None:
        """Marks intent to pay — no coin movement (already debited at
        request time). The actual transfer happens outside this app; call
        mark_paid() once it's done."""
        if self.status != self.Status.PENDING:
            raise ValidationError(f"This request is already {self.get_status_display().lower()}.")
        self.status = self.Status.APPROVED
        self.reviewed_by = admin_user
        self.reviewed_at = timezone.now()
        self.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    def reject(self, admin_user, reason: str) -> None:
        if self.status not in (self.Status.PENDING, self.Status.APPROVED):
            raise ValidationError(f"This request is already {self.get_status_display().lower()}.")
        self._refund_coins(reason)
        self.status = self.Status.REJECTED
        self.reviewed_by = admin_user
        self.reviewed_at = timezone.now()
        self.save(update_fields=["status", "admin_note", "reviewed_by", "reviewed_at"])

    def cancel(self) -> None:
        """User-initiated: only while still PENDING (once an admin has
        APPROVED it, a payout may already be in flight — cancel through
        support instead)."""
        if self.status != self.Status.PENDING:
            raise ValidationError(f"This request is already {self.get_status_display().lower()}.")
        self._refund_coins("Cancelled by user.")
        self.status = self.Status.CANCELLED
        self.save(update_fields=["status", "admin_note"])

    def mark_paid(self, admin_user, external_reference: str) -> None:
        if self.status != self.Status.APPROVED:
            raise ValidationError("Only an approved request can be marked paid.")
        self.status = self.Status.PAID
        self.external_reference = external_reference
        self.reviewed_by = admin_user
        self.paid_at = timezone.now()
        self.save(update_fields=["status", "external_reference", "reviewed_by", "paid_at"])


# ---------------------------------------------------------------------------
# 12B. CLASSROOM BAN (persistent, classroom-wide — distinct from
#      SessionParticipant.kicked_at, which only blocks re-entry to the ONE
#      session a moderator kicked someone from)
#
# GAP THIS CLOSES: ClassSessionViewSet.kick() (views.py) already stops a
# disruptive participant re-joining the SAME session, but nothing stopped
# them from joining the NEXT session of the same classroom, raising a fresh
# ClassJoinRequest after a refund, or continuing to sit in chat/materials/
# doubts if they still held an active pass. A teacher who wants someone
# permanently off their classroom (not just out of one session) had no way
# to do that short of manually refusing every future join request by hand.
# ---------------------------------------------------------------------------
class ClassroomBan(models.Model):
    """A teacher/co-teacher/moderator has permanently banned this student
    from this classroom. Checked by:
        - ClassJoinRequestViewSet.perform_create (views.py) — a banned
          student can't raise a new join request.
        - _perform_join (views.py) — a banned student can't join a live
          session even if they somehow still hold a valid pass.
        - Classroom.has_access() (below) — a banned student loses room
          access outright, same tier as an expired pass.

    Creating a ban (see ClassroomViewSet.ban action) also best-effort kicks
    the student from any session they're currently live in, cancels any
    pending join request, and reverses/refunds any active PassPurchase for
    this classroom — a ban should mean "gone", not "still holds a paid
    pass they can never use again"."""

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="bans")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="classroom_bans")
    banned_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="classroom_bans_issued"
    )
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("classroom", "student")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student} banned from {self.classroom.title}"


# ---------------------------------------------------------------------------
# 14. CO-TEACHERS / TEACHING ASSISTANTS (multi-staff classrooms)
# ---------------------------------------------------------------------------
class ClassroomStaff(models.Model):
    class Role(models.TextChoices):
        CO_TEACHER = "co_teacher", "Co-Teacher"
        MODERATOR = "moderator", "Moderator"
        TA = "ta", "Teaching Assistant"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="staff")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="staff_roles")
    role = models.CharField(max_length=15, choices=Role.choices, default=Role.TA)

    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("classroom", "user")

    def __str__(self):
        return f"{self.user} - {self.get_role_display()} @ {self.classroom.title}"


# ---------------------------------------------------------------------------
# 15. WAITLIST (session hits max_participants)
# ---------------------------------------------------------------------------
class SessionWaitlist(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="waitlist")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="waitlisted_sessions")

    joined_at = models.DateTimeField(auto_now_add=True)
    notified = models.BooleanField(default=False)  # notified when a seat opens up

    class Meta:
        unique_together = ("session", "student")
        ordering = ["joined_at"]  # first-come-first-served

    def __str__(self):
        return f"{self.student} waiting for {self.session}"


# ---------------------------------------------------------------------------
# 15B. CLASSROOM REPORTS (student flags a classroom — scam, abandoned,
#      inappropriate, etc. — for platform staff to review)
# ---------------------------------------------------------------------------
class ClassroomReport(models.Model):
    """A user-filed report against a classroom. This is the other half of
    the anti-abuse fix alongside Classroom.can_be_deleted()/
    ClassroomViewSet.close(): even before a fraudulent teacher tries to
    delete anything, students who feel scammed (class never happened,
    teacher stopped responding, etc.) have somewhere to flag it, and
    enough pending reports auto-hides the classroom from Explore (see
    _auto_flag_classroom below) while staff look into it."""

    class Reason(models.TextChoices):
        SCAM = "scam", "Scam / took coins, no class"
        NOT_DELIVERING = "not_delivering", "Classes not happening as promised"
        INAPPROPRIATE = "inappropriate", "Inappropriate content or behaviour"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        REVIEWED = "reviewed", "Reviewed"
        ACTION_TAKEN = "action_taken", "Action Taken"
        DISMISSED = "dismissed", "Dismissed"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="reports")
    reported_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="classroom_reports_filed")

    reason = models.CharField(max_length=20, choices=Reason.choices)
    description = models.TextField(blank=True)

    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="classroom_reports_reviewed"
    )
    admin_note = models.CharField(max_length=255, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    # A pending report auto-flags the classroom once enough distinct users
    # have filed one — see _auto_flag_classroom below.
    AUTO_FLAG_THRESHOLD = 3

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["classroom", "status"])]
        constraints = [
            # Stop one user from spamming the same classroom with repeat
            # reports — one open report per (user, classroom) at a time.
            models.UniqueConstraint(
                fields=["classroom", "reported_by"],
                condition=models.Q(status="pending"),
                name="one_pending_report_per_user_per_classroom",
            )
        ]

    def __str__(self):
        return f"Report on {self.classroom.title} by {self.reported_by} ({self.get_status_display()})"


# ---------------------------------------------------------------------------
# 16. CERTIFICATES (issued on course/classroom completion)
# ---------------------------------------------------------------------------
class Certificate(models.Model):
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="certificates")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="certificates")

    certificate_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    certificate_file = models.FileField(
        upload_to="certificates/", null=True, blank=True,
        validators=[MaxFileSizeValidator(10), FileExtensionValidator(["pdf"])],
    )
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("classroom", "student")

    def __str__(self):
        return f"Certificate: {self.student} - {self.classroom.title}"


# ---------------------------------------------------------------------------
# 17. CLASS REMINDERS (scheduled notifications before a session starts)
# ---------------------------------------------------------------------------
class ClassReminder(models.Model):
    class Channel(models.TextChoices):
        PUSH = "push", "Push Notification"
        SMS = "sms", "SMS"
        EMAIL = "email", "Email"
        # NOTE (Pass 7): WhatsApp is India's highest-open-rate channel —
        # added the moment notifications.py actually had a working
        # _send_whatsapp sender behind it (see that file). No migration
        # concern here beyond the usual new-choice-value one: existing
        # rows keep whatever channel they were created with.
        WHATSAPP = "whatsapp", "WhatsApp"

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="reminders")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="class_reminders")

    remind_at = models.DateTimeField()
    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.PUSH)
    is_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ["remind_at"]
        indexes = [models.Index(fields=["remind_at", "is_sent"])]

    def __str__(self):
        return f"Reminder for {self.user} @ {self.remind_at}"


# ---------------------------------------------------------------------------
# 18. CLASS HOLIDAY / OFF-DAY (exception dates — festival, teacher leave, etc.)
# ---------------------------------------------------------------------------
class ClassHoliday(models.Model):
    """A specific calendar date on which class is OFF for a classroom.

    This is different from ClassSchedule.days_of_week (which defines the
    *regular* weekly pattern, e.g. "runs Mon/Wed/Fri"): ClassHoliday is a
    one-off exception on top of that pattern — e.g. Diwali, a teacher's sick
    leave, a public holiday — so the session-generation job can skip creating
    a ClassSession for that date without editing the recurrence rule itself.
    """

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="holidays")
    schedule = models.ForeignKey(
        ClassSchedule,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="holidays",
        help_text="Leave blank to mark this date off across every schedule in the classroom.",
    )

    date = models.DateField()
    reason = models.CharField(max_length=150, blank=True, help_text="e.g. 'Diwali', 'Teacher on leave'")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="holidays_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("classroom", "schedule", "date")
        ordering = ["date"]
        indexes = [models.Index(fields=["classroom", "date"])]

    def __str__(self):
        return f"{self.classroom.title} off on {self.date} ({self.reason or 'holiday'})"


# ---------------------------------------------------------------------------
# 19. NOTICE BOARD (announcements from teacher/staff to enrolled students)
# ---------------------------------------------------------------------------
class Notice(models.Model):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        URGENT = "urgent", "Urgent"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="notices")
    posted_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notices_posted")

    title = models.CharField(max_length=150)
    message = models.TextField()
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    is_pinned = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Optional auto-hide time, e.g. for a one-off exam-date notice."
    )

    class Meta:
        ordering = ["-is_pinned", "-created_at"]
        indexes = [models.Index(fields=["classroom", "is_pinned", "-created_at"])]

    def __str__(self):
        return f"{self.title} ({self.classroom.title})"

    def is_expired(self) -> bool:
        return bool(self.expires_at and timezone.now() > self.expires_at)


# ---------------------------------------------------------------------------
# 20. CLASS QUERY / DOUBT (student asks a question, teacher/staff answers)
# ---------------------------------------------------------------------------
class ClassQuery(models.Model):
    """A student's question about a classroom (optionally tied to one
    specific session, e.g. a doubt from today's lecture). The classroom's
    teacher, co-teacher, or moderator can answer it — same permission split
    used for notices/holidays (see `_can_manage_classroom` in views.py)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ANSWERED = "answered", "Answered"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="queries")
    session = models.ForeignKey(
        ClassSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queries",
        help_text="Optional — set if the doubt is about a specific live session.",
    )
    asked_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="queries_asked")

    question = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    answer = models.TextField(blank=True)
    answered_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="queries_answered"
    )
    answered_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["classroom", "status", "-created_at"])]

    def __str__(self):
        return f"{self.asked_by} asked: {self.question[:40]} ({self.get_status_display()})"


# ---------------------------------------------------------------------------
# 21. NOTIFICATION (in-app notification feed / bell icon)
#
# NOTE (gap fix): the app already has FCM configured (see
# FCM_SERVICE_ACCOUNT_JSON_PATH in settings.py) and a Celery task pipeline
# for reminders/waitlist promotion, so a push alert can reach a user's
# device — but there was nowhere for the user to see a HISTORY of what
# happened (join request accepted/rejected, submission graded, doubt
# answered, certificate issued, refund processed, etc.) once the push
# banner disappears, and no unread-count for a bell icon. This model is
# that missing persisted record. It's deliberately separate from — not a
# replacement for — the push layer: create_notification() below is the one
# place that writes the in-app row; call it right alongside (not instead
# of) any existing push/email dispatch in tasks.py/signals.py.
# ---------------------------------------------------------------------------
class Notification(models.Model):
    class NotifType(models.TextChoices):
        JOIN_REQUEST_RECEIVED = "join_request_received", "Join Request Received"
        JOIN_REQUEST_ACCEPTED = "join_request_accepted", "Join Request Accepted"
        JOIN_REQUEST_REJECTED = "join_request_rejected", "Join Request Rejected"
        PASS_REFUNDED = "pass_refunded", "Pass Refunded"
        SESSION_REMINDER = "session_reminder", "Session Reminder"
        ASSIGNMENT_GRADED = "assignment_graded", "Assignment Graded"
        QUERY_ANSWERED = "query_answered", "Doubt Answered"
        CERTIFICATE_ISSUED = "certificate_issued", "Certificate Issued"
        WAITLIST_PROMOTED = "waitlist_promoted", "Waitlist Promoted"
        CLASSROOM_FLAGGED = "classroom_flagged", "Classroom Flagged"
        NOTICE_POSTED = "notice_posted", "Notice Posted"
        # NOTE (fix — production notification coverage audit): these six
        # were real events with no notification of any kind (not even the
        # in-app bell row) before this pass. Added alongside their
        # create_notification()/notify_*.delay() call sites in views.py —
        # see tasks.py for the matching push tasks.
        SESSION_LIVE = "session_live", "Class Started"
        SESSION_CANCELLED = "session_cancelled", "Session Cancelled"
        ASSIGNMENT_POSTED = "assignment_posted", "New Assignment"
        SUBMISSION_RECEIVED = "submission_received", "New Submission"
        STAFF_ADDED = "staff_added", "Added As Staff"
        REVIEW_POSTED = "review_posted", "New Review"
        REPORT_REVIEWED = "report_reviewed", "Report Reviewed"
        # NOTE (feature add — coin withdrawal / payout): see CoinWithdrawal
        # in this file and CoinWithdrawalViewSet in views.py.
        WITHDRAWAL_APPROVED = "withdrawal_approved", "Withdrawal Approved"
        WITHDRAWAL_REJECTED = "withdrawal_rejected", "Withdrawal Rejected"
        WITHDRAWAL_PAID = "withdrawal_paid", "Withdrawal Paid"
        # NOTE (feature add — classroom sharing): see ClassroomShare above
        # and ClassroomViewSet.share in views.py.
        CLASSROOM_SHARED = "classroom_shared", "Classroom Shared With You"
        # NEW (Pass 14 — gifting a pass): the two notification moments a
        # gift needs — the recipient finding out they got one, and the
        # gifter finding out it was actually claimed (they already paid
        # at send time, so "claimed" is their confirmation the coins
        # went somewhere, not a new charge). See PassGiftViewSet in
        # views.py.
        PASS_GIFT_RECEIVED = "pass_gift_received", "Pass Gift Received"
        PASS_GIFT_CLAIMED = "pass_gift_claimed", "Pass Gift Claimed"
        # NEW (fix — auto-renew/gift-expiry sweep tasks added: see
        # tasks.run_auto_renewals/expire_unclaimed_gifts): these three
        # events had no NotifType of their own even though the
        # underlying flows (PassPurchase.renew(), PassGift.
        # refund_to_gifter()) already existed — pure enum additions, no
        # migration needed since choices aren't a schema change.
        PASS_AUTO_RENEWED = "pass_auto_renewed", "Pass Auto-Renewed"
        AUTO_RENEW_FAILED = "auto_renew_failed", "Auto-Renewal Failed"
        PASS_GIFT_EXPIRED = "pass_gift_expired", "Gift Expired & Refunded"
        GENERIC = "generic", "Generic"

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    notif_type = models.CharField(max_length=30, choices=NotifType.choices, default=NotifType.GENERIC)

    title = models.CharField(max_length=150)
    message = models.CharField(max_length=255, blank=True)

    # Optional deep-link targets — whichever applies to notif_type. Both
    # SET_NULL (not CASCADE): a classroom/session being deleted later
    # shouldn't wipe out a user's notification history, just orphan the link.
    classroom = models.ForeignKey(
        Classroom, on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications"
    )
    session = models.ForeignKey(
        ClassSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications"
    )

    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read", "-created_at"])]

    def __str__(self):
        return f"{self.recipient} - {self.title}"

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])


# ---------------------------------------------------------------------------
# NEW (Pass 14 — per-notification-type channel preferences). One row per
# user. Two layers, cheapest-first:
#   1. Four blanket toggles (push/email/sms/whatsapp_enabled) — the "I
#      never want SMS at all" switch most users will actually touch.
#   2. `muted_types` — a JSON list of NotifType values the user wants
#      silenced entirely (no channel, not even the in-app bell row),
#      e.g. muting NOTICE_POSTED on a classroom they're only half-
#      following. Deliberately NOT a per-type-per-channel matrix (that's
#      a settings-screen nobody asks for in practice) — see
#      `allowed_channels_for()` below for exactly how the two layers
#      combine.
# ---------------------------------------------------------------------------
class NotificationPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="notification_preference")

    push_enabled = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=True)
    sms_enabled = models.BooleanField(default=False)  # opt-IN: SMS costs real money per send (see notifications.py)
    whatsapp_enabled = models.BooleanField(default=False)  # opt-IN, same reasoning as sms_enabled

    muted_types = models.JSONField(default=list, blank=True, help_text="List of Notification.NotifType values.")

    # Digest email (Pass 14): instead of one email per event, batch
    # everything since the last digest into a single daily/weekly email.
    # Independent of email_enabled above — a user can want digest-only
    # (no per-event email, still wants the roundup) or both off.
    class DigestFrequency(models.TextChoices):
        OFF = "off", "Off"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"

    digest_frequency = models.CharField(max_length=10, choices=DigestFrequency.choices, default=DigestFrequency.OFF)
    last_digest_sent_at = models.DateTimeField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Notification prefs for {self.user}"

    def allowed_channels_for(self, notif_type: str) -> list[str]:
        """What notifications.dispatch_notification() should actually try
        for this (user, notif_type) — an empty list means "in-app bell row
        only, no push/email/sms/whatsapp send at all", which is what a
        muted type collapses to (the Notification row itself is still
        created — a user muting reminders shouldn't lose the in-app
        history, just the interruption)."""
        if notif_type in (self.muted_types or []):
            return []
        allowed = []
        if self.push_enabled:
            allowed.append("push")
        if self.email_enabled:
            allowed.append("email")
        if self.sms_enabled:
            allowed.append("sms")
        if self.whatsapp_enabled:
            allowed.append("whatsapp")
        return allowed

    @classmethod
    def for_user(cls, user) -> "NotificationPreference":
        """Every user gets sane defaults (all-on push/email, opt-in
        sms/whatsapp, no mutes) without needing a migration data-load or a
        signal on User creation — get_or_create on first touch, same lazy
        pattern already used elsewhere in this app (see e.g.
        Classroom.refresh_rating being callable on demand rather than
        requiring a row to pre-exist)."""
        pref, _ = cls.objects.get_or_create(user=user)
        return pref


def create_notification(
    recipient: User,
    notif_type: str,
    title: str,
    message: str = "",
    classroom: "Classroom | None" = None,
    session: "ClassSession | None" = None,
) -> "Notification | None":
    """Single choke point for writing an in-app notification row. Swallows
    and logs its own errors rather than raising — a notification failing to
    save should never roll back or fail the request/transaction (a coin
    charge, a grade, an accept()) that triggered it. Returns None on
    failure instead of raising, same reasoning as the notify_classroom_flagged
    try/except pattern already used in the signal below."""
    try:
        return Notification.objects.create(
            recipient=recipient,
            notif_type=notif_type,
            title=title,
            message=message,
            classroom=classroom,
            session=session,
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to create in-app notification (%s) for user %s", notif_type, getattr(recipient, "id", None)
        )
        return None


def create_bulk_notifications(
    recipients,
    notif_type: str,
    title: str,
    message: str = "",
    classroom: "Classroom | None" = None,
    session: "ClassSession | None" = None,
) -> None:
    """Bulk variant for fan-out cases (e.g. an urgent notice to every
    enrolled student) — one INSERT instead of N, and skips read_at/is_read
    defaults being touched per-row. recipients can be any iterable of User
    (or user id) — deduplicated defensively since a teacher/co-teacher could
    otherwise appear twice (once as staff, once as an enrolled student)."""
    recipient_ids = {getattr(r, "id", r) for r in recipients}
    recipient_ids.discard(None)
    if not recipient_ids:
        return
    try:
        Notification.objects.bulk_create(
            [
                Notification(
                    recipient_id=rid,
                    notif_type=notif_type,
                    title=title,
                    message=message,
                    classroom=classroom,
                    session=session,
                )
                for rid in recipient_ids
            ],
            batch_size=500,
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to bulk-create in-app notifications (%s) for %d recipients", notif_type, len(recipient_ids)
        )



# ---------------------------------------------------------------------------
# 12. CHUNKED UPLOAD (large-file uploads in pieces, assembled server-side)
# ---------------------------------------------------------------------------
# Why this exists: DATA_UPLOAD_MAX_MEMORY_SIZE is 10MB (see settings.py —
# deliberately capped there as a DoS guard on the request body size Django
# will parse at all). ClassMaterial.file allows up to 100MB and
# Assignment.attachment / AssignmentSubmission.file allow up to 50MB — all
# three would be rejected outright as a single request well before hitting
# their own MaxFileSizeValidator. Chunked upload splits a big file into
# small pieces (each comfortably under the 10MB request-body cap), uploads
# them one at a time, and assembles the final file server-side only once
# every piece has arrived — at which point the *existing* size/extension
# validators run against the assembled file exactly as they always have.
#
# One row = one in-flight (or finished/aborted/expired) upload attempt.
# Temp chunks are NOT stored under MEDIA_ROOT (see settings.py
# CHUNKED_UPLOAD_TMP_ROOT) specifically so a partially-uploaded file can
# never be served/guessed at via the media URL while assembly is still in
# progress. See chunked_upload_views.py for the init/chunk/complete/abort
# endpoints that operate on this model.
class ChunkedUpload(models.Model):
    class Purpose(models.TextChoices):
        COVER_IMAGE = "cover_image", "Classroom Cover Image"
        MATERIAL = "material", "Class Material"
        ASSIGNMENT_ATTACHMENT = "assignment_attachment", "Assignment Attachment"
        SUBMISSION_FILE = "submission_file", "Assignment Submission File"

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In Progress"       # accepting chunks
        PROCESSING = "processing", "Processing"           # complete() claimed it, assembling
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"                        # assembly/validation error — see error_message
        ABORTED = "aborted", "Aborted"                      # client-cancelled
        EXPIRED = "expired", "Expired"                      # swept by cleanup_stale_chunked_uploads

    upload_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chunked_uploads")
    purpose = models.CharField(max_length=30, choices=Purpose.choices)

    # Sanitized (basename-only, no path separators) original name — used
    # only to derive the extension and for display; never trusted as a
    # filesystem path (see _safe_extension() in chunked_upload_views.py).
    original_file_name = models.CharField(max_length=255)
    file_extension = models.CharField(max_length=10)  # lowercase, no leading dot
    total_chunks = models.PositiveIntegerField()
    total_size = models.PositiveBigIntegerField()

    # Everything complete() needs to know WHERE the assembled file goes,
    # captured once at init() (and permission-checked again at both ends —
    # see chunked_upload_views.py). Shape depends on purpose:
    #   cover_image           -> {"classroom_id": <id>}
    #   material               -> {"classroom_id": <id>, "title": str,
    #                              "material_type": str, "session_id": <id>|None}
    #   assignment_attachment -> {"assignment_id": <id>}
    #   submission_file        -> {"assignment_id": <id>}
    extra_data = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=15, choices=Status.choices, default=Status.IN_PROGRESS)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            # cleanup_stale_chunked_uploads sweeps exactly this shape of query.
            models.Index(fields=["status", "created_at"]),
            # per-user in-progress count check at init() (caps concurrent
            # uploads per user — see MAX_IN_PROGRESS_UPLOADS_PER_USER).
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"{self.purpose} upload {self.upload_id} ({self.status})"


# ---------------------------------------------------------------------------
# SIGNALS — keep Classroom.rating_avg / rating_count / enrolled_count in sync
# ---------------------------------------------------------------------------
from django.db.models.signals import post_delete, post_save, pre_save  # noqa: E402
from django.dispatch import receiver  # noqa: E402


@receiver(post_save, sender=Classroom)
@receiver(post_delete, sender=Classroom)
def _bump_classroom_list_cache(sender, instance, **kwargs):
    """Single choke point for Explore/search list cache invalidation — see
    the CLASSROOM LIST CACHE VERSIONING note near the top of this file.
    Fires on every Classroom save (including the internal
    refresh_rating/refresh_enrolled_count/sync_flag_status saves below) and
    on every delete, so the cached list can never drift from what's
    actually in the DB by more than the time it takes this signal to run."""
    bump_classroom_list_cache_version()


@receiver(post_save, sender=Notice)
@receiver(post_delete, sender=Notice)
def _bump_notice_list_cache(sender, instance, **kwargs):
    """Invalidates the cached notice-board page for this notice's
    classroom only — see the PER-CLASSROOM NOTICE LIST CACHE VERSIONING
    note near the top of this file. Also fires on the expires_at auto-hide
    boundary being crossed only if something re-saves the row at that
    point (nothing currently does) — same known limitation as
    Classroom.enrolled_count going stale on pure time-based expiry; a
    reasonable follow-up is a short TTL on top of this (see
    NoticeViewSet.LIST_CACHE_TTL_SECONDS) rather than a new sweep task,
    since a notice going stale by a few minutes is low-stakes compared to
    money/access state.
    """
    bump_notice_list_cache_version(instance.classroom_id)


@receiver(post_save, sender=ClassroomReview)
@receiver(post_delete, sender=ClassroomReview)
def _sync_classroom_rating(sender, instance, **kwargs):
    instance.classroom.refresh_rating()


@receiver(post_save, sender=PassPurchase)
def _sync_classroom_enrolled_count(sender, instance, **kwargs):
    instance.class_pass.classroom.refresh_enrolled_count()


@receiver(pre_save, sender=ClassSession)
def _stash_previous_session_status(sender, instance, **kwargs):
    """Capture the pre-save status so the post_save handler below can tell
    an actual transition INTO completed apart from an unrelated field edit
    (title/recording_url/etc.) on a session that was already completed —
    same reasoning as the was_cancelled comparison in
    ClassSessionViewSet.perform_update (views.py)."""
    if instance.pk:
        instance._previous_status = (
            ClassSession.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        )
    else:
        instance._previous_status = None


@receiver(post_save, sender=ClassSession)
def _charge_passes_for_completed_session(sender, instance, created, **kwargs):
    """The other half of the per-day escrow design (see the NOTE (fix —
    "pay only for classes actually held") on PassPurchase above):
    PassPurchase.charge_for_session() existed but nothing ever called it,
    so no pass was ever actually charged day-by-day — this is that missing
    call site. Fires on every path that can mark a session COMPLETED
    (ClassSessionViewSet.end, Django admin, a future auto-complete task),
    same "doesn't matter which path" guarantee the docstrings already
    promised. Guarded to only run on an actual transition into COMPLETED
    (via the pre_save stash above), not on every later edit to an
    already-completed session. charge_for_session() itself is idempotent
    (unique purchase+date constraint), so even a double-fire here is safe.
    """
    if instance.status != ClassSession.Status.COMPLETED:
        return
    if getattr(instance, "_previous_status", None) == ClassSession.Status.COMPLETED:
        return

    purchases = PassPurchase.objects.filter(
        class_pass__classroom_id=instance.classroom_id,
        status=PassPurchase.Status.SUCCESS,
        is_active=True,
    )
    for purchase in purchases:
        try:
            with transaction.atomic():
                locked = PassPurchase.objects.select_for_update().get(pk=purchase.pk)
                locked.charge_for_session(instance)
        except Exception:
            # NOTE: one purchase's charge failing (e.g. a lock timeout)
            # must never block the rest of the classroom's purchases from
            # being charged for this same session, and must never roll
            # back the session-completion save itself. sync_missed_charges()
            # is the safety net that catches whatever this loop misses.
            logging.getLogger(__name__).exception(
                "Failed to charge purchase %s for completed session %s", purchase.id, instance.id
            )


@receiver(post_save, sender=ClassroomReport)
def _auto_flag_classroom(sender, instance, created, **kwargs):
    """Once a classroom accumulates AUTO_FLAG_THRESHOLD pending reports,
    flag it so it drops out of the public Explore listing (see
    ClassroomViewSet.get_queryset) until platform staff review it via
    ClassroomReportViewSet.review. Doesn't touch is_active, so the
    teacher/existing enrolled students keep normal access while it's
    under review — this only hides it from new students discovering it."""
    if not created or instance.classroom.is_flagged:
        return
    pending_count = instance.classroom.reports.filter(status=ClassroomReport.Status.PENDING).count()
    if pending_count >= ClassroomReport.AUTO_FLAG_THRESHOLD:
        instance.classroom.is_flagged = True
        instance.classroom.save(update_fields=["is_flagged"])

        # NOTE (fix): the teacher previously had no way to find out their
        # classroom had dropped out of Explore — they'd only discover it
        # if enrollment mysteriously stalled and they went looking. Queued
        # (not sent inline) so a notification-provider hiccup can never
        # roll back the flagging transaction itself. Lazy import: models.py
        # must stay importable even if tasks.py/Celery isn't fully wired
        # yet (e.g. during migrations).
        try:
            from .tasks import notify_classroom_flagged

            notify_classroom_flagged.delay(instance.classroom_id)
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to queue flagged-classroom notification for classroom %s",
                instance.classroom_id,
            )