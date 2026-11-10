# tuitionclass/models/classroom.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from django.contrib.postgres.indexes import GinIndex
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
)
from login.models import User
from django.db import models
from django.utils import timezone
from .validators import MaxFileSizeValidator
from .cache_versions import _broadcast_classroom_stats


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
    # X12 (auto-recording): opt-in. When True (and `recording_enabled` is on) the LiveKit `room_started` /
    # `participant_joined` webhook starts the egress recording by itself the moment the HOST is in the room
    # (see recording_auto.py). Default False - nothing is ever recorded behind the teacher's back; the live
    # room already shows the REC badge to everyone when it starts (socket `recording.started`).
    recording_auto = models.BooleanField(default=False)

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

    # ---------------------------------------------------------------------
    # FEATURE (trial/demo access): a gateway that lets a not-yet-enrolled
    # student sample a short slice of a LIVE session before paying for a
    # pass. No pre-existing "demo class" concept existed anywhere in this
    # app (searched models/views/serializers for demo/trial/preview/free —
    # the only "free" thing is ClassPass.PassType.FREE, which is a full
    # pass, not a preview), so this lives here rather than extending
    # something else.
    #
    #   is_trial_enabled        teacher opt-in (default off)
    #   trial_password          teacher-set short code, stored HASHED
    #                           (django make_password) — never returned by
    #                           any serializer; use set_trial_password() /
    #                           check_trial_password() below. A teacher who
    #                           forgets it simply sets a new one.
    #   trial_duration_minutes  how long a trial join stays valid
    #
    # Per-student state (who used their one trial, and when it lapses)
    # lives on ClassroomTrialAccess below. See ClassroomViewSet.trial_join.
    # ---------------------------------------------------------------------
    TRIAL_MIN_MINUTES = 1
    TRIAL_MAX_MINUTES = 30
    is_trial_enabled = models.BooleanField(default=False)
    trial_password = models.CharField(max_length=128, blank=True, default="")
    trial_duration_minutes = models.PositiveSmallIntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
        help_text="How many minutes a trial join stays valid (1-30).",
    )

    # ---------------------------------------------------------------------
    # FEATURE (optional Parent) — teacher opt-in per classroom. When False
    # (the default) the whole parent layer is dormant: no invites, no parent
    # notifications. When True the teacher can, per student, add a parent
    # or skip (see ClassroomParentState). NOTHING else in the app (join,
    # pass purchase, session, attendance, payment) ever reads this flag as
    # a gate — a parent is always optional.
    #
    # FEATURE (student attendance) — knobs for the auto attendance build
    # (see tuitionclass/attendance.py):
    #   attendance_min_percent  a student who was in the room for at least
    #                           this % of the session counts as present/late
    #   attendance_edit_days    how long after a session the teacher/staff
    #                           may still hand-edit its attendance
    # ---------------------------------------------------------------------
    parents_enabled = models.BooleanField(default=False)
    attendance_min_percent = models.PositiveSmallIntegerField(
        default=50,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="Minimum % of a session a student must attend to count as present (1-100).",
    )
    attendance_edit_days = models.PositiveSmallIntegerField(
        default=7,
        validators=[MinValueValidator(0), MaxValueValidator(60)],
        help_text="Days after a session during which its attendance can still be edited by hand.",
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

    # X12 L-C (standalone "Go Live"): the hidden "personal classroom" every creator gets the first time they go live
    # from the "+" hub without a class. It is a normal Classroom row (so every live-room feature - LiveKit, chat,
    # polls, recording, moderation, passes - is reused, nothing is copied), but it never shows in Explore / "my
    # classes" lists (see ClassroomViewSet.get_queryset) and has no chat group. One per teacher
    # (tuitionclass/standalone_live.py::personal_classroom_for).
    is_personal = models.BooleanField(default=False, db_index=True)

    # ---------------------------------------------------------------------
    # 🔧 GAP FIX (Gap 2 prerequisite) — `core/classroom_chat_bridge.py`
    # already reads these two fields (`_get_group_for_classroom`) and
    # writes them (`create_classroom_group`), but they never actually
    # existed on this model — that file's own docstring flags them under
    # "ASSUMPTIONS" as best-guess names pending confirmation. Added here
    # with exactly those names so the bridge (and the Gap 2 parent-
    # dashboard restructure, which reads `chat_group_enabled` to decide
    # whether a classroom has an optional chat block) actually works
    # instead of raising AttributeError on every call.
    #
    # `linked_conversation_id` is a plain UUIDField, NOT a ForeignKey —
    # deliberately, to keep `tuitionclass` decoupled from `message`'s models
    # (see the bridge file's module docstring, point 1). `message.
    # Conversation`'s primary key is a UUID (see `message/models.py`
    # `BaseModel.id`), so the type must match even though there's no DB-
    # level FK constraint enforcing it — the bridge is the only code that
    # ever writes this field, and it always writes a real Conversation id.
    #
    # `chat_group_enabled` is opt-in per classroom: False until a teacher
    # explicitly creates a group (`create_classroom_group()`) — so an
    # older classroom, or one a teacher deliberately never linked, has
    # `chat_group_enabled=False, linked_conversation_id=None` and is
    # simply skipped by every bridge sync function (documented as a
    # best-effort no-op there) and by the dashboard's optional chat block.
    #
    # NOTE: adding these fields requires a migration
    # (`manage.py makemigrations tuitionclass`) before they take effect.
    # ---------------------------------------------------------------------
    # T3 — now default True: every new classroom gets its chat group
    # automatically (post_save signal -> core.chat_sync "create"). The teacher
    # can switch it off (POST classrooms/<id>/group/toggle/ -> the group is
    # ARCHIVED, never deleted) and on again (archived group is restored).
    # `chat_group_enabled=True` + `linked_conversation_id=None` just means
    # "wanted, not created yet" (retry / backfill / reconcile fixes it).
    chat_group_enabled = models.BooleanField(default=True)
    linked_conversation_id = models.UUIDField(null=True, blank=True)

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

    def set_trial_password(self, raw: str) -> None:
        """Hash + store the teacher-set trial code (caller saves)."""
        from django.contrib.auth.hashers import make_password

        self.trial_password = make_password(raw) if raw else ""

    def check_trial_password(self, raw: str) -> bool:
        from django.contrib.auth.hashers import check_password

        if not self.trial_password or not raw:
            return False
        return check_password(raw, self.trial_password)

    def is_enrolled(self, user: User) -> bool:
        """Has this user EVER successfully purchased a pass for this
        classroom — active OR expired? Broader than has_access() on purpose:
        a lapsed pass should still unlock the classroom's general content
        (materials, notices, holidays, schedule/session listing, doubts,
        assigmentss, certificates, reviews) — everything except actually
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
        active schedules, e.g. 'Mon, Wed, Fri 06:00 PM Asia/Kolkata (60 min)' (the zone is
        included because the time alone is ambiguous for viewers elsewhere). Used by the
        classroom list/detail API so the frontend doesn't have to stitch
        ClassSchedule rows together itself."""
        parts = []
        for sched in self.schedules.filter(is_active=True):
            if sched.recurrence_type == ClassSchedule.RecurrenceType.WEEKLY and sched.days_of_week:
                days = ", ".join(d.title() for d in sched.days_of_week)
            else:
                days = sched.get_recurrence_type_display()
            parts.append(f"{days} {sched.start_time:%I:%M %p} {sched.timezone} ({sched.duration_minutes} min)")
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

        web_base = getattr(settings, "TUITIONCLASS_WEB_BASE_URL", "https://app.example.com").rstrip("/")
        deep_link_scheme = getattr(settings, "TUITIONCLASS_DEEP_LINK_SCHEME", "tuitionclass")
        web_url = f"{web_base}/classroom/{self.id}"
        deep_link = f"{deep_link_scheme}://classroom/{self.id}"
        return web_url, deep_link

    @property
    def referral_active(self) -> bool:
        """T-REF: refer & earn exists for this class ONLY when the teacher turned it on AND chose a cut > 0."""
        return bool(self.referral_enabled) and (self.referral_commission_percent or 0) > 0

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



# Forward references: used only inside method/function bodies, defined in a LATER module. Bound by models/__init__.py
# once every module is loaded (see _link_forward_refs there). `None` here only keeps linters quiet.
ClassSchedule = None  # -> .schedules
ClassroomReport = None  # -> .classroom_admin
PassPurchase = None  # -> .passes
referral_code_for_user = None  # -> .referral
