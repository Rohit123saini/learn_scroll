# message/views_parent.py
"""
Feature 8 — Parent/Guardian read-only view ("Parent Mode").

Two audiences:

STUDENT SIDE (normal login, `IsAuthenticated`, student's own access_token):
    GET    /message/parent/codes/      -> list this student's own active codes
    POST   /message/parent/codes/      -> generate a new code {"label": "Mom"}
    DELETE /message/parent/codes/      -> revoke a code {"id": "<uuid>"}

    GET    /message/parent/codes/<code_id>/tokens/
           -> list of individual devices/tokens verified against that one
              code (id, created_at, last_seen_at) — 🔧 NEW, see below.
    DELETE /message/parent/codes/<code_id>/tokens/<token_id>/
           -> revoke ONE device without touching the code or any other
              device tied to it — 🔧 NEW.

    🔧 GAP FIX — previously the ONLY way to cut off a parent's access was
    `DELETE /parent/codes/` on the whole code, which killed every device
    that had ever verified that code (one code can produce many
    `ParentToken`s — e.g. Mom's phone AND Dad's phone both verifying the
    same "Parents" code). If just one device was lost/stolen, the
    student had no choice but to revoke the shared code and re-share a
    brand new one with everyone else on it too. The two endpoints above
    let a single `ParentToken` be revoked on its own, leaving the code
    and its other devices untouched.

PARENT SIDE (no student login — code/token only):
    POST   /message/parent/verify/     -> {"code": "..."} -> {parent_token, ...}
    GET    /message/parent/dashboard/  -> read-only summary
           (header: X-Parent-Token: <parent_token>)

STRICT SCOPE — `ParentDashboardView` must NEVER return message text,
media, contact info, or anything beyond: the student's display name,
per-classroom attendance stats, and assignment pending/submitted
counts. Before adding a field here, ask: "would this be fine on a
report-card-style summary a parent sees?" — if it's chat content
(even metadata like who they talked to), it does NOT belong here.

🔧 GAP FIX (Gap 2 — supersedes the Gap 1 shape below) — this dashboard
used to loop over the student's chat Groups as the PRIMARY source, which
made any liveclass Classroom invisible here whenever chat-group linking
hadn't happened for it (`chat_group_enabled=False` — either the teacher
opted out, or it's an older classroom from before linking existed). A
fully active classroom — homework, marks, report cards, attendance — was
silently missing from a parent's view for no reason a parent could see.

Fixed by making the student's `liveclass` Classrooms the PRIMARY loop
(via `is_enrolled()`-equivalent — active OR lapsed pass, same breadth
`ClassroomParentCodeGenerateView`/`ReportCardViewSet` already use), with
the chat-group's own `assignments` data attached as an OPTIONAL nested
`chat_group` block, present only when `core.classroom_chat_bridge.
get_groups_for_classrooms()` finds a real linked Group for that
classroom. No classroom ever disappears
from the dashboard just because it has no chat group; it just has
`chat_group: null` instead.

This keeps the Gap 1 rule intact — liveclass homework and message-app
assignments are still never summed into one number — just expressed via
nesting (`homework` at the classroom's top level, `assignments` only
inside its `chat_group`) instead of two parallel top-level lists.

🔧 GAP FIX (Gap 3) — ALL attendance shown by this view now comes
strictly from `liveclass`'s own session-attendance record (`ClassSession`
+ `SessionParticipant`, via `liveclass.models.
compute_attendance_percent_bulk`). The message app's own
`StudyRoomAttendance` self-check-in streak widget (`attendance_utils.
compute_attendance_stats_bulk`) is a separate, unrelated feature — this
app has no teacher/student/classroom concept of its own, only chat
groups/group-study — and per product decision it is no longer used
anywhere in this view. `chat_group` below therefore only ever carries
`group_name` + `assignments`, never an attendance field.
"""
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Assignment,
    AssignmentSubmission,
    Group,
    GroupMember,
    ParentAccessCode,
    ParentToken,
)
# 🔧 NOTE (this session) — `Assignment.group` and `AssignmentSubmission.
# student` had their `related_name` renamed in `models.py`
# (`assignments` -> `message_assignments`, `assignment_submissions` ->
# `message_assignment_submissions`) to fix a reverse-accessor clash with
# a separate `liveclass` app's own `Assignment`/`AssignmentSubmission`
# models pointing at the same `Group`/`User`. Confirmed NO code below
# needs to change: every query here (`assignment_totals`,
# `submitted_counts`) filters *forward* through the FK
# (`group_id__in=`, `assignment__group_id__in=`, `student=`) rather than
# via the reverse accessor (`some_group.message_assignments.all()` /
# `some_user.message_assignment_submissions.all()`), and forward lookups
# are unaffected by a `related_name` change.
from .permissions import HasValidParentToken
from .throttles import ParentCodeRevealThrottle, ParentCodeVerifyThrottle

# 🔧 GAP FIX (Gap 1 — liveclass Assignment vs message Assignment collision):
# `liveclass` has its own `Classroom` / `Assignment` / `AssignmentSubmission`
# / `StudentReportCard` models — a completely different domain object from
# the `Group` / `Assignment` / `AssignmentSubmission` imported above (this
# app's own). They share class names because they model similar concepts,
# but they are NOT the same rows and must never be summed or merged
# together into one number on the parent dashboard. Imported here under a
# `Liveclass*` alias so every reference below stays unambiguous about which
# app's assignment it means. This is the mirror image of the cross-app
# import `liveclass/parent_link_views.py` already does in the other
# direction (`from message.models import ParentAccessCode, ...`); neither
# app's `models.py` imports the other, so this does not create an import
# cycle.
from liveclass.models import (
    Assignment as LiveclassAssignment,
    AssignmentSubmission as LiveclassAssignmentSubmission,
    Classroom as LiveclassClassroom,
    PassPurchase as LiveclassPassPurchase,
    StudentReportCard as LiveclassStudentReportCard,
    compute_attendance_percent_bulk,
)

# 🔧 GAP FIX (Gap 2) — the ONE place that knows how a liveclass Classroom
# maps to a chat Group (see that module's own docstring, design principle
# 1). Deliberately reused rather than re-deriving `chat_group_enabled` +
# `linked_conversation_id` here, so this view can never drift out of sync
# with how `liveclass/signals.py` itself determines "does this classroom
# have a group".
from core.classroom_chat_bridge import get_groups_for_classrooms


def _display_name(user):
    full_name = getattr(user, 'get_full_name', lambda: '')() if hasattr(user, 'get_full_name') else ''
    return full_name or getattr(user, 'username', None) or str(user.id)


# ======================================================================
# STUDENT SIDE — manage own parent-codes
# ======================================================================
class ParentAccessCodeView(APIView):
    """Student's own "Manage parent access" screen."""
    permission_classes = [IsAuthenticated]

    # Ek student ke max itne active codes — clutter/abuse dono rokta hai.
    # Genuinely 2 guardians + ek grandparent jaisa case bhi isme aa jaata hai.
    MAX_ACTIVE_CODES = 5

    def get(self, request):
        # 🔧 GAP FIX (reveal-once exposure) — pehle yahan raw `code`
        # return hota tha har baar. Ab sirf `masked_code` ("••••9QRT")
        # — poora plaintext sirf generation ke turant baad (neeche
        # `post()`) ya explicit `ParentAccessCodeRevealView` se milta
        # hai, kabhi is list se nahi.
        codes = ParentAccessCode.objects.filter(
            student=request.user, is_active=True,
        ).annotate(active_devices=Count('tokens'))
        return Response([
            {
                'id': str(c.id),
                'label': c.label,
                'masked_code': c.masked_code,
                'last_used_at': c.last_used_at,
                'created_at': c.created_at,
                'active_devices': c.active_devices,
                'expires_at': c.expires_at,
                'is_expired': c.is_expired,
                # 🔧 NEW — student ko khud pata rahe last baar kab poora
                # code dobara reveal kiya tha (audit trail unki apni
                # nazar me bhi).
                'last_revealed_at': c.last_revealed_at,
            }
            for c in codes
        ])

    def post(self, request):
        label = (request.data.get('label') or '').strip()[:50]
        active_count = ParentAccessCode.objects.filter(
            student=request.user, is_active=True,
        ).count()
        if active_count >= self.MAX_ACTIVE_CODES:
            return Response(
                {'detail': f"Max {self.MAX_ACTIVE_CODES} active parent codes allowed. "
                           f"Pehle koi purana revoke karo."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        code_obj = ParentAccessCode.generate_for(request.user, label=label)
        # 🔧 NOTE — ye ek hi jagah hai jahan POST response me raw `code`
        # jaata hai bina throttle/reveal-endpoint ke — jaan-boojh kar,
        # kyunki generation khud ek deliberate, one-time student action
        # hai (list ki tarah repeatedly hit hone wala GET nahi).
        code_obj.last_revealed_at = timezone.now()
        code_obj.save(update_fields=['last_revealed_at', 'updated_at'])
        return Response(
            {
                'id': str(code_obj.id),
                'label': code_obj.label,
                'code': code_obj.code,
                'expires_at': code_obj.expires_at,
            },
            status=status.HTTP_201_CREATED,
        )

    def delete(self, request):
        code_id = request.data.get('id')
        if not code_id:
            return Response({'detail': "'id' required hai"}, status=status.HTTP_400_BAD_REQUEST)

        updated = ParentAccessCode.objects.filter(
            id=code_id, student=request.user,
        ).update(is_active=False)
        if not updated:
            return Response({'detail': 'Code not found'}, status=status.HTTP_404_NOT_FOUND)

        # `HasValidParentToken` already filters on `is_active=True`, so this
        # alone would be enough — deleting the tokens too is just cleanup,
        # not load-bearing for the actual revoke.
        ParentToken.objects.filter(parent_access_code_id=code_id).delete()
        return Response({'detail': 'Revoked'})


# ======================================================================
# 🔧 NEW — STUDENT SIDE — renew an expired/expiring code
# ======================================================================
class ParentAccessCodeRenewView(APIView):
    """
    POST /message/parent/codes/<code_id>/renew/
    -> gives the SAME code a fresh TTL (default `ParentAccessCode.
       DEFAULT_TTL_DAYS`) and re-activates it if it had auto-expired.
       Every `ParentToken` already verified against this code (every
       device) becomes valid again immediately — no re-verification,
       no new code to re-share with every parent/guardian on it.

    This is deliberately separate from `POST /parent/codes/` (new code):
    that endpoint issues a BRAND NEW code+devices-must-reverify; this one
    extends an EXISTING code's life in place.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, code_id):
        access_code = ParentAccessCode.objects.filter(
            id=code_id, student=request.user,
        ).first()
        if not access_code:
            return Response({'detail': 'Code not found'}, status=status.HTTP_404_NOT_FOUND)

        access_code.renew()
        return Response({
            'id': str(access_code.id),
            'expires_at': access_code.expires_at,
            'is_expired': access_code.is_expired,
        })


# ======================================================================
# 🔧 NEW — STUDENT SIDE — reveal-once: explicit action to see the full
# plaintext code again (list/GET never returns it, see §GET above)
# ======================================================================
class ParentAccessCodeRevealView(APIView):
    """
    POST /message/parent/codes/<code_id>/reveal/
    -> {"code": "7F3K9QRT"}

    Deliberately its own throttled endpoint rather than a GET query-param
    on the list — every reveal is a distinct, rate-limited, audited
    action (`last_revealed_at`), not something that happens silently as
    a side-effect of just opening the screen.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ParentCodeRevealThrottle]

    def post(self, request, code_id):
        access_code = ParentAccessCode.objects.filter(
            id=code_id, student=request.user, is_active=True,
        ).first()
        if not access_code:
            return Response({'detail': 'Code not found'}, status=status.HTTP_404_NOT_FOUND)

        access_code.last_revealed_at = timezone.now()
        access_code.save(update_fields=['last_revealed_at', 'updated_at'])
        return Response({'code': access_code.code, 'last_revealed_at': access_code.last_revealed_at})


# ======================================================================
# 🔧 NEW — STUDENT SIDE — per-device management within ONE code
# ======================================================================
class ParentCodeTokensView(APIView):
    """
    GET /message/parent/codes/<code_id>/tokens/
    -> list of every device (`ParentToken`) currently verified against
       this one code, so the student can tell "3 devices on the 'Mom'
       code" apart and decide which one to cut, instead of only being
       able to nuke the whole code.

    [{"id": "...", "created_at": "...", "last_seen_at": "..." | null}, ...]
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, code_id):
        access_code = ParentAccessCode.objects.filter(
            id=code_id, student=request.user, is_active=True,
        ).first()
        if not access_code:
            return Response({'detail': 'Code not found'}, status=status.HTTP_404_NOT_FOUND)

        tokens = ParentToken.objects.filter(
            parent_access_code=access_code,
        ).order_by('-created_at')

        return Response([
            {
                'id': str(t.id),
                'created_at': t.created_at,
                'last_seen_at': t.last_seen_at,
            }
            for t in tokens
        ])


class ParentCodeTokenDetailView(APIView):
    """
    DELETE /message/parent/codes/<code_id>/tokens/<token_id>/
    -> revoke exactly ONE device. The code itself and every other
       device tied to it stay untouched — the whole point of this
       endpoint existing separately from `ParentAccessCodeView.delete`.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, code_id, token_id):
        # Ownership goes through `parent_access_code__student`, not a
        # bare token-id lookup — a student can never revoke a token
        # hanging off a code that isn't theirs, even by guessing/brute-
        # forcing a UUID.
        deleted, _ = ParentToken.objects.filter(
            id=token_id,
            parent_access_code_id=code_id,
            parent_access_code__student=request.user,
        ).delete()
        if not deleted:
            return Response({'detail': 'Device not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response({'detail': 'Device revoked'})


# ======================================================================
# PARENT SIDE — no student login
# ======================================================================
class ParentVerifyCodeView(APIView):
    """
    POST /message/parent/verify/  {"code": "7F3K9QRT"}
    -> {"parent_token": "...", "student_name": "...", "label": "Mom"}
    """
    permission_classes = [AllowAny]
    throttle_classes = [ParentCodeVerifyThrottle]

    def post(self, request):
        code = (request.data.get('code') or '').strip().upper()
        if not code:
            return Response({'detail': "'code' required hai"}, status=status.HTTP_400_BAD_REQUEST)

        access_code = ParentAccessCode.objects.filter(
            code=code, is_active=True,
        ).select_related('student').first()
        if not access_code:
            return Response({'detail': 'Invalid ya expired code.'}, status=status.HTTP_404_NOT_FOUND)

        # 🔧 GAP FIX — code exists aur `is_active=True` hai, lekin TTL
        # cross ho chuki ho sakti hai (student ne kabhi explicitly
        # revoke nahi kiya, bas time nikal gaya). 410 alag se isliye
        # taaki parent-side app "galat code type kiya" (404) aur
        # "code expire ho gaya, student se naya/renewed maango" (410)
        # ko alag message dikha sake.
        if access_code.is_expired:
            return Response(
                {'detail': 'Ye code expire ho chuka hai. Student se naya ya renewed code maango.'},
                status=status.HTTP_410_GONE,
            )

        access_code.last_used_at = timezone.now()
        access_code.save(update_fields=['last_used_at', 'updated_at'])

        token = ParentToken.objects.create(
            parent_access_code=access_code,
            token=ParentToken.generate_token(),
        )

        return Response({
            'parent_token': token.token,
            'student_name': _display_name(access_code.student),
            'label': access_code.label,
        }, status=status.HTTP_200_OK)


class ParentDashboardView(APIView):
    """
    GET /message/parent/dashboard/   (header: X-Parent-Token: <token>)

    {
      "student_name": "...",
      "classrooms": [
        {
          "classroom_id": 41,
          "classroom_title": "Physics Batch A",
          "attendance_percent": 92.5,
          "homework": {"pending": 1, "submitted": 6, "total": 7},
          "latest_report_card": {
            "period_label": "Term 1",
            "attendance_percent": 92.5,
            "homework_completion_percent": 85.71,
            "average_marks": "78.50",
            "teacher_remark": "..."
          },
          "chat_group": {
            "group_name": "Physics Batch A",
            "assignments": {"pending": 2, "submitted": 5, "total": 7}
          }
        }
      ]
    }

    🔧 GAP FIX (Gap 2) — `liveclass` Classroom is now the PRIMARY,
    always-present source for every entry in `classrooms` (via
    `is_enrolled()`-equivalent: active OR lapsed pass — a lapsed pass
    should still show classwork history, same reasoning `Classroom.
    is_enrolled()` itself documents). `chat_group` is OPTIONAL and only
    appears when `core.classroom_chat_bridge.get_groups_for_classrooms()`
    finds a real linked Group AND the student is still an unbanned
    member of it — otherwise the key is simply `null`. A classroom the
    teacher never turned chat-group linking on for (or an older one from
    before that existed) still shows up here in full, just with
    `chat_group: null`.

    🔧 GAP FIX (Gap 1, still enforced) — `homework` (liveclass) and
    `chat_group.assignments` (message-app) are two independently-sourced
    datasets from two different apps and are NEVER summed into one
    number — see the imports above for why. Keep any future per-app
    addition (quizzes, tests, etc.) inside its own app's part of the
    entry the same way.

    🔧 GAP FIX (Gap 3) — the ONLY `attendance_percent` anywhere in this
    payload (top-level and inside `latest_report_card`) is liveclass's
    own session-attendance number (`ClassSession`/`SessionParticipant`
    via `liveclass.models.compute_attendance_percent_bulk`). `chat_group`
    deliberately carries no attendance field at all — the message app is
    chat/group-study only and has no classroom-attendance concept of its
    own; its unrelated `StudyRoomAttendance` self-check-in streak widget
    is never used here (see module docstring).
    """
    permission_classes = [HasValidParentToken]

    def get(self, request):
        student = request.parent_student
        return Response({
            'student_name': _display_name(student),
            'classrooms': self._classrooms(student),
        })

    @classmethod
    def _classrooms(cls, student):
        # ---- PRIMARY SOURCE (Gap 2): liveclass Classroom, not chat Group ----
        classroom_ids = list(
            LiveclassClassroom.objects.filter(
                passes__purchases__student=student,
                passes__purchases__status=LiveclassPassPurchase.Status.SUCCESS,
                passes__purchases__is_active=True,
            )
            .distinct()
            .values_list('id', flat=True)
        )
        if not classroom_ids:
            return []

        # 🔧 GAP FIX (Gap 2) — also pull the two chat-link fields here,
        # `.only(...)`, so `get_groups_for_classrooms()` below can decide
        # per-classroom eligibility without a second query per classroom.
        classrooms_by_id = {
            c.id: c
            for c in LiveclassClassroom.objects.filter(id__in=classroom_ids)
            .only('id', 'title', 'chat_group_enabled', 'linked_conversation_id')
        }

        # ---- liveclass-side data: homework + attendance % + report card ----
        # (unchanged from Gap 1 — see class docstring: this never merges
        # with the message-app assignment counts below.)
        homework_totals = dict(
            LiveclassAssignment.objects.filter(classroom_id__in=classroom_ids)
            .values('classroom_id')
            .annotate(total=Count('id'))
            .values_list('classroom_id', 'total')
        )
        homework_submitted = dict(
            LiveclassAssignmentSubmission.objects.filter(
                assignment__classroom_id__in=classroom_ids,
                student=student,
            )
            .values('assignment__classroom_id')
            .annotate(submitted=Count('id'))
            .values_list('assignment__classroom_id', 'submitted')
        )
        attendance_percent_by_classroom = compute_attendance_percent_bulk(classroom_ids, student)

        latest_report_card_by_classroom = {}
        for report_card in LiveclassStudentReportCard.objects.filter(
            classroom_id__in=classroom_ids, student=student,
        ).order_by('classroom_id', '-id'):
            latest_report_card_by_classroom.setdefault(report_card.classroom_id, report_card)

        # ---- optional chat-group side (Gap 2) ----
        chat_group_by_classroom = cls._chat_group_by_classroom(
            classrooms_by_id.values(), student,
        )

        results = []
        for classroom_id in classroom_ids:
            classroom = classrooms_by_id.get(classroom_id)
            if not classroom:
                continue
            total = homework_totals.get(classroom_id, 0)
            submitted = homework_submitted.get(classroom_id, 0)
            report_card = latest_report_card_by_classroom.get(classroom_id)

            results.append({
                'classroom_id': classroom_id,
                'classroom_title': classroom.title,
                'attendance_percent': attendance_percent_by_classroom.get(classroom_id, 0),
                # Deliberately named 'homework', not 'assignments' — see
                # class docstring (Gap 1).
                'homework': {
                    'pending': max(total - submitted, 0),
                    'submitted': submitted,
                    'total': total,
                },
                'latest_report_card': {
                    'period_label': report_card.period_label,
                    'attendance_percent': report_card.attendance_percent,
                    'homework_completion_percent': report_card.homework_completion_percent,
                    'average_marks': report_card.average_marks,
                    'teacher_remark': report_card.teacher_remark,
                } if report_card else None,
                # 🔧 GAP FIX (Gap 2) — None whenever there's no linked
                # group (or the student isn't/no-longer an unbanned member
                # of it) — the classroom itself is still fully listed above.
                'chat_group': chat_group_by_classroom.get(classroom_id),
            })

        return results

    @staticmethod
    def _chat_group_by_classroom(classrooms, student):
        """
        🔧 GAP FIX (Gap 2/3) — builds the optional `chat_group` sub-block
        for whichever of `classrooms` actually have a linked Group. Only
        ever `{group_name, assignments}` — no attendance field (Gap 3:
        the message app has no classroom-attendance concept; it's
        chat/group-study only, see module docstring). Every query here
        is bulk (fixed count, not one per classroom):
          1. `get_groups_for_classrooms()` — 2 queries total.
          2. GroupMember (unbanned-membership check) — 1 query.
          3. message-app Assignment totals/submissions — 2 queries.
        Returns {classroom_id: {...}} — a classroom with no eligible
        linked group is simply absent (caller does `.get(classroom_id)`).
        """
        group_by_classroom_id = get_groups_for_classrooms(classrooms)
        if not group_by_classroom_id:
            return {}

        # A linked Group existing isn't enough on its own — the student
        # must still be an unbanned member of it (same gate the OLD
        # Group-centric loop applied via `GroupMember.filter(is_banned=
        # False)`, preserved here so a chat-removal still hides chat data
        # even though the classroom itself stays visible per Gap 2).
        group_ids = [group.id for group in group_by_classroom_id.values()]
        member_group_ids = set(
            GroupMember.objects.filter(
                group_id__in=group_ids, user=student, is_banned=False,
            ).values_list('group_id', flat=True)
        )

        eligible = {
            classroom_id: group
            for classroom_id, group in group_by_classroom_id.items()
            if group.id in member_group_ids
        }
        if not eligible:
            return {}

        eligible_group_ids = [group.id for group in eligible.values()]

        # 🔧 FIX (N+1, preserved from the pre-Gap-2 loop) — bulk, not one
        # query per classroom.
        assignment_totals = dict(
            Assignment.objects.filter(group_id__in=eligible_group_ids)
            .values('group_id')
            .annotate(total=Count('id'))
            .values_list('group_id', 'total')
        )
        submitted_counts = dict(
            AssignmentSubmission.objects.filter(
                assignment__group_id__in=eligible_group_ids,
                student=student,
                is_submitted=True,
            )
            .values('assignment__group_id')
            .annotate(submitted=Count('id'))
            .values_list('assignment__group_id', 'submitted')
        )

        return {
            classroom_id: {
                'group_name': group.name,
                'assignments': {
                    'pending': max(
                        assignment_totals.get(group.id, 0) - submitted_counts.get(group.id, 0), 0,
                    ),
                    'submitted': submitted_counts.get(group.id, 0),
                    'total': assignment_totals.get(group.id, 0),
                },
            }
            for classroom_id, group in eligible.items()
        }