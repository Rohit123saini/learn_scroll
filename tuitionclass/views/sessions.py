"""
tuitionclass/views/sessions.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import logging
from django.conf import settings
from core.classroom_chat_bridge import resolve_parent_from_token
from core.models import Notification
from core.services import create_notification
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, Throttled, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from ..live_presence import fresh_cutoff, touch_heartbeat
from ..office_convert import OfficeConvertError, ensure_presentable_pdf
from ..livekit_utils import (
    LIVEKIT_URL,
    LiveKitError,
    ParticipantRole,
    ensure_room,
    generate_livekit_token,
    remove_participant,
    set_participant_audio_muted,
    set_participants_can_publish,
    start_room_recording,
    stop_room_recording,
    verify_webhook_event,
)
from ..models import (
    BreakoutRoom,
    ChatMessage,
    ClassMaterial,
    ClassSession,
    Classroom,
    ClassroomTrialAccess,
    LivePoll,
    PassPurchase,
    SessionCaption,
    SessionParticipant,
    SessionReaction,
    SessionReadState,
    SessionWaitlist,
)
from ..realtime import broadcast_to_session
from ..serializers import (
    BreakoutRoomSerializer,
    ClassSessionSerializer,
    LiveNowSessionSerializer,
    SessionCaptionSerializer,
    SessionEngagementReportSerializer,
    SessionParticipantSerializer,
    SessionReactionSerializer,
    SessionWaitlistSerializer,
)
from ..throttles import ParentJoinIPThrottle

from ._common import TuitionClassPagination, _safe_apply_async, _safe_delay
from .access import (
    _accessible_classroom_ids,
    _can_cohost_moderate,
    _can_manage_classroom,
    _can_moderate_session,
    _can_view_classroom_internals,
    _has_full_room_access,
    _has_room_access,
    _resolve_session_roles,
)


def _stage_can_publish(session, livekit_role, user):
    """`can_publish` override for generate_livekit_token. None = default (everyone publishes, host tier always).
    In a stage-only session a plain student publishes only while ON STAGE (so a rejoin keeps a guest's mic).

    X12: while a recording runs, a MINOR who has not acknowledged the recording notice is listen-only (False) in every
    session type - see live_safety.recording_blocks_publish; recording_consent.acknowledge lifts it."""
    from ..live_safety import recording_blocks_publish

    if recording_blocks_publish(session, livekit_role, user):
        return False
    if not getattr(session, "stage_only", False):
        return None
    if livekit_role in (ParticipantRole.HOST, ParticipantRole.CO_HOST):
        return None
    return session.participants.filter(
        user=user, left_at__isnull=True, guest_state=SessionParticipant.GuestState.ON_STAGE
    ).exists()


def _live_refusal(message, code):
    """X12 L-B: the same 403 as PermissionDenied(message) (same text, same status) but with its own stable `code`
    (`classroom_banned` / `session_kicked`), so the app can show the right 'you were removed' screen instead of the
    generic 'pass required' one. Imported lazily (live_abuse -> live_safety only, no cycle)."""
    from ..live_abuse import LiveSafetyError

    return LiveSafetyError(message, code=code, status_code=403)


def _recording_notice(session, user, participant=None):
    """X12: the `recording_notice` block of the join / token reply (text En + Hi, show / ack_required flags). Never
    raises - a broken notice must not stop anybody joining. None when it cannot be built."""
    try:
        from ..live_safety import notice_payload
        from ..recording_consent import current_participant

        return notice_payload(session, user, participant or current_participant(session, user))
    except Exception:
        logging.getLogger(__name__).exception("recording notice failed (session %s)", getattr(session, "id", None))
        return None


# ---------------------------------------------------------------------------
# 🔧 CONSOLIDATION FIX (post-Task-9/11 gap-fix pass) — parent-join used to
# authenticate a parent via a bespoke, stateless *signed* token
# (`django.core.signing`, previously salted via a now-deleted
# `PARENT_JOIN_TOKEN_SALT` — see TASK 20 note below) that had
# nothing to do with the `ParentAccessCode`/`ParentToken` DB rows the rest
# of the parent-portal feature set (teacher-generated codes, report
# cards, parent-mode query threads — see `parent_link_views.py`) already
# runs on. That was a genuine, unreconciled two-mechanism problem: a
# parent given a `ParentAccessCode` code had no way to actually join a
# live session with it (this endpoint only accepted a signed link), and a
# parent given a signed join-link had no way to see report cards or ask
# a query (those only accept a `ParentAccessCode`-backed token). Signed
# links also could never be revoked — no DB row to flip `is_active` on,
# unlike a `ParentAccessCode`.
#
# Fixed by switching `parent_join()` below onto the SAME resolution every
# other parent-facing endpoint uses —
# `core.classroom_chat_bridge.resolve_parent_from_token()`, the exact
# function `tuitionclass/permissions.py`'s `HasValidParentSessionToken` was
# always meant to gate this endpoint with (see that class's own
# docstring, which already named this action as its intended caller).
# One parent identity, one revocable code, works for every parent-facing
# feature now.
#
# NOTE — `resolve_parent_from_token()` is called directly in the view
# body below rather than via `HasValidParentSessionToken` as a
# `permission_class`. DRF's `APIView.initial()` runs `check_permissions()`
# BEFORE `check_throttles()`; if the permission class rejected a bad
# token, `ParentJoinIPThrottle` below would never even see (and so never
# count) the failed attempt — quietly defeating the one thing that
# throttle exists for (rate-limiting brute-forced/replayed token
# guesses). Keeping `permission_classes=[AllowAny]` on the action and
# resolving the token inside the body keeps "throttle counts every
# attempt, valid or not" true.
#
# TASK 20 (this pass) — the old stateless signed-token scheme this
# section used to also define (`PARENT_JOIN_TOKEN_SALT`,
# `generate_parent_join_token()`, and `serializers.ParentJoinSerializer`)
# has been deleted. A project-wide grep for all three names turned up no
# remaining callers/importers beyond their own now-removed definitions —
# `parent_join()` has resolved tokens via `resolve_parent_from_token()`
# exclusively since the CONSOLIDATION FIX above, and nothing else in the
# codebase ever referenced the old signed-token helpers.
# ---------------------------------------------------------------------------


class ClassSessionViewSet(viewsets.ModelViewSet):
    """NOTE (fix): same gap as ClassScheduleViewSet above — a session's
    scheduled_start/room_id/etc is internal detail, not public listing
    info. Gated behind _can_view_classroom_internals; the actual seat/
    LiveKit-access check on join()/token() (_has_room_access) is unchanged
    and stays the real security boundary for entering the room."""

    serializer_class = ClassSessionSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a long-running weekly classroom accumulates ClassSession
    # rows indefinitely (one per past occurrence, forever) — same
    # unbounded-growth class as chat/coin-ledger. Explicit pagination here
    # for the same reason.
    pagination_class = TuitionClassPagination
    # NOTE (fix): join()/token() below pass throttle_scope="..." as an
    # extra @action kwarg (DRF's documented per-action ScopedRateThrottle
    # pattern). DRF's ViewSetMixin.as_view() validates every such extra
    # kwarg against `hasattr(cls, key)` at ROUTER-REGISTRATION time (i.e.
    # on every server start / URL import), not per-request — so without a
    # base `throttle_scope` attribute existing on this class, the whole
    # app fails to boot with "ClassSessionViewSet() received an invalid
    # keyword 'throttle_scope'" the moment urls.py is imported. The value
    # here is irrelevant (each action overrides it via its own
    # throttle_scope=... kwarg at request time) — only its *existence* as
    # a class attribute matters for this check to pass.
    throttle_scope = None
    # NOTE (perf): classroom_title is a dotted source ("classroom.title") on
    # every row — select_related avoids one extra query per session listed.
    queryset = ClassSession.objects.select_related("classroom")

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get("classroom")
        user = self.request.user
        if classroom_id:
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            if not classroom or not _can_view_classroom_internals(classroom, user):
                return qs.none()
            return qs.filter(classroom_id=classroom_id)
        return qs.filter(classroom_id__in=_accessible_classroom_ids(user))

    # -----------------------------------------------------------------
    # NEW (Home Module Task 3 — "Live Now" row). CONFIRMED: neither
    # candidate the frontend task doc flagged already existed — plain
    # `GET sessions/` (get_queryset() above) DOES already return every
    # classroom the caller can access when called with no ?classroom=
    # filter, but it reads no status filter at all (client would have to
    # fetch a full, unbounded, non-live-only page and filter/paginate
    # client-side), and ClassSessionSerializer carries several internal-
    # detail fields (whiteboard/spotlight/recording) that are unnecessary
    # weight — and arguably over-exposure — for a home-screen strip
    # showing OTHER classrooms' sessions the caller may not even be
    # inside right now. So: a real, purpose-built action, Option-A style
    # (per the task doc), not a client-side loop over Option B.
    #
    # Scope: same `_accessible_classroom_ids(user)` get_queryset() already
    # uses for the no-?classroom= case — classrooms the caller teaches/
    # staffs, or holds/held a successful pass for. No separate
    # has_access()-per-classroom loop needed since that's exactly what
    # this helper already computes in one query.
    #
    # No pagination (TuitionClassPagination is for unbounded history lists;
    # a home strip only ever renders a handful of cards) — capped by
    # ?limit=, same clamped-int pattern as ClassroomViewSet.recommended()
    # above, just a tighter ceiling since this is a horizontal card row,
    # not a scrollable list.
    # -----------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="live-now")
    def live_now(self, request):
        limit = min(int(request.query_params.get("limit", 10) or 10), 20)
        sessions = (
            ClassSession.objects.filter(
                classroom_id__in=_accessible_classroom_ids(request.user),
                status=ClassSession.Status.LIVE,
                # LV1: a LIVE row whose host stopped heartbeating is a ghost
                # (crashed app) — same freshness rule as the story tray.
                live_heartbeat_at__gte=fresh_cutoff(),
            )
            .select_related("classroom", "classroom__teacher")
            .annotate(
                live_participant_count=Count(
                    "participants", filter=Q(participants__left_at__isnull=True)
                )
            )
            .order_by("-actual_start")[:limit]
        )
        serializer = LiveNowSessionSerializer(sessions, many=True, context={"request": request})
        return Response(serializer.data)

    # NOTE (fix, CRITICAL): this viewset is a plain ModelViewSet with NO
    # perform_create/perform_update/perform_destroy override at all — every
    # other "classroom internals" viewset in this file (schedules,
    # materials, assigmentss, notices, holidays...) locks writes behind
    # _can_manage_classroom, but sessions never got that treatment. As
    # shipped, ANY authenticated user could:
    #   - POST a brand-new, immediately-joinable ClassSession onto ANY
    #     classroom (not just one they teach), bypassing schedule
    #     generation entirely;
    #   - PATCH someone else's session — including flipping `status` to
    #     COMPLETED/CANCELLED, which the post_save signal in signals.py
    #     treats as a real end-of-class event: it tears down the LiveKit
    #     room, force-closes every open poll, force-checks-out every
    #     participant, and wipes the waitlist. That's a one-request DoS
    #     against a tuition class run by anyone with an account, not just a
    #     bug — kicking out an active teacher's students mid-lecture;
    #   - overwrite `recording_url` with an arbitrary link shown to every
    #     student as "the class recording" (a phishing vector), or
    #     silently DELETE the session outright.
    # Locked down to the same _can_manage_classroom check every sibling
    # viewset already uses. Manual creation stays available for a teacher
    # adding an ad-hoc session outside their normal recurrence rule; the
    # normal path is still schedule-driven generation.
    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can create a session.")
        # NOTE (fix): nothing stopped a session being created on a classroom
        # that's inactive/closed (see ClassroomViewSet.close — refunds every
        # active pass, then deactivates) or soft-deleted. A "closed" or
        # deleted classroom should never gain a fresh joinable session —
        # students who were just refunded could otherwise be shown a live
        # class to join with no valid pass path back in, and a soft-deleted
        # classroom's history should stay frozen, not keep growing.
        if not classroom.is_active or classroom.is_deleted:
            raise ValidationError("Can't create a session on an inactive or deleted classroom.")
        serializer.save()

    # NOTE (fix — writable-classroom-FK reassigments sweep, item 5): both
    # `classroom` and `schedule` are writable on ClassSessionSerializer,
    # but permission here was only ever checked against the OLD
    # `serializer.instance.classroom` — a co-teacher/moderator on
    # classroom A could edit a session that belongs to A while also
    # reassigning `classroom` (or `schedule`) to a DIFFERENT
    # classroom/schedule in the same PATCH, one they may have no manage
    # rights on. Same bug-class as ClassScheduleViewSet just above;
    # blocked outright since a session isn't meant to move between
    # classrooms/schedules after creation.
    def perform_update(self, serializer):
        if not _can_manage_classroom(serializer.instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can edit this session.")
        new_classroom = serializer.validated_data.get("classroom")
        if new_classroom is not None and new_classroom.id != serializer.instance.classroom_id:
            raise ValidationError({"classroom": "A session can't be moved to a different classroom."})
        new_schedule = serializer.validated_data.get("schedule")
        if new_schedule is not None and new_schedule.id != serializer.instance.schedule_id:
            raise ValidationError({"schedule": "A session can't be reassigned to a different schedule."})
        # NOTE (fix): a teacher flipping a session's status to CANCELLED via
        # PATCH previously told nobody — students who had it on their
        # schedule (and anyone with a ClassReminder queued for it) only
        # found out by opening the app and noticing the session was gone
        # from their list. Compared before/after save so this only fires on
        # an actual transition into CANCELLED, not on every unrelated edit
        # (title/description/etc.) to an already-cancelled session.
        was_cancelled = serializer.instance.status == ClassSession.Status.CANCELLED
        session = serializer.save()
        if session.status == ClassSession.Status.CANCELLED and not was_cancelled:
            from ..tasks import notify_session_cancelled

            _safe_delay(notify_session_cancelled,
                classroom_id=session.classroom_id,
                classroom_title=session.classroom.title,
                session_id=session.id,
                scheduled_start_iso=timezone.localtime(session.scheduled_start).strftime("%d %b, %I:%M %p"),
                exclude_user_id=self.request.user.id,
            )

    def perform_destroy(self, instance):
        if not _can_manage_classroom(instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can delete this session.")
        # NOTE (fix): same gap as the CANCELLED-status case above, for the
        # DELETE path — captured before the row is gone since the task
        # can't look a deleted session back up by id.
        classroom_id = instance.classroom_id
        classroom_title = instance.classroom.title
        session_id = instance.id
        scheduled_start_iso = timezone.localtime(instance.scheduled_start).strftime("%d %b, %I:%M %p")
        instance.delete()

        from ..tasks import notify_session_cancelled

        _safe_delay(notify_session_cancelled,
            classroom_id=classroom_id,
            classroom_title=classroom_title,
            session_id=session_id,
            scheduled_start_iso=scheduled_start_iso,
            exclude_user_id=self.request.user.id,
        )

    # NOTE (perf/abuse): join/token both call out to LiveKit's server API
    # (ensure_room + a fresh JWT). Neither was rate-limited, so a buggy
    # client (retry loop on a flaky network) or a deliberate abuser could
    # hammer LiveKit's Room Service through us with no backpressure. Scoped
    # throttle here — add matching rates to DRF's DEFAULT_THROTTLE_RATES in
    # settings.py, e.g. {"session_join": "20/min", "session_token": "30/min"}.
    @action(detail=True, methods=["post"], throttle_classes=[ScopedRateThrottle], throttle_scope="session_join")
    def join(self, request, pk=None):
        """Gate joining behind Classroom.has_access(); handles waitlist overflow;
        ensures the LiveKit room exists and issues a role-scoped join token.

        The seat-count check + participant creation happen inside a row-locked
        transaction (select_for_update on the session) so two students hitting
        /join/ at the same instant can't both slip in past max_participants —
        the second request's lock acquisition waits for the first to commit,
        then re-reads the count. LiveKit network calls stay outside the lock
        so we're not holding a DB row lock open for the duration of an HTTP
        call to LiveKit's API.

        NOTE (fix — simplification): the actual join logic now lives in the
        module-level `_perform_join()` below, shared with
        `ClassroomViewSet.start_or_join()` — that endpoint is the one call
        the app makes to figure out "is there a session, and can I get into
        it" (previously 2-4 separate client-side calls: list live sessions,
        list scheduled ones, sort them client-side, maybe create an ad-hoc
        one, then join — see classroom_detail_screen.dart's old
        _enterClass/_offerStartClassNow/_startClassNow, now collapsed into
        one round trip). This action stays as a thin wrapper so directly
        joining a KNOWN session id still works exactly as before.
        """
        return _perform_join(self.get_object(), request.user)



    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        """Teacher/co-teacher/moderator: end the live session.

        This stays a thin status transition on purpose — room teardown,
        closing open polls, and force-checkout of remaining participants are
        handled by the post_save signal on ClassSession (see tuitionclass/signals.py).
        That way the exact same cleanup fires no matter which path marks a
        session COMPLETED: this action, the Django admin, or a future
        scheduled task that auto-completes sessions past their scheduled_end.

        NOTE (fix — pass escrow was charging the wrong date): actual_end
        was declared on the model and PassPurchase.charge_for_session()
        already read it (falling back to scheduled_start only when it's
        None) to decide WHICH calendar day of the pass's escrow to release
        — but nothing on any path ever actually set it, so that fallback
        was silently the only thing that ever ran. Harmless the vast
        majority of the time (a class usually ends the same calendar day
        it was scheduled to start), but wrong the moment a session runs
        past midnight or a teacher ends it well after scheduled_start's
        date — the escrow charge landed on the WRONG day, which matters
        because charging is per-calendar-day (see the unique
        (purchase, date) constraint on PassDailyCharge). Stamped here,
        before the save that flips status (and therefore before the
        post_save signal that reads it), so charge_for_session sees the
        real end time on its very first attempt.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can end the session.")

        session.status = ClassSession.Status.COMPLETED
        session.actual_end = timezone.now()
        session.save(update_fields=["status", "actual_end"])

        return Response({"detail": "Session ended.", "status": session.status})

    @action(detail=True, methods=["get"], url_path="engagement-report")
    def engagement_report(self, request, pk=None):
        """NEW (Pass 14 audit — post-session engagement report). Attendance,
        chat activity, and poll-participation summary for a completed
        session, for the teacher/co-teacher/moderator to review afterward
        — same `_can_moderate_session` boundary as `end()` above, since
        this is classroom analytics for the host, not something a student
        needs a view into.

        Normally just reads back `ClassSession.engagement_report`,
        computed once by tasks.build_engagement_report the moment the
        session hits COMPLETED (see ClassSession.compute_engagement_report's
        docstring in models.py) — never recomputed per-request, so a
        teacher re-opening the report a dozen times doesn't re-run five
        aggregate queries a dozen times. Falls back to computing (and
        persisting) it here on the rare request that lands in the gap
        between a session completing and that queued task actually
        running, so the teacher never sees a hard error for a session
        that has, in fact, already ended.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can view the engagement report."
            )
        if session.status != ClassSession.Status.COMPLETED:
            raise ValidationError({"detail": "Engagement report is only available once the session has ended."})

        if session.engagement_report is None:
            session.engagement_report = session.compute_engagement_report()
            session.save(update_fields=["engagement_report"])

        return Response(SessionEngagementReportSerializer(session.engagement_report).data)

    @action(detail=True, methods=["post"], throttle_classes=[ScopedRateThrottle], throttle_scope="session_token")
    def token(self, request, pk=None):
        """Issue a fresh LiveKit token WITHOUT creating a SessionParticipant
        row or flipping session status/seat-count — for reconnecting a
        dropped call, joining from a second device, or manually testing a
        LiveKit client against this room. Gated behind the same access rule
        as join(); shown on class details as a "Generate token" action.
        """
        session = self.get_object()
        user = request.user

        if not _has_full_room_access(session.classroom, user):
            raise PermissionDenied("A valid pass is required to join this class.")
        # NOTE (fix): same host/staff time-window exemption as join() above
        # — resolve role first so is_joinable() knows whether to skip the
        # scheduled_start/scheduled_end window for this caller.
        livekit_role, participant_role = _resolve_session_roles(session, user)
        is_host = participant_role == SessionParticipant.Role.HOST
        if not session.is_joinable(is_host=is_host):
            raise ValidationError("This session is not joinable right now.")
        if session.participants.filter(user=user, kicked_at__isnull=False).exists():
            raise _live_refusal("You've been removed from this session by a moderator and can't rejoin it.", "session_kicked")

        if is_host:
            touch_heartbeat(session.id)  # LV1: host reconnecting = still live

        room_name = str(session.room_id)
        # NOTE (fix — was `except LiveKitError as exc: return Response(...,
        # status=503)`): LiveKitError is now a DRF APIException (see
        # livekit_utils.py), so simply letting it propagate gives the
        # standard {"detail", "code"} envelope via tuitionclass_exception_handler
        # instead of a hand-built Response that bypassed it. No try/except
        # needed here — nothing to clean up on failure in token()'s case
        # (unlike _perform_join(), this doesn't create a participant row).
        ensure_room(room_name=room_name, max_participants=session.classroom.max_participants)
        livekit_token = generate_livekit_token(
            room_name=room_name,
            user_id=user.id,
            user_name=user.get_full_name() or user.username,
            role=livekit_role,
            can_publish=_stage_can_publish(session, livekit_role, user),
        )

        return Response(
            {
                "room_id": room_name,
                "role": participant_role,
                "livekit_role": livekit_role,
                "livekit_url": LIVEKIT_URL,
                "livekit_token": livekit_token,
                "recording_notice": _recording_notice(session, user),
            },
            status=200,
        )

    @action(detail=True, methods=["post"], throttle_classes=[ScopedRateThrottle], throttle_scope="session_heartbeat")
    def heartbeat(self, request, pk=None):
        """LV1 — host presence ping (teacher / co-teacher / moderator only).

        The host's app calls this every ~20s while it is inside the live room.
        It keeps the session's story-tray "LIVE" ring alive; if the pings stop
        (app killed, no network) the ring drops after LIVE_HEARTBEAT_TTL_SECONDS
        and tasks.sweep_stale_live_sessions eventually completes the session.
        Cheap on purpose: one indexed UPDATE, no LiveKit call, no serializer.
        Returns {"live": false} (200) when the session is no longer LIVE so the
        client can stop its timer without treating it as an error.
        """
        session = get_object_or_404(ClassSession.objects.select_related("classroom"), pk=pk)
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the host can send a live heartbeat.")
        return Response({"live": bool(touch_heartbeat(session.id))})

    # NOTE: no throttle_scope="..." kwarg here (unlike join()/token() above)
    # — ScopedRateThrottle keys off request.user, which doesn't exist for
    # an unauthenticated caller. ParentJoinIPThrottle sets its own `scope`
    # class attribute instead (see throttles.py) — that's what
    # DEFAULT_THROTTLE_RATES["session_parent_join_ip"] in settings.py
    # actually keys against.
    @action(
        detail=True, methods=["post"], url_path="parent-join",
        permission_classes=[AllowAny],
        throttle_classes=[ParentJoinIPThrottle],
    )
    def parent_join(self, request, pk=None):
        """A parent (no platform account) presents the `parent_token`
        their `ParentAccessCode`/`ParentToken` was issued (see
        `ClassroomParentCodeGenerateView` / the student's own
        self-generate flow in the `message` app) — resolved via
        `core.classroom_chat_bridge.resolve_parent_from_token()`, the
        same call every other parent-facing endpoint uses (see the
        CONSOLIDATION FIX module note above for why this replaced the
        old bespoke signed-token scheme). Only if
        it resolves to a student who currently has valid access to THIS
        classroom does the parent get an observer-role LiveKit token for
        this session. Never creates a SessionParticipant row: a watching
        parent isn't a seat/attendance/waitlist participant, so this
        deliberately mirrors token() (issue-only) rather than join()
        (which creates one) — same reasoning token()'s own docstring
        already gives for why it skips participant-row creation.
        """
        # get_object() is deliberately NOT used here: it goes through
        # get_queryset(), which scopes by
        # _accessible_classroom_ids(self.request.user) — meaningless (and
        # would just 404 everyone) for an AnonymousUser. Fetch directly
        # instead, same as ClassroomCreateGroupView/ClassroomGroupStatusView
        # do for the same underlying reason.
        session = get_object_or_404(
            ClassSession.objects.select_related("classroom"), pk=pk
        )

        # Resolved here in the body (NOT via HasValidParentSessionToken as
        # a permission_class) so ParentJoinIPThrottle above still counts
        # every attempt, valid or not — see the CONSOLIDATION FIX module
        # note above for the full reasoning
        # (permission_classes run before check_throttles in DRF, so
        # gating here via a permission class would let a bad-token guess
        # skip the throttle counter entirely).
        resolution = resolve_parent_from_token(request.data.get("parent_token"))
        if resolution is None:
            raise PermissionDenied("Ye parent link invalid ya expire ho chuka hai.")
        student = resolution.student

        classroom = session.classroom
        if not classroom.has_access(student):
            raise PermissionDenied("Ye bachcha is classroom ka valid student nahi hai.")
        if not session.is_joinable(is_host=False):
            raise ValidationError("Ye session abhi joinable nahi hai.")
        if session.participants.filter(user=student, kicked_at__isnull=False).exists():
            raise PermissionDenied(
                "Is student ko is session se remove kiya gaya hai — parent bhi is waqt join nahi kar sakte."
            )

        room_name = str(session.room_id)
        ensure_room(room_name=room_name, max_participants=session.classroom.max_participants)
        # Distinct LiveKit identity from the student's own — a parent
        # watching alongside their (possibly also-connected) child must
        # never collide with the child's own room identity.
        # NOTE (Task 4 fix) — `ParticipantRole` (livekit_utils.py) only
        # defines HOST/CO_HOST/STUDENT/PARENT_OBSERVER; there is no
        # `.OBSERVER` member, so this used to raise `AttributeError` on
        # every real call. `PARENT_OBSERVER` is the actual role
        # `_grants_for_role` already has a branch for (subscribe-only,
        # hidden=True) — matches this method's own "observer-role" and
        # "never a room participant in the normal sense" docstring intent
        # exactly.
        livekit_token = generate_livekit_token(
            room_name=room_name,
            user_id=f"parent-{student.id}",
            user_name=f"{student.get_full_name() or student.username} (Parent)",
            role=ParticipantRole.PARENT_OBSERVER,
        )

        return Response(
            {
                "room_id": room_name,
                "role": "observer",
                "livekit_role": ParticipantRole.PARENT_OBSERVER,
                "livekit_url": LIVEKIT_URL,
                "livekit_token": livekit_token,
                "student_id": student.id,
                "student_name": student.get_full_name() or student.username,
            },
            status=200,
        )

    @action(detail=True, methods=["post"], url_path="kick/(?P<user_id>[^/.]+)")
    def kick(self, request, pk=None, user_id=None):
        """Teacher/co-teacher/moderator: remove a disruptive participant from
        the live LiveKit room AND block them from rejoining THIS session.

        NOTE (fix): this used to only call remove_participant() + mark
        left_at — nothing stopped the same user calling /join/ or /token/
        again immediately after. Now stamps kicked_at, which join()/token()
        check and refuse re-entry on. Scoped to this one session (not the
        whole classroom) — a heated moment in one class shouldn't
        permanently lock someone out of a classroom they've paid for; a
        teacher who wants that can still deactivate/refund the pass.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can remove participants.")

        # NOTE (fix): let LiveKitError (a DRF APIException) propagate
        # instead of catching it just to hand-build a Response — see
        # token()'s NOTE above for why.
        # X12: the removal (LiveKit room + linked parent observer, kicked_at, waitlist promotion, realtime event) lives in
        # live_moderation.kick_participant now, shared with the moderation panel's `act` endpoint, so both paths behave
        # the same. A LiveKitError of the student's own removal still propagates (standard envelope) as before.
        from django.contrib.auth import get_user_model

        from .. import live_moderation

        live_moderation.kick_participant(session, user_id)
        live_moderation.log_event(
            session, "kick", actor=request.user, target=get_user_model().objects.filter(pk=user_id).first(),
        )
        return Response({"detail": "Participant removed and blocked from rejoining this session."})

    @action(detail=True, methods=["post"], url_path="mute/(?P<user_id>[^/.]+)")
    def mute(self, request, pk=None, user_id=None):
        """Teacher/co-teacher/moderator: force-mute (or release the mute on)
        a participant's microphone WITHOUT removing them from the room.

        NOTE (fix — daily-use classroom control was missing): kick() is a
        blunt instrument for the extremely common "please mute, I can hear
        an echo/dog barking/etc." moment — it drops the student's whole
        connection and blocks them from rejoining this session. This just
        flips the participant's already-published mic track's muted flag
        server-side via LiveKit's Room Service API; LiveKit pushes that down
        as a TrackMutedEvent to the student's own client, so their mic
        button/UI updates to reflect it immediately, same as it would if
        they'd muted themselves. Nothing here touches SessionParticipant or
        kicked_at — this is a live audio toggle, not a moderation record.

        Body: {"muted": true|false} — defaults to true (mute) when omitted,
        so a bare POST with no body is the common "mute them" case; the
        host calls this again with {"muted": false} to hand the mic back.
        """
        session = self.get_object()
        if not _can_cohost_moderate(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, moderator, or a co-host guest can mute participants.")

        muted = request.data.get("muted", True)
        if isinstance(muted, str):
            muted = muted.strip().lower() not in ("false", "0", "")

        # NOTE (fix): let LiveKitError propagate — see token()'s NOTE above.
        set_participant_audio_muted(str(session.room_id), identity=str(user_id), muted=muted)

        return Response({"detail": "Microphone muted." if muted else "Microphone unmuted."})

    @action(detail=True, methods=["post"], url_path="hand")
    def raise_hand(self, request, pk=None):
        """Any current participant (student or host) toggles THEIR OWN hand.

        NOTE (feature): the only "I want to say something right now" signal
        in the app used to be ClassQuery — a written doubt meant to be
        answered whenever, not a live in-the-moment gesture. This is that
        gesture: a plain timestamp on the caller's own SessionParticipant
        row for this session, cleared either by the student themselves
        (calling this again with {"raised": false}) or by the host via
        hand/{user_id}/lower/ below after acknowledging it.

        Body: {"raised": true|false} — defaults to true (raise) so a bare
        POST with no body is the common case.
        """
        session = self.get_object()
        participant = (
            session.participants.filter(user=request.user, left_at__isnull=True).order_by("-joined_at").first()
        )
        if participant is None:
            raise PermissionDenied("You're not currently an active participant in this session.")

        raised = request.data.get("raised", True)
        if isinstance(raised, str):
            raised = raised.strip().lower() not in ("false", "0", "")

        participant.hand_raised_at = timezone.now() if raised else None
        participant.save(update_fields=["hand_raised_at"])
        broadcast_to_session(
            session.id, "hand.raised" if raised else "hand.lowered",
            {"user_id": request.user.id, "hand_raised": raised},
        )
        return Response({"detail": "Hand raised." if raised else "Hand lowered.", "hand_raised": raised})

    @action(detail=True, methods=["post"], url_path="hand/(?P<user_id>[^/.]+)/lower")
    def lower_hand(self, request, pk=None, user_id=None):
        """Teacher/co-teacher/moderator: clear someone ELSE's raised hand —
        the normal "okay, I've seen you, go ahead / next question" action
        once they've been acknowledged, without waiting for the student to
        lower it themselves.
        """
        session = self.get_object()
        if not _can_cohost_moderate(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, moderator, or a co-host guest can lower another participant's hand.")

        updated = session.participants.filter(
            user_id=user_id, left_at__isnull=True, hand_raised_at__isnull=False
        ).update(hand_raised_at=None)
        if not updated:
            raise NotFound("That participant's hand wasn't raised.")
        broadcast_to_session(session.id, "hand.lowered", {"user_id": int(user_id), "hand_raised": False})
        return Response({"detail": "Hand lowered."})

    @action(detail=True, methods=["post"], url_path="whiteboard")
    def whiteboard(self, request, pk=None):
        """FEATURE (fix — whiteboard persistence): checkpoint the
        collaborative whiteboard to the server so it survives everyone who
        was holding it in memory leaving the room. Live sync between
        currently-connected clients still happens peer-to-peer over the
        LiveKit data channel (see live_session_screen.dart) — this is only
        the periodic/close-time autosave that a reconnecting participant,
        or a joiner who arrives before anyone with the current board has
        (re)joined, falls back to reading via the normal session GET
        (whiteboard_snapshot).

        Any current room participant can call this — same `_has_room_access`
        gate as chat/polls, NOT the tighter `_can_manage_classroom` the
        generic session PATCH uses — a student's device may hold the most
        complete stroke history, not just the host's.

        Body: {"snapshot": {<strokeId>: {...}, ...}} — the client's full
        current stroke map, or {"snapshot": null} to clear the board
        (e.g. host taps "Clear"). Always a full overwrite, same
        last-write-wins contract as any other autosave.
        """
        session = self.get_object()
        if not _has_room_access(session.classroom, request.user):
            raise PermissionDenied("You don't have access to this session's room.")

        if "snapshot" not in request.data:
            raise ValidationError({"snapshot": "This field is required (an object, or null to clear)."})
        snapshot = request.data.get("snapshot")
        if snapshot is not None and not isinstance(snapshot, dict):
            raise ValidationError({"snapshot": "Must be an object (strokeId -> stroke) or null."})

        session.whiteboard_snapshot = snapshot
        session.save(update_fields=["whiteboard_snapshot"])
        return Response({"detail": "Whiteboard saved."})

    @action(detail=True, methods=["post"], url_path="spotlight")
    def spotlight(self, request, pk=None):
        """FEATURE (fix — spotlight persistence): host-only pin, previously
        a pure LiveKit data-channel broadcast with nothing stored
        server-side — a reconnecting participant, or a fresh mid-class
        joiner, saw no spotlight at all until the host happened to
        re-broadcast one. Persists the current pin here so a reconnect/
        late join can read it straight off the session GET
        (spotlight_identity), and also re-broadcasts over the session's
        realtime channel so anyone already connected updates immediately
        too — same as every other host/moderator control below.

        Body: {"identity": "<livekit identity>"} or {"identity": null} to
        clear. Same `_can_moderate_session` gate as mute/kick/lower_hand —
        host, co-teacher, or moderator only.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can set the spotlight.")

        if "identity" not in request.data:
            raise ValidationError({"identity": "This field is required (a string identity, or null to clear)."})
        identity = request.data.get("identity")
        if identity is not None and not isinstance(identity, str):
            raise ValidationError({"identity": "Must be a string identity or null."})

        session.spotlight_identity = identity or ""
        session.save(update_fields=["spotlight_identity"])
        broadcast_to_session(session.id, "spotlight", {"identity": identity or None})
        return Response({"detail": "Spotlight updated.", "identity": identity or None})

    # Allowed slow-mode gaps (seconds). 0 = off. A closed set keeps the UI a simple picker and
    # stops a client from parking a session on an absurd value.
    SLOW_MODE_CHOICES = (0, 5, 10, 30, 60, 120)

    @action(detail=True, methods=["post"], url_path="slow-mode")
    def slow_mode(self, request, pk=None):
        """Host / co-teacher / moderator: set the chat slow-mode gap for THIS session.

        Body: {"seconds": 0|5|10|30|60|120}. Moderators are exempt from the gap themselves
        (see views/live_chat.py::_enforce_slow_mode). Pushes `chat.slow_mode` {"seconds": n} so
        every connected client updates its composer immediately; late joiners read
        `chat_slow_mode_seconds` off the session GET.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can change slow mode.")
        raw = request.data.get("seconds", 0)
        try:
            seconds = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({"seconds": "Must be a whole number of seconds."})
        if seconds not in self.SLOW_MODE_CHOICES:
            raise ValidationError({"seconds": f"Must be one of {list(self.SLOW_MODE_CHOICES)}."})
        if session.chat_slow_mode_seconds != seconds:
            session.chat_slow_mode_seconds = seconds
            session.save(update_fields=["chat_slow_mode_seconds"])
            broadcast_to_session(session.id, "chat.slow_mode", {"seconds": seconds})
        return Response({"detail": "Slow mode updated.", "seconds": seconds})

    # ----- Present PDF / slides -----------------------------------------------------------------
    # Host picks a PDF/image material -> everyone's room shows it, page-synced. State lives on the
    # session (presented_material / presented_page / presented_page_count) so a late joiner or a
    # reconnect reads it off the session GET; sockets are only the live push:
    #   present.started {material_id, title, material_type, page, page_count}
    #   present.page    {page}
    #   present.stopped {}
    # PDF and image are shown as-is (the app rasterises PDF pages itself). ppt/doc are converted to PDF once on the
    # server (office_convert.py, LibreOffice) and then shown like a PDF; video/link are refused with a clear message.
    OFFICE_TYPES = (ClassMaterial.MaterialType.PPT, ClassMaterial.MaterialType.DOC)
    PRESENTABLE_TYPES = (ClassMaterial.MaterialType.PDF, ClassMaterial.MaterialType.IMAGE) + OFFICE_TYPES
    MAX_PRESENT_PAGES = 500

    def _present_gate(self, request, session):
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can present.")
        if session.status != ClassSession.Status.LIVE:
            raise ValidationError("You can present only while the class is live.")

    @staticmethod
    def _int_field(request, name, default=None, minimum=1):
        raw = request.data.get(name, default)
        if raw is None:
            return None
        try:
            val = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({name: "Must be a whole number."})
        if val < minimum:
            raise ValidationError({name: f"Must be at least {minimum}."})
        return val

    @action(detail=True, methods=["post"], url_path="present")
    def present(self, request, pk=None):
        """Body: {"material": <id>, "page_count": n (PDF, measured by the host's device), "page": 1}."""
        session = self.get_object()
        self._present_gate(request, session)
        material_id = self._int_field(request, "material")
        if material_id is None:
            raise ValidationError({"material": "This field is required."})
        material = ClassMaterial.objects.filter(pk=material_id, classroom_id=session.classroom_id).first()
        if material is None:
            raise ValidationError({"material": "That material isn't in this classroom."})
        if material.material_type not in self.PRESENTABLE_TYPES:
            raise ValidationError({"material": "Only PDF, image, PowerPoint and Word materials can be presented."})
        if not material.file:
            raise ValidationError({"material": "This material has no file to present."})

        is_pdf = material.material_type == ClassMaterial.MaterialType.PDF
        if material.material_type in self.OFFICE_TYPES:
            # The server converts (cached) and measures the pages itself; the client's page_count is ignored.
            try:
                page_count = ensure_presentable_pdf(material)
            except OfficeConvertError as exc:
                raise ValidationError({"material": str(exc)})
        else:
            page_count = self._int_field(request, "page_count", default=1 if not is_pdf else None, minimum=1)
        if is_pdf and page_count is None:
            raise ValidationError({"page_count": "Required for a PDF."})
        if page_count > self.MAX_PRESENT_PAGES:
            raise ValidationError({"page_count": f"At most {self.MAX_PRESENT_PAGES} pages can be presented."})
        page = min(self._int_field(request, "page", default=1), page_count)

        session.presented_material = material
        session.presented_page = page
        session.presented_page_count = page_count
        session.save(update_fields=["presented_material", "presented_page", "presented_page_count"])
        payload = {
            "material_id": material.id, "title": material.title, "material_type": material.material_type,
            "page": page, "page_count": page_count,
        }
        broadcast_to_session(session.id, "present.started", payload)
        # X12: a marker on the recording ("Presenting: Chapter 3.pdf") - silent no-op when nothing is being recorded.
        from ..recording_tools import add_chapter

        add_chapter(
            session, "present", f"Presenting: {material.title}", user=request.user, ref_type="material", ref_id=material.id,
        )
        return Response(payload)

    @action(detail=True, methods=["post"], url_path="present/page")
    def present_page(self, request, pk=None):
        """Body: {"page": n}. Throttled like other chatty host actions by the viewset's defaults."""
        session = self.get_object()
        self._present_gate(request, session)
        if session.presented_material_id is None:
            raise ValidationError("Nothing is being presented.")
        page = self._int_field(request, "page")
        if page is None:
            raise ValidationError({"page": "This field is required."})
        page = min(page, max(session.presented_page_count, 1))
        if page != session.presented_page:
            session.presented_page = page
            session.save(update_fields=["presented_page"])
            broadcast_to_session(session.id, "present.page", {"page": page})
        return Response({"page": page})

    @action(detail=True, methods=["post"], url_path="present/stop")
    def present_stop(self, request, pk=None):
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can stop presenting.")
        if session.presented_material_id is not None:
            session.presented_material = None
            session.presented_page = 1
            session.presented_page_count = 0
            session.save(update_fields=["presented_material", "presented_page", "presented_page_count"])
            broadcast_to_session(session.id, "present.stopped", {})
        return Response({"detail": "Stopped presenting."})

    # ----- Live guest / stage -------------------------------------------------------------------
    # Flow: host taps "Invite to stage" (on a raised hand = "accept") -> student gets `guest.invited`
    # -> student accepts/declines -> `guest.updated` to everyone. Host (or the guest) can end it.
    # guest_state is a room state. In a STAGE-ONLY session (ClassSession.stage_only) accept/remove also flips the
    # guest's live LiveKit publish permission, and token()/join() hand plain students a listen-only token.
    def _active_participant(self, session, user_id):
        return session.participants.filter(user_id=user_id, left_at__isnull=True).order_by("-joined_at").first()

    def _push_guest(self, session, participant):
        broadcast_to_session(session.id, "guest.updated", {
            "user_id": participant.user_id, "guest_state": participant.guest_state,
            "guest_cohost": participant.guest_cohost,
        })

    @action(detail=True, methods=["post"], url_path=r"guest/(?P<user_id>[^/.]+)/invite")
    def guest_invite(self, request, pk=None, user_id=None):
        """Host/co-teacher/moderator: invite a participant on stage. Accepts their raised hand too
        (the hand is lowered). Max 4 people invited-or-on-stage at once."""
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can invite guests.")
        p = self._active_participant(session, user_id)
        if p is None:
            raise NotFound("That person is not in the session right now.")
        if p.role == "host":
            raise ValidationError("The host is already on stage.")
        # X12: a minor goes on stage only under the policy (never / after acknowledging the recording notice).
        from ..live_safety import stage_invite_error

        problem = stage_invite_error(session, p)
        if problem is not None:
            raise ValidationError({"detail": problem[1], "code": problem[0]})
        if p.guest_state:
            return Response({"detail": "Already invited.", "guest_state": p.guest_state})
        busy = session.participants.filter(left_at__isnull=True, guest_state__in=["invited", "on_stage"]).count()
        if busy >= 4:
            raise ValidationError("At most 4 guests can be on stage at once.")
        p.guest_state = "invited"
        p.hand_raised_at = None
        p.save(update_fields=["guest_state", "hand_raised_at"])
        broadcast_to_session(session.id, "hand.lowered", {"user_id": p.user_id, "hand_raised": False})
        broadcast_to_session(session.id, "guest.invited", {"user_id": p.user_id})
        self._push_guest(session, p)
        return Response({"detail": "Invite sent.", "guest_state": p.guest_state})

    @action(detail=True, methods=["post"], url_path="guest/respond")
    def guest_respond(self, request, pk=None):
        """The invited person answers: {"accept": true|false}. Only valid while state == invited."""
        session = self.get_object()
        p = self._active_participant(session, request.user.id)
        if p is None or p.guest_state != "invited":
            raise ValidationError("There is no pending stage invite for you.")
        accept = request.data.get("accept", True)
        if isinstance(accept, str):
            accept = accept.strip().lower() not in ("false", "0", "")
        if accept:
            # P-F16: the stage policy may have changed (or the invite pre-dates it) - a minor is re-checked when they ACCEPT,
            # not only when the host invites. Only the hard "never" rule is re-applied here; the recording-notice rule below
            # is unchanged.
            from ..live_safety import stage_invite_error

            _problem = stage_invite_error(session, p)
            if _problem is not None and _problem[0] == "minor_stage_not_allowed":
                p.guest_state = ""
                p.guest_at = None
                p.save(update_fields=["guest_state", "guest_at"])
                self._push_guest(session, p)
                raise ValidationError({"detail": _problem[1], "code": _problem[0]})
            # X12: the recording may have started after the invite - a minor still has to acknowledge the notice first.
            from ..live_safety import recording_blocks_publish

            livekit_role, _ = _resolve_session_roles(session, request.user)
            if recording_blocks_publish(session, livekit_role, request.user):
                raise ValidationError({
                    "detail": "This class is being recorded. Read and acknowledge the recording notice first.",
                    "code": "recording_notice_required",
                })
        p.guest_state = "on_stage" if accept else ""
        p.guest_at = timezone.now() if accept else None
        p.guest_cohost = False if not accept else p.guest_cohost
        p.save(update_fields=["guest_state", "guest_at", "guest_cohost"])
        if accept and session.stage_only:
            self._set_publish(session, [request.user.id], True)
        self._push_guest(session, p)
        return Response({"detail": "Joined the stage." if accept else "Invite declined.", "guest_state": p.guest_state})

    @action(detail=True, methods=["post"], url_path=r"guest/(?P<user_id>[^/.]+)/remove")
    def guest_remove(self, request, pk=None, user_id=None):
        """End a stage invite / guest spot. A moderator can do it for anyone (their mic is muted);
        a guest can always leave on their own."""
        session = self.get_object()
        is_self = str(user_id) == str(request.user.id)
        if not is_self and not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can remove a guest.")
        p = self._active_participant(session, user_id)
        if p is None or not p.guest_state:
            raise NotFound("That person is not a guest right now.")
        was_on_stage = p.guest_state == "on_stage"
        p.guest_state = ""
        p.guest_at = None
        p.guest_cohost = False
        p.save(update_fields=["guest_state", "guest_at", "guest_cohost"])
        if was_on_stage and session.stage_only:
            self._set_publish(session, [user_id], False)
        if was_on_stage and not is_self:
            try:
                set_participant_audio_muted(str(session.room_id), identity=str(user_id), muted=True)
            except Exception:
                logging.getLogger(__name__).exception("guest_remove: could not mute %s in session %s", user_id, session.id)
        self._push_guest(session, p)
        return Response({"detail": "Removed from stage.", "guest_state": ""})

    def _set_publish(self, session, identities, allowed):
        """Best-effort live LiveKit permission change; a failure never blocks the stage action itself because the
        next token (rejoin/reconnect) is computed from the same DB state by `_stage_can_publish`."""
        try:
            set_participants_can_publish(str(session.room_id), list(identities), allowed)
        except Exception:
            logging.getLogger(__name__).exception("stage permission update failed (session %s)", session.id)

    @action(detail=True, methods=["post"], url_path=r"guest/(?P<user_id>[^/.]+)/cohost")
    def guest_cohost(self, request, pk=None, user_id=None):
        """Moderator: make an ON-STAGE guest a co-host ({"enabled": true|false}). Co-host = chat delete/pin/unpin,
        mute, lower hand, slow-mode exempt. NOT end / kick / record / invite / breakouts. Only a real moderator can
        grant or take it back (a co-host guest cannot promote anyone)."""
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can make a co-host.")
        p = self._active_participant(session, user_id)
        if p is None or p.guest_state != "on_stage":
            raise ValidationError("Only a guest who is on stage can be made a co-host.")
        enabled = request.data.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in ("false", "0", "")
        # P-F16: a co-host can delete / pin chat and mute other people - a minor is never given that authority.
        from common import minor_policy

        if enabled and minor_policy.blocked(p.user, minor_policy.LIVE_GUEST):
            raise ValidationError({"detail": minor_policy.MINOR_COHOST_TEXT, "code": "minor_cohost_not_allowed"})
        p.guest_cohost = bool(enabled)
        p.save(update_fields=["guest_cohost"])
        self._push_guest(session, p)
        return Response({"detail": "Co-host updated.", "guest_cohost": p.guest_cohost})

    @action(detail=True, methods=["post"], url_path="stage-only")
    def stage_only(self, request, pk=None):
        """Moderator: listener mode ({"enabled": true|false}). ON: plain students lose the right to publish mic/camera
        (live LiveKit permission change + listen-only tokens) except guests on stage. OFF: everyone can publish again.
        Hand-raise, chat, reactions, polls and Q&A keep working (data channel + REST stay on)."""
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can change stage-only mode.")
        if session.status != ClassSession.Status.LIVE:
            raise ValidationError("Stage-only mode can only be changed while the session is live.")
        enabled = request.data.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in ("false", "0", "")
        enabled = bool(enabled)
        session.stage_only = enabled
        session.save(update_fields=["stage_only"])
        # Plain, non-trial students only: host tier always publishes, trial students never could.
        rows = session.participants.filter(left_at__isnull=True, role=SessionParticipant.Role.STUDENT, is_trial=False)
        if enabled:
            rows = rows.exclude(guest_state=SessionParticipant.GuestState.ON_STAGE)
        self._set_publish(session, [r.user_id for r in rows], not enabled)
        broadcast_to_session(session.id, "stage.mode", {"enabled": enabled})
        return Response({"detail": "Stage-only mode updated.", "stage_only": enabled})

    @action(
        detail=True, methods=["get", "post"], url_path="reactions",
        throttle_classes=[ScopedRateThrottle], throttle_scope="session_reaction",
    )
    def reactions(self, request, pk=None):
        """NEW (persistence fix) — durable log for the in-room emoji
        reactions (`_sendReaction` in live_session_screen.dart), which
        used to be PURE LiveKit data-channel signaling with nothing ever
        written to the database (see SessionReaction's docstring in
        models.py). Live delivery to already-connected peers is
        UNCHANGED — the client still broadcasts over the data channel
        for lowest latency; this endpoint is the persistence layer
        underneath it, called alongside that broadcast, not instead of
        it.

        POST {"reaction": "heart"} — any current room participant (same
        `_has_room_access` gate as chat/whiteboard) logs one reaction
        tap. Returns {"total": <int>} — the session's running total, so
        a reconnecting client can re-seed its `_reactionTotalCount`
        badge instead of starting back at zero.

        GET — same gate. Returns {"total": <int>, "counts": {<emoji>:
        <int>, ...}} — a lightweight summary, not the full per-tap log
        (a popular session could have thousands of taps; nobody needs
        to page through them one by one, unlike chat/captions where the
        individual lines matter).
        """
        session = self.get_object()
        if not _has_room_access(session.classroom, request.user):
            raise PermissionDenied("A valid pass is required to react in this session.")

        if request.method == "POST":
            serializer = SessionReactionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            SessionReaction.objects.create(
                session=session, user=request.user, reaction=serializer.validated_data["reaction"]
            )

        total = SessionReaction.objects.filter(session=session).count()
        if request.method == "POST":
            return Response({"total": total}, status=status.HTTP_201_CREATED)

        counts: dict[str, int] = {}
        for row in (
            SessionReaction.objects.filter(session=session).values("reaction").annotate(n=Count("reaction"))
        ):
            counts[row["reaction"]] = row["n"]
        return Response({"total": total, "counts": counts})

    @action(
        detail=True, methods=["get", "post"], url_path="captions",
        throttle_classes=[ScopedRateThrottle], throttle_scope="session_caption",
    )
    def captions(self, request, pk=None):
        """NEW (persistence fix) — durable transcript line for the LIVE
        caption feature (`speech_to_text` on-device STT in
        live_session_screen.dart). Recognition is still per-device,
        own-mic-only (see SessionCaption's docstring in models.py for
        why remote-audio STT isn't possible from this Dart file) — what
        changes is that every device's own finished line now also lands
        here, so the SERVER accumulates a transcript covering every
        participant's own speech, not just whoever's on-screen 3-line
        feed happened to still show it live. Previously the on-screen
        feed was capped to 3 lines and self-expired after 6 seconds,
        with nothing persisted anywhere.

        POST {"text": "..."} — the caller's own just-finished caption
        line (speaker is always the authenticated caller, never
        client-supplied — mirrors ChatMessageViewSet.perform_create's
        "owner comes from request.user" rule). Same `_has_room_access`
        gate as chat/reactions.

        GET — returns the session's transcript so far, oldest first,
        capped to the most recent 200 lines (a live caption transcript
        is a running log meant for "what did I miss" / post-session
        review, not something that needs full pagination like chat
        history).
        """
        session = self.get_object()
        if not _has_room_access(session.classroom, request.user):
            raise PermissionDenied("A valid pass is required to use captions in this session.")

        if request.method == "POST":
            serializer = SessionCaptionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            caption = SessionCaption.objects.create(
                session=session, speaker=request.user, text=serializer.validated_data["text"]
            )
            return Response(SessionCaptionSerializer(caption).data, status=status.HTTP_201_CREATED)

        recent = list(
            SessionCaption.objects.filter(session=session).select_related("speaker").order_by("-created_at")[:200]
        )
        recent.reverse()  # oldest first, matches the live on-screen feed's own top-to-bottom order
        return Response(SessionCaptionSerializer(recent, many=True).data)

    @action(detail=True, methods=["get"], url_path="unread")
    def unread(self, request, pk=None):
        """NEW (Pass 13) — "5 new messages" badge for a student who closed
        this session's tab and came back. Counts chat messages and polls
        created AFTER this user's last recorded SessionReadState watermark
        (see that model's docstring for the "never seen any" NULL
        handling) — a real DB count, not a read off the Pass 10 replay
        buffer, which is capped at 50 events / 15 minutes and would badly
        undercount "away since this morning".

        Own messages never count as unread (nobody needs a badge for
        their own chat sends); a poll a user already voted on still
        counts here if it was created after their watermark — "new since
        you left" is about the poll's existence, not their vote status
        (LivePollSerializer.result_counts already tells the frontend
        whether they've voted).
        """
        session = self.get_object()
        if not _has_room_access(session.classroom, request.user):
            raise PermissionDenied("A valid pass is required to view unread counts for this session.")

        state = SessionReadState.objects.filter(session=session, user=request.user).first()

        chat_qs = ChatMessage.objects.filter(session=session, is_deleted=False).exclude(sender=request.user)
        poll_qs = LivePoll.objects.filter(session=session)
        if state and state.last_read_chat_message_id is not None:
            chat_qs = chat_qs.filter(id__gt=state.last_read_chat_message_id)
        if state and state.last_seen_poll_id is not None:
            poll_qs = poll_qs.filter(id__gt=state.last_seen_poll_id)

        return Response({"chat": chat_qs.count(), "polls": poll_qs.count()})

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        """Advances the caller's own SessionReadState watermark — call
        this when the student opens/refreshes this session's chat+poll
        tab. Defaults to "everything that exists right now" (the latest
        chat message id / poll id in this session) when no explicit id is
        given, which covers the common "I opened the tab and saw
        everything" case in one call; pass {"chat_message_id": ...} and/
        or {"poll_id": ...} explicitly only if the client wants a
        narrower watermark (e.g. it only scrolled through some of a long
        backlog).
        """
        session = self.get_object()
        if not _has_room_access(session.classroom, request.user):
            raise PermissionDenied("A valid pass is required to update read state for this session.")

        chat_message_id = request.data.get("chat_message_id")
        if chat_message_id is None:
            chat_message_id = (
                ChatMessage.objects.filter(session=session, is_deleted=False).order_by("-id").values_list(
                    "id", flat=True
                ).first()
            )
        poll_id = request.data.get("poll_id")
        if poll_id is None:
            poll_id = LivePoll.objects.filter(session=session).order_by("-id").values_list("id", flat=True).first()

        state, _created = SessionReadState.objects.update_or_create(
            session=session,
            user=request.user,
            defaults={"last_read_chat_message_id": chat_message_id, "last_seen_poll_id": poll_id},
        )
        return Response(
            {"last_read_chat_message_id": state.last_read_chat_message_id, "last_seen_poll_id": state.last_seen_poll_id}
        )

    @action(detail=True, methods=["post"], url_path="start-recording")
    def start_recording(self, request, pk=None):
        """Teacher/co-teacher/moderator: start a LiveKit Room Composite
        Egress recording of this session.

        NOTE (gap fix): Classroom.recording_enabled and
        ClassSession.recording_url already existed — a teacher could see
        "Recording" as a classroom setting and there was a field to hold
        the finished link — but nothing anywhere ever called LiveKit's
        Egress API, so recording_url could never actually get filled in.
        This is that missing engine call.

        Refuses to start a second recording on top of one already running
        (egress_id already set) rather than silently losing track of the
        first job's ID. The file only actually appears at recording_url
        once LiveKit's `egress_ended` webhook lands (see
        LiveKitWebhookView) — starting this call does not itself return a
        playable URL, since the recording doesn't exist yet.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can start recording.")
        if not session.classroom.recording_enabled:
            raise ValidationError("Recording is turned off for this classroom.")
        if session.egress_id:
            raise ValidationError("A recording is already in progress for this session.")

        # NOTE (fix): let LiveKitError propagate — see token()'s NOTE above.
        egress_id = start_room_recording(str(session.room_id))

        # X12: `recording_started_at` is 0:00 of the video (every chapter / replay-chat offset is measured from it - the
        # `egress_started` webhook refines it by the few seconds LiveKit needs to warm up); `last_egress_id` survives
        # Stop so the `egress_ended` webhook can still find this session; markers of an earlier recording go.
        session.egress_id = egress_id
        session.last_egress_id = egress_id
        session.recording_started_at = timezone.now()
        session.recording_duration_seconds = None
        session.save(update_fields=["egress_id", "last_egress_id", "recording_started_at", "recording_duration_seconds"])
        from ..recording_tools import reset_timeline, seed_start_chapter

        reset_timeline(session)
        seed_start_chapter(session)
        # NOTE (fix — realtime gap): consumers.py's module docstring has
        # always promised connected clients a "recording.started" push —
        # nothing ever actually sent one, so a student's "REC" indicator
        # never lit up live; they'd only find out on their next REST call.
        # X12: the room-wide event carries the notice text (En + Hi); minors who have not acknowledged it drop to
        # listen-only at once (recording_consent.on_recording_started) and get `recording.notice_required`.
        from ..live_safety import broadcast_notice_payload
        from ..recording_consent import on_recording_started

        broadcast_to_session(
            session.id, "recording.started", {"egress_id": egress_id, "notice": broadcast_notice_payload(session)}
        )
        on_recording_started(session)
        return Response({"detail": "Recording started.", "egress_id": egress_id})

    @action(detail=True, methods=["post"], url_path="stop-recording")
    def stop_recording(self, request, pk=None):
        """Teacher/co-teacher/moderator: stop the in-progress recording.

        Clears egress_id immediately so the UI stops showing "REC" right
        away; recording_url still fills in asynchronously via the
        `egress_ended` webhook once LiveKit finishes uploading the file —
        stopping the egress job and the file being ready are two different
        moments, and this endpoint only guarantees the former.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can stop recording.")
        if not session.egress_id:
            raise ValidationError("No recording is currently in progress for this session.")

        # NOTE (fix): let LiveKitError propagate — see token()'s NOTE above.
        egress_id = session.egress_id
        stop_room_recording(egress_id)

        session.egress_id = ""
        session.save(update_fields=["egress_id"])
        # NOTE (fix — realtime gap, same class as start_recording above):
        # clears the "REC" indicator live for every connected client
        # instead of only for whoever happens to hit the REST API next.
        # The eventual recording_url still arrives separately via
        # LiveKitWebhookView's own broadcast below once the file is ready.
        broadcast_to_session(session.id, "recording.stopped", {"egress_id": egress_id})
        return Response({"detail": "Recording stopped. The file will appear here once processing finishes."})

    # FEATURE: breakout rooms. Backend for live_session_screen.dart's
    # breakout UI, which shipped assuming exactly this REST shape (see that
    # file's header comment) — `TuitionClassApi.breakoutRooms` on the Flutter
    # side was still hitting endpoints that didn't exist until now.
    @action(detail=True, methods=["get", "post"], url_path="breakout")
    def breakout(self, request, pk=None):
        """GET: the session's current breakout-room layout — an empty list
        means no breakout is running. Readable by anyone with room access
        (same _has_room_access gate as chat/polls), not just the host, so a
        student who reconnects mid-breakout finds out which room they're in
        from the server rather than a peer-to-peer signal they may have
        missed.

        POST {"room_count": int}: teacher/co-teacher/moderator only.
        Creates exactly `room_count` empty numbered rooms (1..room_count).
        Refused if a breakout is already running for this session — call
        breakout/close/ first, so a stray double-tap can't silently double
        the room count or leave half the class assigned to rooms nobody's
        tracking the size of anymore.
        """
        session = self.get_object()

        if request.method == "GET":
            if not _has_room_access(session.classroom, request.user):
                raise PermissionDenied("A valid pass is required to view this session's breakout rooms.")
            rooms = session.breakout_rooms.all()
            return Response(BreakoutRoomSerializer(rooms, many=True).data)

        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can start breakout rooms.")
        if session.breakout_rooms.exists():
            raise ValidationError("Breakout rooms are already running for this session — close them first.")

        try:
            room_count = int(request.data.get("room_count"))
        except (TypeError, ValueError):
            raise ValidationError("room_count must be a positive integer.")
        if not (1 <= room_count <= 50):
            raise ValidationError("room_count must be between 1 and 50.")

        BreakoutRoom.objects.bulk_create(
            [BreakoutRoom(session=session, room_number=n) for n in range(1, room_count + 1)]
        )
        rooms = session.breakout_rooms.all()
        return Response(BreakoutRoomSerializer(rooms, many=True).data, status=201)

    @action(detail=True, methods=["post"], url_path="breakout/assign")
    def breakout_assign(self, request, pk=None):
        """Teacher/co-teacher/moderator: put one participant into a room, or
        back into the main room with `{"room": null}`.

        Body: {"participant_id": int, "room": int|null}. [participant_id]
        is the SessionParticipant row id — same convention every other
        moderation action in this file uses (kick/mute/lower-hand all key
        off a participant/user id the same way) — not the room-scoped
        LiveKit identity string; BreakoutRoomSerializer does that
        conversion on the way out so the client never has to.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can assign breakout rooms.")

        participant = session.participants.filter(
            pk=request.data.get("participant_id"), left_at__isnull=True
        ).first()
        if participant is None:
            raise ValidationError("That participant isn't currently active in this session.")

        room_number = request.data.get("room")
        if room_number is None:
            participant.breakout_room = None
        else:
            try:
                room_number = int(room_number)
            except (TypeError, ValueError):
                raise ValidationError("room must be an integer or null.")
            room = session.breakout_rooms.filter(room_number=room_number).first()
            if room is None:
                raise ValidationError(f"Breakout room {room_number} doesn't exist for this session.")
            participant.breakout_room = room
        participant.save(update_fields=["breakout_room"])

        rooms = session.breakout_rooms.all()
        return Response(BreakoutRoomSerializer(rooms, many=True).data)

    @action(detail=True, methods=["post"], url_path="breakout/close")
    def breakout_close(self, request, pk=None):
        """Teacher/co-teacher/moderator: end the breakout. Deletes every
        BreakoutRoom row for this session — SessionParticipant.breakout_room
        is on_delete=SET_NULL, so this one delete also clears everyone's
        room assigments in a single query, no per-participant loop needed.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can close breakout rooms.")
        deleted, _ = session.breakout_rooms.all().delete()
        if not deleted:
            raise ValidationError("No breakout rooms are currently running for this session.")
        return Response({"detail": "Breakout rooms closed — everyone's back in the main room."})


# ---------------------------------------------------------------------------
# LIVEKIT WEBHOOK — server-to-server, not a user-facing endpoint.
#
# FEATURE (recording, part 2): start_recording()/stop_recording() above
# control the egress JOB, but the actual output file only exists once
# LiveKit finishes encoding + uploading it — which happens asynchronously,
# sometimes well after stop_recording() returns. LiveKit's own webhook
# mechanism is how it tells us that's done. Point your LiveKit project's
# webhook URL (in the LiveKit Cloud/self-host dashboard) at this endpoint;
# verify_webhook_event() checks the Authorization header's signature against
# LIVEKIT_API_KEY/SECRET so a request claiming to be this event can't be
# forged by anyone who doesn't hold those credentials — this is why the
# view skips normal user auth (LiveKit's server has no Django session/token)
# but is NOT unauthenticated in any meaningful sense.
# ---------------------------------------------------------------------------


def _perform_join(session, user):
    """Shared join implementation — see ClassSessionViewSet.join()'s
    docstring above and ClassroomViewSet.start_or_join() below for the two
    call sites. Moved out of the action method so both can share it without
    duplicating the seat-locking transaction / LiveKit error handling.
    """
    # NOTE (fix — ClassroomBan had no teeth in the join path): checked
    # explicitly (not just relying on Classroom.has_access() returning
    # False for a banned student) so a banned student gets a clear "you've
    # been banned" message instead of the generic "no valid pass" one —
    # they may well still hold what looks like a valid pass if the refund
    # from the ban hasn't landed yet.
    if session.classroom.bans.filter(student_id=user.id).exists():
        raise _live_refusal("You've been banned from this classroom by the teacher.", "classroom_banned")
    if not _has_full_room_access(session.classroom, user):
        raise PermissionDenied("A valid pass is required to join this class.")
    # NOTE (fix): resolved BEFORE the is_joinable() check (was resolved
    # again later, inside the transaction, for the same purpose) so the
    # host/staff time-window exemption in is_joinable() has a role to
    # check against. See is_joinable()'s docstring for why this matters.
    livekit_role, participant_role = _resolve_session_roles(session, user)
    is_host = participant_role == SessionParticipant.Role.HOST
    if not session.is_joinable(is_host=is_host):
        raise ValidationError("This session is not joinable right now.")
    # NOTE (fix): block re-entry for anyone kicked from THIS session —
    # see SessionParticipant.kicked_at / kick() above for why this
    # check didn't exist before (moderation had no teeth without it).
    if session.participants.filter(user=user, kicked_at__isnull=False).exists():
        raise _live_refusal("You've been removed from this session by a moderator and can't rejoin it.", "session_kicked")

    # NOTE (fix): PassPurchase.sync_missed_charges()'s own docstring
    # already promised this call site ("called opportunistically
    # whenever a student actually shows up") but nothing ever actually
    # called it — the per-day escrow charge otherwise depends entirely
    # on the post_save signal on ClassSession, which is fine for the
    # normal case but leaves no safety net if that signal ever misses
    # a session (a stuck task, a status flip via some other path).
    # Best-effort and outside the seat-locking transaction below: a
    # failure here must never block the student from actually joining
    # the class they're trying to enter right now.
    purchase = PassPurchase.objects.filter(
        student=user,
        class_pass__classroom=session.classroom,
        status=PassPurchase.Status.SUCCESS,
        is_active=True,
    ).first()
    if purchase is not None:
        try:
            purchase.sync_missed_charges()
        except Exception:
            logging.getLogger(__name__).exception(
                "sync_missed_charges failed for purchase %s on join of session %s",
                purchase.id, session.id,
            )

    with transaction.atomic():
        session = (
            ClassSession.objects.select_for_update().select_related("classroom").get(pk=session.pk)
        )

        # NOTE: livekit_role/participant_role already resolved above,
        # before the is_joinable() check — no need to resolve again here.

        # NOTE (fix): this used to unconditionally .create() a new
        # SessionParticipant row on every call, then check capacity
        # first. A double-tap / retried request (or rejoining after a
        # dropped connection without ever calling leave()) created a
        # second "still in room" (left_at IS NULL) row for the same
        # user, which (a) double-counted them against max_participants,
        # shrinking real capacity over time, (b) could waitlist someone
        # who already has a seat, and (c) left an orphan row that never
        # gets closed out, so attendance/waitlist-promotion logic in
        # signals.py never fires for it. Reuse the user's existing open
        # row for this session if one exists, and skip the capacity
        # check entirely in that case — they already hold a seat.
        participant = session.participants.filter(user=user, left_at__isnull=True).first()
        newly_created = participant is None
        if newly_created:
            current_count = session.participants.filter(left_at__isnull=True).count()
            if current_count >= session.classroom.max_participants:
                SessionWaitlist.objects.get_or_create(session=session, student=user)
                return Response(
                    {"detail": "Session is full — you've been added to the waitlist."}, status=202
                )
            participant = SessionParticipant.objects.create(
                session=session, user=user, role=participant_role
            )
            # X12: a rejoin keeps an earlier recording-notice acknowledgement; a minor entering refreshes the room's
            # "minor present" answer so the contact-info chat rule covers the room from the next line.
            from ..live_safety import note_participant_joined
            from ..recording_consent import carry_ack_to_new_row

            carry_ack_to_new_row(session, user, participant)
            note_participant_joined(session, user)

            # NOTE (fix — max_classes cap was silently dead): a
            # "N-class pack" pass's classes_attended never got
            # incremented anywhere in the codebase, so
            # Classroom.has_access()'s max_classes exclusion (see that
            # method's own NOTE (fix)) had nothing to actually compare
            # against and the cap never tripped. Count exactly one
            # class per SESSION per student — gated on newly_created,
            # not on every /join/ call, so a reconnect/retry against a
            # session the student is already seated in never
            # double-counts. Row-locked and done with select_for_update
            # so two students' concurrent joins against a
            # nearly-exhausted pack can't both slip in past the cap
            # (has_access() re-reads this same row on their NEXT join
            # attempt, so the cap is enforced going forward, not
            # retroactively on the join already in progress here).
            attendance_counted = False
            if purchase is not None:
                PassPurchase.objects.select_for_update().filter(pk=purchase.pk).update(
                    classes_attended=F("classes_attended") + 1
                )
                attendance_counted = True

        went_live = False
        if session.status == ClassSession.Status.SCHEDULED:
            session.status = ClassSession.Status.LIVE
            session.actual_start = session.actual_start or timezone.now()
            session.live_heartbeat_at = timezone.now()  # LV1: starts the presence clock
            live_fields = ["status", "actual_start", "live_heartbeat_at"]
            # X12 L-D #1: platform default "listener mode". Off unless LIVE_STAGE_ONLY_DEFAULT=1; the host can still turn
            # it off per class. Set BEFORE any student token is minted, so no student ever gets a publish grant first.
            if getattr(settings, "LIVE_STAGE_ONLY_DEFAULT", False) and not session.stage_only:
                session.stage_only = True
                live_fields.append("stage_only")
            session.save(update_fields=live_fields)
            went_live = True

    if not went_live and participant_role == SessionParticipant.Role.HOST:
        # LV1: teacher/co-teacher (re)joining a session that is already LIVE
        # (app restart, network drop) -> presence is fresh again, so the
        # story-tray ring comes back without waiting for the next heartbeat.
        touch_heartbeat(session.id)

    if went_live:
        # NOTE (fix): a session going LIVE was previously only ever
        # surfaced to students who had set an explicit ClassReminder
        # (send_due_reminders in tasks.py) — anyone who didn't set one
        # had no way to know class had actually started short of
        # opening the app and checking. Queued after the transaction
        # above has already committed the status change, so a
        # slow/failing push provider can't add latency here; excludes
        # whoever's own join() call just triggered this (the teacher,
        # normally) since they don't need a push about a class they're
        # already in.
        from ..tasks import notify_session_live

        _safe_delay(notify_session_live, session.id, exclude_user_id=user.id)

    room_name = str(session.room_id)
    try:
        ensure_room(room_name=room_name, max_participants=session.classroom.max_participants)
        livekit_token = generate_livekit_token(
            room_name=room_name,
            user_id=user.id,
            user_name=user.get_full_name() or user.username,
            role=livekit_role,
            can_publish=_stage_can_publish(session, livekit_role, user),
        )
    except LiveKitError:
        # If the room service call fails, only roll back a newly-created
        # participant record (so the join can be retried) — if the user
        # was already in the room (reused row), don't mistakenly mark
        # them as "left".
        if newly_created:
            participant.delete()
            # NOTE (fix): the classes_attended bump above happens
            # before this LiveKit call, on the assumption the join
            # succeeds. If it doesn't, the student never actually got
            # into the room — undo the count along with the
            # participant row, or a flaky LiveKit call would silently
            # burn a class off someone's pack for a class they were
            # never let into.
            if attendance_counted:
                PassPurchase.objects.filter(pk=purchase.pk).update(
                    classes_attended=F("classes_attended") - 1
                )
        # NOTE (fix — was `return Response({"detail": str(exc)},
        # status=503)`): that hand-built Response bypassed
        # tuitionclass_exception_handler entirely (it only normalises RAISED
        # exceptions), so this path came back without a "code" field
        # unlike every other error in the app. `exc` is already a
        # LiveKitError — a DRF APIException carrying the right
        # user-safe detail and status_code (see livekit_utils.py) — so a
        # bare re-raise after the cleanup above is all that's needed; DRF
        # + tuitionclass_exception_handler take it from here.
        raise

    return Response(
        {
            "room_id": room_name,
            "participant_id": participant.id,
            "role": participant_role,
            "livekit_role": livekit_role,
            "livekit_url": LIVEKIT_URL,
            "livekit_token": livekit_token,
            "recording_notice": _recording_notice(session, user, participant),
        },
        status=200,
    )


def _perform_trial_join(classroom, user, raw_password, request):
    """Shared trial-join implementation — see
    ClassroomViewSet.trial_join()'s docstring. Deliberately NOT built on
    top of _perform_join(): a trial has its own gate (password, not a
    pass), its own one-shot per-student record (ClassroomTrialAccess), and
    a deliberately weaker LiveKit token (see ParticipantRole.TRIAL_STUDENT
    in livekit_utils.py) — trying to bolt those onto _perform_join's
    pass-centric flow would make that function harder to read for its
    actual (common) case.
    """
    if not classroom.is_trial_enabled or not classroom.trial_password:
        raise PermissionDenied("This class doesn't offer a trial preview.")
    if classroom.bans.filter(student_id=user.id).exists():
        raise _live_refusal("You've been banned from this classroom by the teacher.", "classroom_banned")
    if _has_full_room_access(classroom, user):
        raise ValidationError("You already have access to this classroom — no need for a trial.")

    # Per-(user, classroom) wrong-code lockout, independent of the
    # already-shared IP-scoped throttle on the view. Short TTL: this is
    # meant to slow down code-guessing, not lock a genuine student out for
    # long over a typo.
    lock_key = f"tuitionclass:trial_lock:{classroom.id}:{user.id}"
    if cache.get(lock_key):
        raise Throttled(detail="Too many wrong trial codes — try again in a minute.")

    existing = ClassroomTrialAccess.objects.filter(classroom=classroom, user=user).first()
    if existing is not None and not existing.is_active():
        raise PermissionDenied("You've already used your trial for this class.")

    if existing is None and not classroom.check_trial_password(raw_password or ""):
        fail_key = f"tuitionclass:trial_fail:{classroom.id}:{user.id}"
        fail_count = cache.get(fail_key, 0) + 1
        cache.set(fail_key, fail_count, timeout=120)
        if fail_count >= 5:
            cache.set(lock_key, True, timeout=60)
        raise PermissionDenied("That trial password isn't right.")

    session = (
        classroom.sessions.filter(status=ClassSession.Status.LIVE)
        .order_by("-actual_start", "-scheduled_start")
        .first()
    )
    if session is None:
        raise NotFound({"detail": "There's no tuition class to trial right now.", "no_session": True})
    if session.participants.filter(user=user, kicked_at__isnull=False).exists():
        raise _live_refusal("You've been removed from this session by a moderator and can't rejoin it.", "session_kicked")

    with transaction.atomic():
        session = (
            ClassSession.objects.select_for_update().select_related("classroom").get(pk=session.pk)
        )
        participant = session.participants.filter(user=user, left_at__isnull=True).first()
        newly_created = participant is None
        if newly_created:
            current_count = session.participants.filter(left_at__isnull=True).count()
            if current_count >= session.classroom.max_participants:
                raise ValidationError({"detail": "This session is full right now.", "code": "session_full"})
            participant = SessionParticipant.objects.create(
                session=session, user=user, role=SessionParticipant.Role.STUDENT, is_trial=True,
            )

        if existing is not None and existing.is_active():
            trial = existing
        else:
            now = timezone.now()
            trial = ClassroomTrialAccess.objects.create(
                classroom=classroom,
                user=user,
                session=session,
                expires_at=now + timezone.timedelta(minutes=classroom.trial_duration_minutes),
                duration_minutes=classroom.trial_duration_minutes,
            )
            # Kick the student out (server-side) the moment the trial ends,
            # independent of whether the client's own countdown UI is even
            # still open. Best-effort: worst case the client-side timer /
            # the next _has_room_access check on their next action still
            # catches it.
            from ..tasks import expire_trial_access

            _safe_apply_async(expire_trial_access, args=[trial.id], countdown=classroom.trial_duration_minutes * 60)

    room_name = str(session.room_id)
    try:
        ensure_room(room_name=room_name, max_participants=session.classroom.max_participants)
        livekit_token = generate_livekit_token(
            room_name=room_name,
            user_id=user.id,
            user_name=user.get_full_name() or user.username,
            role=ParticipantRole.TRIAL_STUDENT,
            ttl=timezone.timedelta(seconds=trial.remaining_seconds()),
        )
    except LiveKitError:
        if newly_created:
            participant.delete()
        raise

    return Response(
        {
            "room_id": room_name,
            "participant_id": participant.id,
            "role": SessionParticipant.Role.STUDENT,
            "livekit_role": ParticipantRole.TRIAL_STUDENT,
            "livekit_url": LIVEKIT_URL,
            "livekit_token": livekit_token,
            "session": ClassSessionSerializer(session, context={"request": request}).data,
            "is_trial_session": True,
            "trial": {
                "expires_at": trial.expires_at,
                "duration_minutes": trial.duration_minutes,
                "remaining_seconds": trial.remaining_seconds(),
                "server_time": timezone.now(),
            },
        },
        status=200,
    )


def _try_promote_from_waitlist(session):
    """Called immediately whenever a seat frees up on a session — a normal
    /leave/ (SessionParticipantViewSet.leave) or a host /kick/
    (ClassSessionViewSet.kick) — so the next waitlisted student gets pulled
    in automatically instead of the seat just sitting empty until a host
    happens to open the waitlist tab and hits /promote/ manually.

    NOTE (fix — real product gap): SessionWaitlistViewSet.promote() already
    existed as a host-triggered path, but nothing on the /leave/ or /kick/
    side ever called anything like it — a seat freeing up produced no
    signal to anyone. This is that missing trigger. promote() itself stays
    available unchanged for a host who wants to hand-pick a specific
    student out of order; this is just the "nobody has to babysit the
    waitlist" default that fires on every seat-freeing path automatically.

    Best-effort and swallows its own errors — a failure here must never
    break the /leave/ or /kick/ call that triggered it.
    """
    try:
        if session.status not in (ClassSession.Status.LIVE, ClassSession.Status.SCHEDULED):
            return

        student = None
        with transaction.atomic():
            current_count = session.participants.filter(left_at__isnull=True).count()
            if current_count >= session.classroom.max_participants:
                return  # still full — e.g. someone else already took the seat

            entry = (
                SessionWaitlist.objects.select_for_update()
                .select_related("student")
                .filter(session=session)
                .order_by("joined_at")
                .first()
            )
            if entry is None:
                return

            # Never resurrect someone THIS session explicitly kicked — same
            # guard as the manual promote() path.
            if session.participants.filter(user=entry.student, kicked_at__isnull=False).exists():
                entry.delete()
                return

            _, participant_role = _resolve_session_roles(session, entry.student)
            SessionParticipant.objects.create(
                session=session, user=entry.student, role=participant_role
            )
            student = entry.student
            entry.delete()
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to auto-promote waitlist for session %s", session.pk
        )
        return

    if student is None:
        return

    create_notification(
        recipient=student,
        notif_type=Notification.NotifType.WAITLIST_PROMOTED,
        title="You're in!",
        message=f"A seat opened up in '{session.classroom.title}' — you've been moved in from the waitlist.",
        classroom=session.classroom,
        session=session,
    )
    from ..tasks import notify_waitlist_promotion

    _safe_delay(notify_waitlist_promotion, student.id, session.id)


class LiveKitWebhookView(APIView):
    permission_classes = []
    authentication_classes = []
    # NOTE (fix — legitimate webhook deliveries could get 429'd): without
    # this, the view inherits REST_FRAMEWORK's DEFAULT_THROTTLE_CLASSES
    # (AnonRateThrottle, since authentication_classes=[] means every
    # request here is anonymous) — 20/min, bucketed by source IP. LiveKit's
    # webhook sender always hits this from the same IP, and every
    # concurrent recording that ends around the same time fires one
    # `egress_ended` POST each; a busy platform can easily burst past
    # 20/min. Throttling runs in dispatch() BEFORE post() even executes,
    # so a tripped limit would silently drop the signature check and the
    # recording_url update with it — no user-facing symptom, just a
    # recording that never gets its playable link. This endpoint is
    # already protected by verify_webhook_event()'s signature check
    # (see livekit_utils.py), which is the real access control here, not
    # the shared per-IP rate limit meant for browser/app clients.
    throttle_classes = []

    def post(self, request):
        # NOTE (fix — was `except LiveKitError as exc: return Response(...,
        # status=401)`): verify_webhook_event() raises LiveKitError with
        # status_code=401 for a bad/missing signature (see
        # livekit_utils.py) — now that LiveKitError is a DRF APIException,
        # letting it propagate gives the same 401 with the standard
        # {"detail", "code"} envelope instead of a hand-built Response
        # that skipped tuitionclass_exception_handler. Signature-failure
        # detail is still logged inside verify_webhook_event(); the
        # caller here (LiveKit's servers, not a browser) doesn't need
        # more than the plain 401 + detail it already gets.
        event = verify_webhook_event(request.body, request.headers.get("Authorization", ""))

        # C2 — message-app CALL rooms (`call_<hex>`) ke events (participant join/left,
        # room_finished, egress_ended). Best-effort + idempotent, kabhi raise nahi karta;
        # non-call rooms ko ignore karta hai, isliye neeche ka tuitionclass flow unchanged.
        try:
            from message.livekit_webhook import handle_message_event

            handle_message_event(event)
        except Exception:
            logging.getLogger(__name__).exception("message call webhook forward failed")

        # X12 (auto recording) — best-effort hooks: they never raise and never change the 200 this view answers with.
        #   room_started / participant_joined -> start the recording by itself for classrooms that opted in
        #   egress_started                    -> LiveKit's own start moment becomes 0:00 of the chapter / replay-chat timeline
        if event.event in ("room_started", "participant_joined"):
            room_name = getattr(getattr(event, "room", None), "name", "") or ""
            if room_name:
                from ..recording_auto import maybe_auto_start

                maybe_auto_start(room_name, event.event)
        elif event.event == "egress_started":
            from ..recording_auto import note_egress_started

            note_egress_started(event.egress_info)

        if event.event == "egress_ended":
            egress_info = event.egress_info
            # X12: matches on `egress_id` (still running) OR `last_egress_id` — a manual Stop clears egress_id at once, which
            # used to leave this webhook with no session to attach the file's link to.
            from ..recording_auto import egress_file_seconds, session_for_egress

            session = session_for_egress(egress_info.egress_id)
            if session is not None:
                ended_id = egress_info.egress_id
                # Capture this BEFORE clearing below, so we know whether
                # stop_recording() already told connected clients "REC" is
                # off, or whether this webhook is the first anyone hears of
                # it (the auto-stop case in the comment right below).
                was_stopped_explicitly = session.egress_id != ended_id
                # A NEWER recording (started after this one was stopped) owns recording_url / the duration now.
                owns_file = session.last_egress_id in ("", ended_id)
                file_results = list(egress_info.file_results)
                url = ""
                if file_results:
                    # `location` is the final playable URL/path for
                    # cloud-storage outputs; some SDK versions instead only
                    # populate `filename` for local/file-only outputs — try
                    # both rather than assuming one.
                    url = getattr(file_results[0], "location", "") or getattr(file_results[0], "filename", "")
                    if url and owns_file:
                        session.recording_url = url
                    seconds = egress_file_seconds(file_results[0])
                    if seconds and owns_file:
                        session.recording_duration_seconds = seconds
                # Egress job is over one way or another (success or
                # failure) — always clear egress_id so the UI's "REC"
                # indicator can't get stuck on if stop_recording() was
                # never explicitly called (e.g. the room's empty_timeout
                # closed the room and LiveKit auto-stopped the egress).
                # (Only when it is THIS egress: a newer one may be running.)
                if session.egress_id == ended_id:
                    session.egress_id = ""
                session.save(update_fields=["recording_url", "egress_id", "recording_duration_seconds"])
                # NOTE (fix — realtime gap): connected clients previously
                # never learned the recording had actually finished
                # processing and was playable — only the next REST poll of
                # this session/its /recordings/ list would reveal it. Also
                # covers the auto-stop path (empty_timeout) where
                # stop_recording() was never called, so no
                # "recording.stopped" was ever sent for it until now.
                if not was_stopped_explicitly:
                    broadcast_to_session(session.id, "recording.stopped", {"egress_id": egress_info.egress_id})
                if url and owns_file:
                    broadcast_to_session(session.id, "recording.ready", {"recording_url": url})
                    # X12: bell + N9-aware push "your class recording is ready" (once per egress, see the task).
                    from ..tasks import notify_recording_ready

                    _safe_delay(notify_recording_ready, session.id, ended_id)
                    # NOTE (fix — captions wiring): transcribe_recording
                    # (tasks.py) was fully implemented but never actually
                    # queued from anywhere. Gated behind a per-classroom
                    # opt-in toggle (mirrors recording_enabled's own
                    # shape) so transcription cost is only ever incurred
                    # for classrooms that asked for captions — NOT fired
                    # unconditionally on every recording.
                    if session.classroom.captions_enabled:
                        from ..tasks import transcribe_recording

                        _safe_delay(transcribe_recording, session.id)
            else:
                # Not a tuition-class recording: it may be a CAMPUS class recording
                # (campus.CampusLiveSession). Campus is reached only through the core
                # bridge; the handler never raises and ignores ids it does not know.
                from core.classroom_chat_bridge import handle_campus_egress_ended

                handle_campus_egress_ended(egress_info)

        # 200 with no body is all LiveKit's webhook sender expects — it
        # just needs to know delivery succeeded so it doesn't retry.
        return Response(status=200)


# ---------------------------------------------------------------------------
# 6. SESSION PARTICIPANT
# ---------------------------------------------------------------------------
class SessionParticipantViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """NOTE (fix): ?session=<id> used to return EVERY participant row for
    that session (i.e. the full attendance list, including who joined and
    when) to ANY authenticated user — not just the classroom's teacher/
    staff. Now gated behind _can_view_classroom_internals, same as
    ClassSessionViewSet; anyone else, filtered or not, only ever sees
    their own participation rows."""

    serializer_class = SessionParticipantSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination

    def get_queryset(self):
        qs = SessionParticipant.objects.select_related("user")
        session_id = self.request.query_params.get("session")
        user = self.request.user
        if session_id:
            session = ClassSession.objects.filter(pk=session_id).select_related("classroom").first()
            if session and _can_view_classroom_internals(session.classroom, user):
                return qs.filter(session_id=session_id)
            return qs.filter(session_id=session_id, user=user)
        return qs.filter(user=user)

    @action(detail=True, methods=["post"])
    def leave(self, request, pk=None):
        participant = self.get_object()
        if participant.user_id != request.user.id:
            raise PermissionDenied("You can only end your own participation.")
        participant.left_at = timezone.now()
        participant.save(update_fields=["left_at"])
        # NOTE (fix): see _try_promote_from_waitlist's docstring (views.py,
        # right after _perform_join) — a seat freeing up here previously had
        # no path to the waitlist at all; a student had to wait for the
        # host to manually notice and hit /promote/.
        _try_promote_from_waitlist(participant.session)
        return Response({"left_at": participant.left_at})


# ---------------------------------------------------------------------------
# 15. WAITLIST
# ---------------------------------------------------------------------------
class SessionWaitlistViewSet(
    mixins.ListModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """Own waitlist entries only by default (list/leave). Pass ?session=<id>
    as that session's teacher/co-teacher/moderator to see every entry
    waiting on it instead, oldest first — the listing `promote` needs an
    id from, since it has no other way to discover who's waiting. `promote`
    itself stays a separate, host-only path that bypasses the "own
    entries" queryset on purpose — a teacher/co-teacher/moderator needs to
    promote a STUDENT's entry, not their own."""

    serializer_class = SessionWaitlistSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination

    def get_queryset(self):
        base = SessionWaitlist.objects.select_related("student", "session__classroom")

        # NOTE (fix): there was no way for a teacher/co-teacher/moderator to
        # list who's waiting on their own session — get_queryset only ever
        # returned the CALLER's own entries, so the host had no id to pass
        # into promote/{id}/ short of guessing. ?session=<id> switches to
        # every entry on that session (still oldest-first, via the model's
        # Meta.ordering = ["joined_at"] — matches who should logically be
        # promoted next) once _can_moderate_session confirms the caller
        # actually hosts it. No ?session= (or failing that check) falls
        # straight through to the original "own entries" behaviour, so
        # nothing that already relies on the current shape breaks.
        session_id = self.request.query_params.get("session")
        if session_id:
            session = ClassSession.objects.filter(pk=session_id).select_related("classroom").first()
            if session and _can_moderate_session(session, self.request.user):
                return base.filter(session_id=session_id)

        return base.filter(student=self.request.user)

    @action(detail=True, methods=["post"])
    def promote(self, request, pk=None):
        """Host action — pulls this waitlisted student into the live room
        (e.g. after someone else leaves and a seat frees up). Looked up
        directly (not via get_queryset/get_object) since the caller is the
        host, not the student who owns the waitlist entry."""
        entry = SessionWaitlist.objects.filter(pk=pk).select_related("session__classroom").first()
        if entry is None:
            raise NotFound("Waitlist entry not found.")

        session = entry.session
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can promote from the waitlist."
            )

        current_count = session.participants.filter(left_at__isnull=True).count()
        if current_count >= session.classroom.max_participants:
            raise ValidationError("Session is still full — no seat to promote into.")

        # NOTE (fix): join()/token() both refuse re-entry to anyone with a
        # kicked_at stamp on THIS session (see SessionParticipant.kicked_at),
        # but promote() — a separate host-only path that bypasses join()
        # entirely — never checked that. A host removing a disruptive
        # student, then promoting them straight back in from the waitlist
        # (or another host doing so without realizing), silently undid the
        # kick with no error and no way for the original moderator to know.
        if session.participants.filter(user=entry.student, kicked_at__isnull=False).exists():
            # NOTE (fix — was a hand-built Response, bypassing
            # tuitionclass_exception_handler and coming back without a
            # "code" field): the entry.delete() side effect still needs
            # to happen before we fail the request, so raise after it
            # instead of before.
            entry.delete()
            raise PermissionDenied("This student was removed from this session and can't be promoted back in.")

        livekit_role, participant_role = _resolve_session_roles(session, entry.student)
        participant = SessionParticipant.objects.create(
            session=session, user=entry.student, role=participant_role
        )

        create_notification(
            recipient=participant.user,
            notif_type=Notification.NotifType.WAITLIST_PROMOTED,
            title="You're in!",
            message=f"A seat opened up in '{session.classroom.title}' — you've been moved in from the waitlist.",
            classroom=session.classroom,
            session=session,
        )
        # NOTE (fix — CRITICAL, production bug): notify_waitlist_promotion
        # already existed as a Celery task, but nothing anywhere ever
        # called it — a promoted student got the silent in-app bell row
        # above and NOTHING else. If their app was closed/backgrounded they
        # had no idea a seat had opened until they happened to check back,
        # defeating the entire point of a waitlist promotion (the seat can
        # fill back up while they're not looking). Passes plain ids instead
        # of the waitlist entry's own pk — see notify_waitlist_promotion's
        # docstring for why (entry.delete() below would otherwise race it).
        from ..tasks import notify_waitlist_promotion

        _safe_delay(notify_waitlist_promotion, entry.student_id, session.id)
        entry.delete()
        broadcast_to_session(
            session.id, "waitlist.promoted",
            {"user_id": participant.user_id, "seats_remaining": session.classroom.max_participants - current_count - 1},
        )
        return Response(SessionParticipantSerializer(participant).data, status=status.HTTP_201_CREATED)
