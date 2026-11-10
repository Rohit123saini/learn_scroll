"""
tuitionclass/views/learning.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import hashlib
import uuid
from assignments.serializers import (
    assigmentsSerializer as UnifiedassigmentsSerializer,
    assigmentsSubmissionSerializer as UnifiedassigmentsSubmissionSerializer,
)
from core.models import Notification
from core.services import create_bulk_notifications, create_notification
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .. import bridge
from ..realtime import broadcast_to_session
from ..models import (
    Certificate,
    ClassHoliday,
    ClassQuery,
    ClassQueryUpvote,
    ClassReminder,
    ClassSession,
    Classroom,
    Notice,
    PassPurchase,
    SessionParticipant,
    assigmentsSubmission,
    get_notice_list_cache_version,
    seconds_until_next_notice_expiry,
)
from ..serializers import (
    CertificateIssueSerializer,
    CertificateSerializer,
    ClassHolidaySerializer,
    ClassQueryAnswerSerializer,
    ClassQuerySerializer,
    ClassReminderSerializer,
    NoticeSerializer,
    StudentProgressSerializer,
)

from ._common import TuitionClassPagination, _is_truthy, _safe_delay
from .access import (
    _can_manage_classroom,
    _can_view_classroom_internals,
    _has_active_trial,
    _has_room_access,
)


# ---------------------------------------------------------------------------
# 10. assigments + SUBMISSION
#
# Task 12 — thin proxy onto the unified `assigments` app via
# `tuitionclass.bridge`. Only two things stay on tuitionclass's own URL
# surface: posting a new assigments to a classroom, and listing a
# classroom's assigmentss/submissions — because only those two need
# tuitionclass's own classroom-membership/manage-permission rules
# (_can_manage_classroom / _can_view_classroom_internals), which
# `assigments` has no way to check (it has no concept of a "classroom").
#
# Submitting an assigments, grading it, structured-question review,
# publish/unpublish — NOT proxied here. `assigments.views.
# assigmentsSubmissionViewSet`'s own queryset already grants access
# correctly for a tuitionclass-sourced submission (`Q(student=user) |
# Q(assigments__posted_by=user)` — `posted_by` is set to the classroom's
# teacher at creation time by `bridge.create_assigments`, `student` is
# the roster member), and its `grade`/`review_answer` actions are gated
# by `IsassigmentsStaffOrOwner`, `submit_*`/`publish`/`unpublish` by
# `IsSubmissionStudent` — both already correct for a tuitionclass-sourced
# row with no tuitionclass-side wrapping needed. Flutter hits
# `/assigments/submissions/{id}/...` directly for those; the two
# viewsets below only ever hand back that `id` (as part of the nested
# `assigments`/each submission row) for the client to call next.
#
# `tuitionclass.models.assigments`/`assigmentsSubmission` (still defined in
# models.py, untouched — no schema/`makemigrations` change here) are no
# longer written to by these two viewsets. `assigmentsSubmission` is
# still imported at the top of this file because `StudentProgressView`
# below still reads it for `assigmentss_submitted` — NOTE (flagged, not
# fixed, out of this task's scope): that count will only reflect
# submissions made before this cutover, plus whatever
# `migrate_tuitionclass_assigmentss_to_unified` backfilled, since new
# submissions from here on land in `assigments.assigmentsSubmission`
# instead. Repointing that dashboard at `tuitionclass.bridge.
# get_assigments_submissions()` (summed across the student's classrooms)
# is a real follow-up, not done here since it wasn't part of Task 12's
# file list.
# ---------------------------------------------------------------------------
def _json_field(data, name):
    """Multipart sends list fields as a JSON string; JSON bodies send a real list. Absent -> None."""
    import json

    raw = data.get(name)
    if raw in (None, ""):
        return None
    if isinstance(raw, (bytes, str)):
        try:
            return json.loads(raw)
        except ValueError:
            raise ValidationError({name: "Send a valid JSON list."})
    return raw


def _parse_class_assignment_extras(data) -> dict:
    """Optional Project type / tags / difficulty / hand-in types / rubric for a CLASS assignment.
    Same validators as the personal assignment API; a rubric makes total marks = its sum (set in the bridge)."""
    from assignments.models import assigmentsDifficulty, assigmentsKind
    from assignments.serializers import validate_rubric, validate_submission_types, validate_tags

    out = {}
    kind = (data.get("kind") or "").strip()
    if kind:
        if kind not in assigmentsKind.values:
            raise ValidationError({"kind": f"Choose one of {list(assigmentsKind.values)}."})
        out["kind"] = kind
    difficulty = (data.get("difficulty") or "").strip()
    if difficulty:
        if difficulty not in assigmentsDifficulty.values:
            raise ValidationError({"difficulty": f"Choose one of {list(assigmentsDifficulty.values)}."})
        out["difficulty"] = difficulty
    for name, validator in (("tags", validate_tags), ("rubric", validate_rubric), ("submission_types", validate_submission_types)):
        value = _json_field(data, name)
        if value is not None:
            out[name] = validator(value)
    return out


class assigmentsViewSet(viewsets.ViewSet):
    """Thin proxy — `list` + `create` only.

    `retrieve`/`update`/`destroy` are NOT implemented: the unified
    `assigments.views.assigmentsViewSet` is personal-source-only by its
    own docstring ("Campus/tuitionclass assigmentss never reach this
    viewset"), so there is currently no path anywhere for editing or
    deleting an already-posted classroom assigments — not a tuitionclass
    gap specifically, a gap in the unified app for the context-sourced
    flow generally. Flagged rather than worked around by guessing at an
    `assigments/bridge.py` function (`update_context_assigments()` /
    `delete_context_assigments()`) that doesn't exist yet.
    """

    permission_classes = [IsAuthenticated]

    def list(self, request):
        classroom_id = request.query_params.get("classroom")
        if not classroom_id:
            raise ValidationError({"classroom": "This query parameter is required."})
        classroom = get_object_or_404(Classroom, pk=classroom_id)
        if not _can_view_classroom_internals(classroom, request.user):
            raise PermissionDenied("A pass (active or expired) is required to view this classroom's assigmentss.")

        submissions = bridge.get_assigments_submissions(classroom)
        if not _can_manage_classroom(classroom, request.user):
            submissions = submissions.filter(student=request.user)

        # bridge.create_assigments() bulk-pre-creates one
        # assigmentsSubmission(status=MISSING) per roster member at
        # posting time (see assigments.bridge.create_context_assigments)
        # — so every assigments this user is entitled to see already has
        # exactly one submission row per user in the queryset above.
        # De-duplicating by assigments_id here is therefore a correct
        # "every assigments this classroom has, this user can see" list,
        # with no separate "list assigmentss" bridge call needed.
        seen_ids = set()
        assigmentss = []
        for submission in submissions.select_related("assigments").order_by("assigments__due_date"):
            if submission.assigments_id in seen_ids:
                continue
            seen_ids.add(submission.assigments_id)
            assigmentss.append(submission.assigments)

        serializer = UnifiedassigmentsSerializer(assigmentss, many=True, context={"request": request})
        return Response(serializer.data)

    def create(self, request):
        classroom_id = request.data.get("classroom")
        if not classroom_id:
            raise ValidationError({"classroom": "This field is required."})
        classroom = get_object_or_404(Classroom, pk=classroom_id)
        if not _can_manage_classroom(classroom, request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can create an assigments."
            )

        due_date = request.data.get("due_date")
        if due_date:
            # Unified assigments.due_date is a DateField (the old local
            # tuitionclass.assigments.due_date was a DateTimeField) — accept
            # either an ISO date or datetime string from the client and
            # take just the date part here, at the one call site that
            # actually knows what the client sent, rather than inside
            # bridge.create_assigments() (see that function's own
            # docstring for why it deliberately does NOT do this
            # coercion itself).
            parsed = parse_date(due_date) or parse_datetime(due_date)
            if parsed is None:
                raise ValidationError({"due_date": "Enter a valid ISO date or datetime."})
            due_date = parsed.date() if hasattr(parsed, "date") and callable(parsed.date) else parsed

        extras = _parse_class_assignment_extras(request.data)
        assigments = bridge.create_assigments(
            classroom=classroom,
            posted_by=request.user,
            title=request.data.get("title", ""),
            description=request.data.get("description", ""),
            attachment=request.FILES.get("attachment"),
            due_date=due_date,
            **extras,
        )

        # NOTE (carried over from the old assigmentsViewSet.perform_create,
        # verified against real PassPurchase fields): posting an
        # assigments fans out an in-app notification to every active pass
        # holder. Kept here rather than folded into bridge.create_assigments()
        # — that function's only job is "create the assigments in the
        # unified app"; it has no reason to know tuitionclass's notification
        # system exists.
        student_ids = list(
            PassPurchase.objects.filter(
                class_pass__classroom=classroom,
                status=PassPurchase.Status.SUCCESS,
                is_active=True,
                expires_at__gt=timezone.now(),
            ).values_list("student_id", flat=True).distinct()
        )
        create_bulk_notifications(
            recipients=student_ids,
            notif_type=Notification.NotifType.assigments_POSTED,
            title="New assigments posted",
            message=f"'{assigments.title}' was posted in '{classroom.title}'.",
            classroom=classroom,
        )
        from ..tasks import notify_assigments_posted

        _safe_delay(notify_assigments_posted, assigments.id, student_ids)

        serializer = UnifiedassigmentsSerializer(assigments, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class assigmentsSubmissionViewSet(viewsets.ViewSet):
    """Thin proxy — read-only `list` (a teacher's grading queue for a
    classroom, or a student's own submissions in it). See the module
    note above `assigmentsViewSet` for why submit/grade/publish are
    deliberately NOT here — those go straight to
    `/assigments/submissions/{id}/...`.
    """

    permission_classes = [IsAuthenticated]

    def list(self, request):
        classroom_id = request.query_params.get("classroom")
        if not classroom_id:
            raise ValidationError({"classroom": "This query parameter is required."})
        classroom = get_object_or_404(Classroom, pk=classroom_id)
        if not _can_view_classroom_internals(classroom, request.user):
            raise PermissionDenied("A pass (active or expired) is required to view this classroom's submissions.")

        submissions = bridge.get_assigments_submissions(classroom)
        # Same manager/self split as the old assigmentsSubmissionViewSet.
        # get_queryset: full roster view for a classroom manager, own
        # rows only for everyone else.
        if not _can_manage_classroom(classroom, request.user):
            submissions = submissions.filter(student=request.user)

        assigments_id = request.query_params.get("assigments")
        if assigments_id:
            submissions = submissions.filter(assigments_id=assigments_id)

        serializer = UnifiedassigmentsSubmissionSerializer(
            submissions.prefetch_related("answers__question"), many=True, context={"request": request}
        )
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# STUDENT PROGRESS DASHBOARD
#
# GAP THIS CLOSES: everything a student's activity produces
# (SessionParticipant rows, assigmentsSubmission grades, Certificate
# issuance) lived scattered per-classroom with nowhere pulling it together
# into "how am I doing overall" — the retention-relevant view a student
# actually wants to open regularly. This is a read-only aggregate, same
# "APIView wrapping a dict from several queries" pattern as
# TeacherEarningsView above.
# ---------------------------------------------------------------------------
class StudentProgressView(APIView):
    """GET /tuitionclass/my-progress/ — the calling user's own activity only;
    there's no "view another student's progress" concept here (a teacher
    wanting per-student insight already has assigmentsSubmissionViewSet /
    CertificateViewSet scoped to their own classrooms for that)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        classes_attended = SessionParticipant.objects.filter(
            user=user, role=SessionParticipant.Role.STUDENT
        ).count()
        classrooms_enrolled = (
            PassPurchase.objects.filter(student=user).values("class_pass__classroom_id").distinct().count()
        )
        assigmentss_submitted = assigmentsSubmission.objects.filter(student=user).count()
        certificates_earned = Certificate.objects.filter(student=user).count()

        current_streak, longest_streak = self._attendance_streaks(user)

        # FEATURE (student attendance): overall % from SessionAttendance.
        from ..models import compute_attendance_counts_bulk

        enrolled_ids = list(
            PassPurchase.objects.filter(student=user, status=PassPurchase.Status.SUCCESS)
            .values_list("class_pass__classroom_id", flat=True).distinct()
        )
        counts = compute_attendance_counts_bulk(enrolled_ids, user)
        sessions_attended = sum(a for a, _ in counts.values())
        sessions_counted = sum(t for _, t in counts.values())

        data = {
            "classes_attended": classes_attended,
            "classrooms_enrolled": classrooms_enrolled,
            "assigmentss_submitted": assigmentss_submitted,
            "certificates_earned": certificates_earned,
            "current_streak_days": current_streak,
            "longest_streak_days": longest_streak,
            "attendance_percent": round(sessions_attended / sessions_counted * 100, 2) if sessions_counted else 0,
            "sessions_attended": sessions_attended,
            "sessions_counted": sessions_counted,
        }
        return Response(StudentProgressSerializer(data).data)

    @staticmethod
    def _attendance_streaks(user) -> tuple[int, int]:
        """Current + longest run of CONSECUTIVE CALENDAR DAYS on which this
        user attended at least one session, across every classroom. A day
        with no attendance breaks the streak — matching how every
        habit-streak feature (Duolingo etc.) actually counts, not just
        "days since first class"."""
        # FEATURE (student attendance): the streak now reads the attendance
        # table. A session with a SessionAttendance row counts only if the
        # student was present/late (so a teacher's manual "absent" edit
        # breaks the streak, and a manual "present" extends it); a session
        # with no row yet falls back to the raw join log as before.
        from ..models import SessionAttendance

        att_rows = list(
            SessionAttendance.objects.filter(student=user).values_list("session_id", "status", "session__scheduled_start")
        )
        att_sessions = {sid for sid, _, _ in att_rows}
        date_set = {
            start.date() for _, st, start in att_rows
            if st in (SessionAttendance.Status.PRESENT, SessionAttendance.Status.LATE)
        }
        for sid, d in SessionParticipant.objects.filter(
            user=user, role=SessionParticipant.Role.STUDENT
        ).values_list("session_id", "joined_at__date"):
            if sid not in att_sessions:
                date_set.add(d)
        dates = sorted(date_set)
        if not dates:
            return 0, 0

        longest = run = 1
        for prev, curr in zip(dates, dates[1:]):
            if (curr - prev).days == 1:
                run += 1
            else:
                run = 1
            longest = max(longest, run)

        today = timezone.now().date()
        if dates[-1] not in (today, today - timezone.timedelta(days=1)):
            # Most recent attended day is neither today nor yesterday —
            # the streak has already lapsed, whatever its past length was.
            current = 0
        else:
            current = 1
            for i in range(len(dates) - 1, 0, -1):
                if (dates[i] - dates[i - 1]).days == 1:
                    current += 1
                else:
                    break
        return current, longest


# ---------------------------------------------------------------------------
# 16. CERTIFICATE (read-only; issued by server-side logic elsewhere)
# ---------------------------------------------------------------------------
class CertificateViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a certificate list grows unbounded over a user's lifetime
    # on the platform (every certificate they've ever earned, or every one
    # issued across a classroom they teach) — same unbounded-list gap
    # already fixed on sessions/purchases/chat/join-requests above.
    pagination_class = TuitionClassPagination

    def get_serializer_class(self):
        return CertificateIssueSerializer if self.action == "create" else CertificateSerializer

    def get_queryset(self):
        base = Certificate.objects.select_related("student", "classroom")
        classroom_id = self.request.query_params.get("classroom")
        user = self.request.user
        if classroom_id:
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            # Teacher/co-teacher/moderator (org staff included) browsing their
            # own classroom's page sees every certificate issued there —
            # everyone else, filtered or not, only ever sees their own.
            if classroom and _can_manage_classroom(classroom, user):
                return base.filter(classroom_id=classroom_id)
            return base.filter(classroom_id=classroom_id, student=user)
        return base.filter(student=user)

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        student = serializer.validated_data["student"]
        if not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can issue a certificate."
            )
        # NOTE (fix): nothing checked that the recipient ever actually held
        # a pass for this classroom — a teacher (compromised account, or a
        # co-teacher/moderator acting maliciously) could mint a legitimate-
        # looking "completion" certificate for ANY user on the platform,
        # including one who never took a single class here. Certificate is
        # unique_together (classroom, student), so this also stops a
        # duplicate re-issue attempt from silently failing with a 500
        # instead of a clear error.
        if not classroom.is_enrolled(student):
            raise ValidationError(
                "Can't issue a certificate to a user who has never held a pass for this classroom."
            )
        if Certificate.objects.filter(classroom=classroom, student=student).exists():
            raise ValidationError("A certificate has already been issued to this student for this classroom.")
        serializer.save(certificate_id=uuid.uuid4().hex, issued_at=timezone.now())
        create_notification(
            recipient=student,
            notif_type=Notification.NotifType.CERTIFICATE_ISSUED,
            title="Certificate issued",
            message=f"You've been issued a certificate for '{classroom.title}'.",
            classroom=classroom,
        )
        # NOTE (fix): "you earned a certificate" is exactly the kind of
        # moment worth an actual push, not just a silent bell-icon row.
        from ..tasks import notify_certificate_issued

        _safe_delay(notify_certificate_issued, serializer.instance.id)


# ---------------------------------------------------------------------------
# 17. CLASS REMINDER
# ---------------------------------------------------------------------------
class ClassReminderViewSet(viewsets.ModelViewSet):
    serializer_class = ClassReminderSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): same unbounded-list gap as certificates/sessions/purchases
    # above — a user active for a long time can accumulate a large number
    # of reminders across every classroom they've ever engaged with.
    pagination_class = TuitionClassPagination

    def get_queryset(self):
        return ClassReminder.objects.filter(user=self.request.user).select_related(
            "user", "session__classroom"
        )

    def perform_create(self, serializer):
        # NOTE (fix): no check that the caller has access to the session's
        # classroom — low severity (rows are already scoped to `user` on
        # read, see get_queryset), but still lets someone create reminder
        # rows against sessions they can't otherwise see. Gated the same
        # way as everywhere else that reads session internals.
        session = serializer.validated_data["session"]
        if not _can_view_classroom_internals(session.classroom, self.request.user):
            raise PermissionDenied("You don't have access to this session.")
        serializer.save(user=self.request.user)


# ---------------------------------------------------------------------------
# 18. CLASS HOLIDAY / OFF-DAY
# ---------------------------------------------------------------------------
class ClassHolidayViewSet(viewsets.ModelViewSet):
    """NOTE (fix): off-days are classroom "internals" like schedules/
    materials — a user with NO pass at all (access_level "none") shouldn't
    be able to read them, only the teacher/staff or anyone who has ever
    held a pass (active or expired). Gated behind
    _can_view_classroom_internals; only the teacher/co-teacher/moderator can
    mark or remove one."""

    serializer_class = ClassHolidaySerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): consistent with every other list endpoint in this app —
    # holiday counts are naturally small per classroom, but a classroom
    # running for years can still accumulate enough rows to be worth capping.
    pagination_class = TuitionClassPagination
    # NOTE (perf): created_by is nested (UserMiniSerializer) on every row.
    queryset = ClassHoliday.objects.select_related("created_by", "classroom", "schedule")

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get("classroom")
        if not classroom_id:
            return qs.none()
        classroom = Classroom.objects.filter(pk=classroom_id).first()
        if not classroom or not _can_view_classroom_internals(classroom, self.request.user):
            return qs.none()
        return qs.filter(classroom_id=classroom_id)

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can mark an off-day.")
        serializer.save(created_by=self.request.user)

    # NOTE (fix — Phase 3, Dart HolidayApi.update() prep): this viewset had
    # no perform_update override at all, unlike every sibling viewset in
    # this file (Notice/assigments/Poll all gate perform_update behind the
    # same manage-tier check their perform_destroy already uses). Left as a
    # plain ModelViewSet, update() would fall through to the default
    # serializer.save() with NO permission check. get_queryset() above only
    # enforces _can_view_classroom_internals (read-tier — true for anyone
    # who has EVER held a pass, active or expired), so any such user could
    # have PATCH'd another classroom's off-day (date/reason/schedule) just
    # by including ?classroom=<id> on the request — a write via a read-only
    # boundary. Gated to the same _can_manage_classroom check perform_create/
    # perform_destroy already use, closing the gap before the Dart client
    # gets a wrapper that actually calls this.
    def perform_update(self, serializer):
        if not _can_manage_classroom(serializer.instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can edit an off-day.")
        # Same classroom-reassigments guard as assigmentsViewSet/
        # NoticeViewSet.perform_update — `ClassHolidaySerializer.classroom`
        # is writable, so without this a manager of the CURRENT classroom
        # could reassign the holiday to a different one in the same PATCH,
        # bypassing that other classroom's own manage-permission check.
        new_classroom = serializer.validated_data.get("classroom")
        if new_classroom is not None and new_classroom.id != serializer.instance.classroom_id:
            raise ValidationError({"classroom": "An off-day can't be moved to a different classroom."})
        serializer.save()

    def perform_destroy(self, instance):
        if not _can_manage_classroom(instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can remove an off-day.")
        instance.delete()


# ---------------------------------------------------------------------------
# 19. NOTICE BOARD
# ---------------------------------------------------------------------------
class NoticeViewSet(viewsets.ModelViewSet):
    """NOTE (fix): notices are classroom "internals" like schedules/
    materials — a user with NO pass at all (access_level "none") shouldn't
    be able to read them, only the teacher/staff or anyone who has ever
    held a pass (active or expired). Reads (?classroom=<id>, newest/pinned
    first, expired ones hidden by default) are now gated behind
    _can_view_classroom_internals; only the teacher/co-teacher/moderator can
    post, pin, or delete a notice."""

    serializer_class = NoticeSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a long-running classroom's notice board grows unbounded
    # (every announcement ever posted) — same class of gap as above.
    pagination_class = TuitionClassPagination
    # NOTE (perf): posted_by is nested (UserMiniSerializer) on every row.
    queryset = Notice.objects.select_related("posted_by", "classroom")

    # NOTE (perf — fix): same version-based caching pattern as
    # ClassroomViewSet.list above, scoped per classroom instead of
    # platform-wide (see get_notice_list_cache_version in models.py). A
    # short TTL is kept on top of the version bump for the same
    # belt-and-braces reason as the classroom list (a raw bulk .update()
    # or data migration wouldn't fire the post_save/post_delete signal
    # that bumps the version).
    LIST_CACHE_TTL_SECONDS = 60

    def list(self, request, *args, **kwargs):
        classroom_id = request.query_params.get("classroom")
        if not classroom_id:
            # get_queryset() below already returns .none() for this case —
            # no point building a cache key with no classroom to scope it to.
            return super().list(request, *args, **kwargs)

        version = get_notice_list_cache_version(classroom_id)
        # Cache key is per-USER, not just per-classroom+query-string: two
        # different users hitting the same classroom can get different
        # results (include_expired aside, _can_view_classroom_internals
        # itself can differ — teacher/staff vs enrolled student vs a user
        # with no access at all, who gets .none()). Scoping by user avoids
        # ever leaking one user's cached page to another.
        query_fingerprint = hashlib.sha256(
            request.get_full_path().encode("utf-8")
        ).hexdigest()
        cache_key = f"tuitionclass:notice_list:{request.user.id}:{version}:{query_fingerprint}"

        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        response = super().list(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            # NOTE (fix — expired notices lingering in cache, see
            # seconds_until_next_notice_expiry() in models.py): a flat
            # LIST_CACHE_TTL_SECONDS meant an expired notice could keep
            # showing in an already-cached page for up to a full minute
            # after expires_at passed, with nothing in that classroom
            # changing to bump the version and bust it sooner. Capping the
            # timeout to "when does the next notice in this classroom
            # expire" makes the cache self-invalidate right when it
            # actually needs to, without shortening the TTL for every
            # classroom (most of which have no expires_at set at all).
            ttl = seconds_until_next_notice_expiry(classroom_id, self.LIST_CACHE_TTL_SECONDS)
            cache.set(cache_key, response.data, timeout=ttl)
        return response

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get("classroom")
        if not classroom_id:
            return qs.none()
        classroom = Classroom.objects.filter(pk=classroom_id).first()
        if not classroom or not _can_view_classroom_internals(classroom, self.request.user):
            return qs.none()
        qs = qs.filter(classroom_id=classroom_id)
        if not _is_truthy(self.request.query_params.get("include_expired")):
            qs = qs.exclude(expires_at__lt=timezone.now())
        return qs

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can post a notice.")
        notice = serializer.save(posted_by=self.request.user)
        # Only URGENT notices fan out an in-app notification to every
        # currently-enrolled student — routine notices would otherwise spam
        # a notification row per student per post. bulk_create keeps this
        # to one INSERT regardless of classroom size.
        if notice.priority == Notice.Priority.URGENT:
            student_ids = list(
                PassPurchase.objects.filter(
                    class_pass__classroom=classroom,
                    status=PassPurchase.Status.SUCCESS,
                    is_active=True,
                    expires_at__gt=timezone.now(),
                ).values_list("student_id", flat=True).distinct()
            )
            create_bulk_notifications(
                recipients=student_ids,
                notif_type=Notification.NotifType.NOTICE_POSTED,
                title=f"Urgent: {notice.title}",
                message=f"New urgent notice in '{classroom.title}'.",
                classroom=classroom,
            )
            # NOTE (fix): the bulk in-app fan-out above never pushed —
            # an "urgent" notice sitting unseen in the bell icon until the
            # student happens to open the app defeats the point of marking
            # it urgent. Queued as one task (not one task per student) so a
            # large classroom's fan-out doesn't flood the queue with
            # thousands of individual jobs; the task itself loops
            # best-effort per recipient.
            from ..tasks import notify_notice_posted

            _safe_delay(notify_notice_posted, notice.id, student_ids)

    # NOTE (fix — Phase 3 follow-up, same classroom-reassigments gap as
    # assigmentsViewSet.perform_update above): `NoticeSerializer.classroom`
    # is writable too — reachable now that NoticeApi.update() (Dart) sends
    # full `Notice.toJson()` (includes `classroom`) on PATCH. Blocked the
    # same way: permission is checked against the notice's current
    # classroom, and the classroom itself can't be changed via update.
    def perform_update(self, serializer):
        if not _can_manage_classroom(serializer.instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can edit this notice.")
        new_classroom = serializer.validated_data.get("classroom")
        if new_classroom is not None and new_classroom.id != serializer.instance.classroom_id:
            raise ValidationError({"classroom": "A notice can't be moved to a different classroom."})
        serializer.save()

    def perform_destroy(self, instance):
        if not _can_manage_classroom(instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can delete this notice.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def pin(self, request, pk=None):
        notice = self.get_object()
        if not _can_manage_classroom(notice.classroom, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can pin a notice.")
        notice.is_pinned = not notice.is_pinned
        notice.save(update_fields=["is_pinned"])
        return Response(NoticeSerializer(notice).data)


# ---------------------------------------------------------------------------
# 20. CLASS QUERY / DOUBT (student asks, teacher/co-teacher/moderator answers)
# ---------------------------------------------------------------------------
class ClassQueryViewSet(viewsets.ModelViewSet):
    """Students ask a question/doubt about a classroom (optionally scoped to
    one session); the classroom's teacher, co-teacher, or moderator answers
    it via the `answer` action.

    Visibility:
        - GET .../queries/?classroom=<id> — if the caller manages that
          classroom (teacher/co-teacher/moderator), they see every query for
          it; anyone else only sees the ones they personally asked.
        - GET .../queries/ (no classroom filter) — a "my doubts" list: every
          query the caller has asked, across all classrooms.
    """

    serializer_class = ClassQuerySerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a classroom's doubts/queries list grows unbounded over its
    # lifetime — same class of gap as above.
    pagination_class = TuitionClassPagination
    # NOTE (perf): asked_by/answered_by are both nested (UserMiniSerializer).
    queryset = ClassQuery.objects.select_related("asked_by", "answered_by", "classroom")

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get("classroom")
        if classroom_id:
            qs = qs.filter(classroom_id=classroom_id)
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            if classroom and _can_manage_classroom(classroom, self.request.user):
                return qs
        return qs.filter(asked_by=self.request.user)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        user = self.request.user
        if getattr(user, "is_authenticated", False):
            # One query: which questions the caller has upvoted (small set - per person).
            ctx["upvoted_ids"] = set(
                ClassQueryUpvote.objects.filter(user=user).values_list("query_id", flat=True)
            )
        return ctx

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        # FEATURE (trial/demo access): a running trial may ask a doubt too.
        if not (
            _can_view_classroom_internals(classroom, self.request.user)
            or _has_active_trial(classroom, self.request.user)
        ):
            raise PermissionDenied("A pass (active or expired) is required to ask a question in this classroom.")
        serializer.save(asked_by=self.request.user)
        # Live Q&A: a question tied to a session shows up in everyone's queue at once.
        q = serializer.instance
        if q.session_id:
            broadcast_to_session(q.session_id, "query.created", ClassQuerySerializer(q, context={"upvoted_ids": set()}).data)

    # NOTE (fix — Phase 3, Dart QueryApi.update() prep): no perform_update
    # override existed here. ClassQuerySerializer locks down status/answer/
    # answered_by/answered_at as read_only (only the `answer` action below
    # can touch those), but — corrected from an earlier pass of this same
    # note — `classroom` and `session` are NOT read_only, so WHO could call
    # this and WHAT it could touch were both wide open. get_queryset() above
    # returns every query for a classroom (any student's) to a caller who
    # manages that classroom, so a teacher/co-teacher/moderator sending
    # ?classroom=<id> on a PATCH could otherwise rewrite a STUDENT's own
    # question, or a student could reassign their own query's `classroom`
    # to one they don't have `_can_view_classroom_internals` access to at
    # all — bypassing perform_create's access check entirely, since it only
    # runs on create. Restricted to: only the original asker may edit, only
    # while still unanswered (same "frozen after the fact" pattern as
    # assigmentsSubmissionViewSet.perform_update's graded_at check), and the
    # classroom can't be changed via update (mirrors the same guard on
    # assigments/Notice/ClassHoliday/LivePoll above). `QueryApi.update()`
    # (Dart) only ever sends `question`, but the endpoint itself doesn't
    # rely on the client to keep behaving.
    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.asked_by_id != self.request.user.id:
            raise PermissionDenied("You can only edit your own question.")
        if instance.status == ClassQuery.Status.ANSWERED:
            raise ValidationError("This question has already been answered and can no longer be edited.")
        new_classroom = serializer.validated_data.get("classroom")
        if new_classroom is not None and new_classroom.id != instance.classroom_id:
            raise ValidationError({"classroom": "A question can't be moved to a different classroom."})
        serializer.save()

    @action(detail=True, methods=["post"])
    def answer(self, request, pk=None):
        query = self.get_object()
        if not _can_manage_classroom(query.classroom, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can answer a query.")
        serializer = ClassQueryAnswerSerializer(query, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(
            answered_by=request.user, answered_at=timezone.now(), status=ClassQuery.Status.ANSWERED
        )
        create_notification(
            recipient=query.asked_by,
            notif_type=Notification.NotifType.QUERY_ANSWERED,
            title="Your doubt was answered",
            message=f"'{query.question[:60]}' has been answered in '{query.classroom.title}'.",
            classroom=query.classroom,
            session=query.session,
        )
        from ..tasks import notify_query_answered

        _safe_delay(notify_query_answered, query.id)
        if query.session_id:
            broadcast_to_session(query.session_id, "query.updated", ClassQuerySerializer(query, context={"upvoted_ids": set()}).data)
        return Response(ClassQuerySerializer(query, context=self.get_serializer_context()).data)

    @action(detail=False, methods=["get"], url_path="live")
    def live(self, request):
        """Live Q&A queue for ONE session: every participant with room access sees every
        question of that session (the plain list only shows a student their own).

        GET queries/live/?session=<id> -> {"results": [...]} (max 200). Order: open questions first,
        most upvoted first (ties: oldest first = fair queue); answered questions after, newest first.
        """
        try:
            session = ClassSession.objects.select_related("classroom").get(pk=int(request.query_params.get("session", "")))
        except (ValueError, TypeError, ClassSession.DoesNotExist):
            raise ValidationError({"session": "A valid session id is required."})
        if not _has_room_access(session.classroom, request.user):
            raise PermissionDenied("A valid pass is required to see this session's questions.")
        rows = list(
            ClassQuery.objects.filter(session=session)
            .select_related("asked_by", "answered_by")
            .order_by("-created_at")[:200]
        )
        open_rows = sorted(
            (r for r in rows if r.status == ClassQuery.Status.OPEN),
            key=lambda r: (-r.upvote_count, r.created_at),
        )
        done_rows = [r for r in rows if r.status != ClassQuery.Status.OPEN]  # already newest first
        data = ClassQuerySerializer(open_rows + done_rows, many=True, context=self.get_serializer_context()).data
        return Response({"results": data})

    @action(detail=True, methods=["post"], url_path="upvote")
    def upvote(self, request, pk=None):
        """Toggle MY upvote on a live question. Body {"upvoted": true|false} (omitted = toggle).

        Only for questions tied to a session, only for people with room access, never on your own
        question, and not once it is answered. Pushes `query.updated` so every queue re-sorts.
        (Not `self.get_object()`: that queryset only contains the caller's OWN questions.)
        """
        try:
            query = ClassQuery.objects.select_related("classroom", "asked_by", "answered_by").get(pk=pk)
        except (ClassQuery.DoesNotExist, ValueError):
            raise NotFound("Question not found.")
        if not query.session_id or not _has_room_access(query.classroom, request.user):
            raise PermissionDenied("You can only upvote questions of a live session you are in.")
        if query.asked_by_id == request.user.id:
            raise ValidationError("You can't upvote your own question.")
        if query.status != ClassQuery.Status.OPEN:
            raise ValidationError("This question is already answered.")
        want = request.data.get("upvoted", None)
        if isinstance(want, str):
            want = want.strip().lower() not in ("false", "0", "")
        with transaction.atomic():
            vote = ClassQueryUpvote.objects.filter(query=query, user=request.user).first()
            if want is None:
                want = vote is None
            if want and vote is None:
                ClassQueryUpvote.objects.get_or_create(query=query, user=request.user)
            elif not want and vote is not None:
                vote.delete()
            count = ClassQueryUpvote.objects.filter(query=query).count()
            ClassQuery.objects.filter(pk=query.pk).update(upvote_count=count)
        query.upvote_count = count
        broadcast_to_session(
            query.session_id, "query.updated",
            ClassQuerySerializer(query, context={"upvoted_ids": set()}).data,
        )
        return Response({"id": query.id, "upvote_count": count, "has_upvoted": bool(want)})
