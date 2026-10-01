# campus/campus_invite.py
"""
[ADDED — Task 13, growth list §D/G13] "Campus family network effect".

Problem this closes (growth_and_feature_tasks.md, G13): once a campus is
on the platform, nothing today gets the REST of that campus's own
students onto it — an admin has to manually `enrollStudent()` every
single one via `StudentEnrollmentViewSet` (campus/views.py), one row at
a time. A section-scoped, reusable join code turns that into "one
enrolled class brings its whole batch": generate one code for a
section, drop it in the class WhatsApp group, and every classmate who
pastes it into the app self-enrolls instantly — no admin action per
student.

Three pieces:
  1. CampusInviteCodeGenerateView -> admin/principal-HOD OR that
     section's own class-teacher (`can_manage_section_subject`, same
     authority `Notice`/attendance/results already use for
     section-level actions) mints (or reuses) a code for one section.
  2. CampusInviteCodeListView     -> admin/principal-HOD only — lists
     every code issued across the whole campus, for a simple "manage
     invites" screen (see which codes exist, how many joins each has
     brought in, revoke a leaked one).
  3. CampusInviteCodeRevokeView   -> admin/principal-HOD OR the
     class-teacher who generated it can deactivate it early.
  4. CampusInviteCodeRedeemView   -> what runs when a student pastes
     the code. No campus-admin authority needed here at all — this is
     deliberately the ONE self-service write path in this whole app
     that doesn't require staff to invoke it (see `StudentEnrollment`'s
     model docstring: every other enrollment row is admin-created).

Reuses `StudentEnrollment` directly — no shadow "pending join request"
model. A redeemed code just creates the exact same row
`StudentEnrollmentViewSet.create()` would, with `session` resolved off
`section.school_class.session` (the same FK chain
`StudentEnrollmentSerializer.validate()` already enforces must match),
so a joined-via-code student is indistinguishable from an admin-
enrolled one everywhere else in the app (roster, attendance, results,
fee) — exactly what "self-service front door onto the same enrollment"
should look like.
"""
from django.db import IntegrityError
from django.db.models import F
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Campus, CampusInviteCode, Section, StudentEnrollment
from .permissions import can_manage_section_subject, is_campus_admin_or_principal, is_campus_approved
from .serializers import CampusInviteCodeSerializer, StudentEnrollmentSerializer
from .throttles import CampusInviteCodeGenerateThrottle, CampusInviteCodeRedeemThrottle


def _invite_share_text(*, section_label: str, campus_name: str, code: str) -> str:
    """Message a class-teacher/admin can paste straight into the class
    WhatsApp/Telegram group — mirrors `common.parent_invite_links.
    parent_invite_share_text`'s "ready-to-forward" shape, but this one
    carries the plaintext code itself (unlike the parent flow, this
    code is meant to be publicly shareable within the batch — see
    `CampusInviteCode`'s model docstring)."""
    return (
        f"Join {section_label} at {campus_name} on LearnScroll! "
        f"Open the app -> Campus -> \"Join with code\" and enter: {code}"
    )


class CampusInviteCodeGenerateView(APIView):
    """
    POST /campus/<campus_id>/sections/<section_id>/invite-code/
    body (all optional): {"label": "...", "max_uses": 60, "ttl_days": 180}

    Reuses an existing still-usable code for this section instead of
    minting a fresh one every call (same "don't invalidate/duplicate
    what's already being shared" reasoning as
    `common.parent_invite_links.generate_or_reuse_parent_code`) — pass
    `"force_new": true` to deliberately rotate it (e.g. the old one
    leaked outside the batch).
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [CampusInviteCodeGenerateThrottle]

    def post(self, request, campus_id, section_id):
        campus = Campus.objects.filter(pk=campus_id).first()
        if not campus:
            return Response({"detail": "Campus not found."}, status=status.HTTP_404_NOT_FOUND)

        section = Section.objects.filter(pk=section_id, school_class__campus_id=campus_id).first()
        if not section:
            return Response({"detail": "Section not found in this campus."}, status=status.HTTP_404_NOT_FOUND)

        if not can_manage_section_subject(request.user, campus.id, section.id):
            return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)

        if not is_campus_approved(campus.id):
            return Response(
                {"detail": "This campus is pending platform verification and can't invite students yet."},
                status=status.HTTP_403_FORBIDDEN,
            )

        force_new = bool(request.data.get("force_new"))
        code_obj = None
        if not force_new:
            code_obj = (
                CampusInviteCode.objects.filter(section=section, is_active=True)
                .order_by("-created_at")
                .first()
            )
            if code_obj is not None and not code_obj.is_usable:
                code_obj = None

        if code_obj is None:
            label = (request.data.get("label") or "").strip()[:80]
            max_uses = request.data.get("max_uses")
            ttl_days = request.data.get("ttl_days")
            code_obj = CampusInviteCode.generate_for(
                section=section,
                campus=campus,
                created_by=request.user,
                label=label,
                max_uses=max_uses if max_uses not in (None, "") else None,
                ttl_days=ttl_days if ttl_days not in (None, "") else None,
            )

        data = CampusInviteCodeSerializer(code_obj).data
        data["share_text"] = _invite_share_text(
            section_label=f"{section.school_class.name} - {section.name}",
            campus_name=campus.name,
            code=code_obj.code,
        )
        return Response(data, status=status.HTTP_200_OK)


class CampusInviteCodeListView(APIView):
    """
    GET /campus/<campus_id>/invite-codes/

    Admin/Principal-HOD only — every code issued across the campus
    (any section), newest first, for a simple "manage invites" table.
    A class-teacher managing just their own section already gets their
    code straight back from the generate call above and doesn't need
    this broader view.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, campus_id):
        if not is_campus_admin_or_principal(request.user, campus_id):
            return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)
        codes = (
            CampusInviteCode.objects.filter(campus_id=campus_id)
            .select_related("section", "section__school_class", "created_by")
            .order_by("-created_at")
        )
        return Response(CampusInviteCodeSerializer(codes, many=True).data)


class CampusInviteCodeRevokeView(APIView):
    """
    POST /campus/invite-code/<code_id>/revoke/

    Admin/Principal-HOD of that code's campus, OR the class-teacher of
    that code's own section, may deactivate it early (leaked code,
    batch's join window closed, etc). Idempotent — revoking an
    already-inactive code just returns it unchanged.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, code_id):
        code_obj = CampusInviteCode.objects.select_related("section", "campus").filter(pk=code_id).first()
        if not code_obj:
            return Response({"detail": "Invite code not found."}, status=status.HTTP_404_NOT_FOUND)
        if not can_manage_section_subject(request.user, code_obj.campus_id, code_obj.section_id):
            return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)
        if code_obj.is_active:
            code_obj.is_active = False
            code_obj.save(update_fields=["is_active"])
        return Response(CampusInviteCodeSerializer(code_obj).data)


class CampusInviteCodeRedeemView(APIView):
    """
    POST /campus/invite-code/redeem/    body: {"code": "..."}

    What runs when a student pastes the code. The logged-in user IS
    the one who gets enrolled (their own `request.user` — there's no
    "redeem on someone else's behalf" here, unlike the parent-invite
    flow where the parent confirms for a student they don't control).

    Idempotent: already being enrolled in this exact section+session
    (however that happened — admin-created or a previous redeem) just
    returns that existing row and does NOT consume a `max_uses` slot —
    tapping/pasting the same code twice should never look like two
    separate joins. Deliberately does NOT resurrect a TRANSFERRED/
    GRADUATED row into ACTIVE — that status change stays an explicit
    admin action (`StudentEnrollmentViewSet`), same posture as
    `CampusParentLinkConfirmView`'s own `get_or_create` not silently
    mutating anything about a pre-existing row.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [CampusInviteCodeRedeemThrottle]

    def post(self, request):
        raw_code = (request.data.get("code") or "").strip().upper()
        if not raw_code:
            return Response({"detail": "code is required."}, status=status.HTTP_400_BAD_REQUEST)

        invite = (
            CampusInviteCode.objects.select_related("section", "section__school_class", "campus")
            .filter(code=raw_code)
            .first()
        )
        if not invite:
            return Response({"detail": "Invalid invite code."}, status=status.HTTP_400_BAD_REQUEST)

        session = invite.section.school_class.session

        # Idempotency check FIRST, before the is_usable/expiry/max_uses
        # gate below — a student who already joined via this exact code
        # must never get locked out of their own already-existing row
        # just because the code later expired or other classmates
        # pushed it past max_uses. Capacity/expiry only gates NEW joins.
        existing = StudentEnrollment.objects.filter(
            student=request.user, section=invite.section, session=session,
        ).first()
        if existing:
            return Response(
                {
                    "enrollment": StudentEnrollmentSerializer(existing).data,
                    "campus_id": str(invite.campus_id),
                    "campus_name": invite.campus.name,
                    "already_enrolled": True,
                },
                status=status.HTTP_200_OK,
            )

        if not invite.is_usable:
            return Response({"detail": "This invite code is no longer active."}, status=status.HTTP_400_BAD_REQUEST)
        if not is_campus_approved(invite.campus_id):
            return Response(
                {"detail": "This campus is pending platform verification and can't onboard students yet."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            enrollment = StudentEnrollment.objects.create(
                student=request.user,
                section=invite.section,
                session=session,
                status=StudentEnrollment.Status.ACTIVE,
            )
        except IntegrityError:
            # Race: two redeem requests for the same student landed at
            # once (double-tap) — the unique_enrollment_per_section_session
            # constraint caught it first; just return the row that won.
            enrollment = StudentEnrollment.objects.filter(
                student=request.user, section=invite.section, session=session,
            ).first()
            return Response(
                {
                    "enrollment": StudentEnrollmentSerializer(enrollment).data,
                    "campus_id": str(invite.campus_id),
                    "campus_name": invite.campus.name,
                    "already_enrolled": True,
                },
                status=status.HTTP_200_OK,
            )

        CampusInviteCode.objects.filter(pk=invite.pk).update(uses_count=F("uses_count") + 1)

        return Response(
            {
                "enrollment": StudentEnrollmentSerializer(enrollment).data,
                "campus_id": str(invite.campus_id),
                "campus_name": invite.campus.name,
                "already_enrolled": False,
            },
            status=status.HTTP_201_CREATED,
        )
