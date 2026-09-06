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
"""
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# 🔧 GAP FIX (N+1) — batched variant, see attendance_utils.py note.
from .attendance_utils import compute_attendance_stats_bulk
from .models import (
    Assignment,
    AssignmentSubmission,
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
          "group_name": "Physics Batch A",
          "attendance": {
            "current_streak": 7, "longest_streak": 12,
            "total_classes_attended": 34, "last_attended": "2026-09-04"
          },
          "assignments": {"pending": 2, "submitted": 5, "total": 7}
        }
      ]
    }
    """
    permission_classes = [HasValidParentToken]

    def get(self, request):
        student = request.parent_student

        memberships = list(
            GroupMember.objects.filter(
                user=student, is_banned=False,
            ).select_related('group', 'group__conversation')
        )
        group_ids = [m.group_id for m in memberships]
        conversation_ids = [m.group.conversation_id for m in memberships]

        # 🔧 FIX (N+1) — assignments: was 2-3 queries PER classroom,
        # batched into 2 queries total for however many classrooms the
        # student is in, looked up per-group in Python below.
        assignment_totals = dict(
            Assignment.objects.filter(group_id__in=group_ids)
            .values('group_id')
            .annotate(total=Count('id'))
            .values_list('group_id', 'total')
        )
        submitted_counts = dict(
            AssignmentSubmission.objects.filter(
                assignment__group_id__in=group_ids,
                student=student,
                is_submitted=True,
            )
            .values('assignment__group_id')
            .annotate(submitted=Count('id'))
            .values_list('assignment__group_id', 'submitted')
        )

        # 🔧 FIX (N+1) — attendance: was 1 query PER classroom via
        # `compute_attendance_stats(conversation, student)` inside the
        # loop. `compute_attendance_stats_bulk` fetches every classroom's
        # attendance rows for this student in ONE query, so this whole
        # dashboard now costs a fixed ~3 queries total regardless of how
        # many classrooms the student is in.
        attendance_by_conversation = compute_attendance_stats_bulk(conversation_ids, student)

        classrooms = [
            {
                'group_name': membership.group.name,
                'attendance': attendance_by_conversation[membership.group.conversation_id],
                'assignments': self._assignment_summary(
                    membership.group_id, assignment_totals, submitted_counts,
                ),
            }
            for membership in memberships
        ]

        return Response({
            'student_name': _display_name(student),
            'classrooms': classrooms,
        })

    @staticmethod
    def _assignment_summary(group_id, assignment_totals, submitted_counts):
        total = assignment_totals.get(group_id, 0)
        submitted = submitted_counts.get(group_id, 0)
        return {
            'pending': max(total - submitted, 0),
            'submitted': submitted,
            'total': total,
        }