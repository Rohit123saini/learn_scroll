# campus/parent_invite.py
"""
"Add parent" automation for the campus app — three pieces, matching the
three asked-for flows:

  1. CampusParentInviteBulkView   -> one click, sends every currently
     enrolled student their own parent-add link (student then forwards
     it to their parent — WhatsApp/SMS/whatever).
  2. CampusParentInviteSingleView -> manual, one specific student.
  3. CampusParentLinkConfirmView  -> what actually runs when the parent
     taps the link. Creates the `CampusParentLink` row.

Reuses the EXISTING `message.ParentAccessCode` machinery for the
code/link itself (exact same model `ClassroomParentCodeGenerateView` in
`tuitionclass/parent_link_views.py` already uses) — no new model, no new
migration. `CampusParentLink` (already in campus/models.py) is still the
row that actually grants campus-scoped parent access; this file is just
the missing "make + send + confirm the link" plumbing around it.

✅ FIXED (this pass) — the pre-existing `ParentLinkVerifyView`
(campus/views.py) used to call `bridge.resolve_parent_from_token(token)`
and unpack the result as `parent_user, student_user = ...`.
`resolve_parent_from_token()` actually returns a `ParentTokenResolution`
object with attributes `.student` / `.parent_access_code` — it has no
`.parent` / parent-user concept at all (Parent Mode is deliberately
loginless, see message/models.py's own ParentAccessCode docstring) and
isn't a 2-tuple, so that unpack raised a ValueError/TypeError on every
real call — the endpoint 500'd unconditionally. `ParentLinkVerifyView`
now resolves the code directly against `ParentAccessCode` instead, the
exact same working logic `CampusParentLinkConfirmView` below already
used — see that view's own updated docstring in campus/views.py.
`CampusParentLinkConfirmView` below remains the primary/recommended
write path (that's what campus_service.dart's `confirmParentLink()`
calls); `ParentLinkVerifyView` is kept only so any existing caller of
its URL/name (`verifyParentLink()`) keeps working too, now correctly.
"""
from django.db.models import Q
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.parent_invite_links import (
    build_parent_invite_link,
    generate_or_reuse_parent_code,
    parent_invite_share_text,
)
from message.models import ParentAccessCode
from message.services import create_bell_rows_for_push

from .models import Campus, CampusParentLink, StudentEnrollment
from .permissions import is_campus_admin_or_principal, is_campus_approved
from .serializers import CampusParentLinkSerializer
from .throttles import CampusParentLinkVerifyThrottle


def _enrolled_student_ids(campus_id, *, department_id=None, school_class_id=None, section_id=None):
    """Currently-ACTIVE enrolled students for a campus, optionally narrowed
    to one department/class/section. Same FK chain
    (Section -> SchoolClass -> Campus) every other scope-filtered campus
    view already walks."""
    qs = StudentEnrollment.objects.filter(
        status=StudentEnrollment.Status.ACTIVE,
        section__school_class__campus_id=campus_id,
    )
    if section_id:
        qs = qs.filter(section_id=section_id)
    if school_class_id:
        qs = qs.filter(section__school_class_id=school_class_id)
    if department_id:
        qs = qs.filter(section__school_class__department_id=department_id)
    return list(qs.values_list("student_id", flat=True).distinct())


class _CampusAdminOnlyMixin:
    """Shared 'is this user an admin/principal of THIS campus' gate — every
    view below needs it, none of them fit CampusMemberScopedMixin's
    list/detail-queryset shape (these are plain action endpoints)."""

    def _require_campus_admin(self, request, campus_id):
        campus = Campus.objects.filter(pk=campus_id).first()
        if not campus:
            return None, Response({"detail": "Campus not found."}, status=status.HTTP_404_NOT_FOUND)
        if not is_campus_admin_or_principal(request.user, campus.id):
            return None, Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)
        # FIX — CampusParentLinkConfirmView already refused to let a
        # parent confirm on a not-yet-platform-approved campus, but the
        # two views that SEND the invite in the first place (bulk/single,
        # both use this mixin) had no matching check. Without this, an
        # admin on a still-pending campus could fire off invite links/push
        # notifications that are guaranteed to fail the moment a parent
        # actually taps them ("campus pending verification") — confusing,
        # avoidable dead-end. Checked here once, centrally, so every
        # subclass gets it automatically.
        if not is_campus_approved(campus.id):
            return None, Response(
                {"detail": "This campus is pending platform verification and can't invite parents yet."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return campus, None


class CampusParentInviteBulkView(_CampusAdminOnlyMixin, APIView):
    """
    POST /campus/<campus_id>/parent-invite/bulk/
    body (all optional): {"department": "<id>", "school_class": "<id>",
                           "section": "<id>", "label": "Parent"}

    One click -> every currently-enrolled student in scope (whole campus by
    default, or narrowed to one department/class/section if given) gets a
    bell + push notification carrying their own parent-add link. The
    student forwards that link to their parent; the parent tapping it hits
    CampusParentLinkConfirmView below.

    Admin/Principal-HOD only (same authority `IsCampusAdminOrPrincipal`
    already grants everywhere else in this app for campus-wide actions).
    """
    permission_classes = [IsAuthenticated]
    # FIX — this view had NO throttle at all (only the parent-facing
    # confirm/ endpoint did). Admin-only cuts most of the risk, but a
    # careless/compromised admin token could otherwise hammer this and
    # spam every enrolled student repeatedly. Reusing
    # CampusParentLinkVerifyThrottle rather than inventing a new scope —
    # same "don't let this action fire in a tight loop" intent applies.
    throttle_classes = [CampusParentLinkVerifyThrottle]

    def post(self, request, campus_id):
        campus, err = self._require_campus_admin(request, campus_id)
        if err:
            return err

        label = (request.data.get("label") or "").strip()[:50] or "Parent"
        student_ids = _enrolled_student_ids(
            campus.id,
            department_id=request.data.get("department"),
            school_class_id=request.data.get("school_class"),
            section_id=request.data.get("section"),
        )

        from django.contrib.auth import get_user_model
        User = get_user_model()
        students = User.objects.filter(id__in=student_ids)

        sent, skipped = [], []
        for student in students:
            active_count = ParentAccessCode.objects.filter(student=student, is_active=True).count()
            if active_count >= getattr(ParentAccessCode, "MAX_ACTIVE_CODES", 5):
                skipped.append({"student_id": student.id, "reason": "max_active_codes_reached"})
                continue
            try:
                code_obj, _created = generate_or_reuse_parent_code(student, label)
                link = build_parent_invite_link(code=code_obj.code, campus_id=campus.id)
                create_bell_rows_for_push(
                    recipient_ids=[student.id],
                    notif_type="parent_invite_link",
                    title="Add your parent",
                    message=parent_invite_share_text(
                        student_name=student.get_full_name() or student.username, link=link,
                    ),
                    data={"type": "parent_invite_link", "link": link, "campus_id": str(campus.id)},
                )
                sent.append({"student_id": student.id, "student_name": student.get_full_name() or student.username})
            except Exception:
                skipped.append({"student_id": student.id, "reason": "notify_failed"})

        return Response(
            {
                "campus_id": campus.id,
                "total_students": len(student_ids),
                "sent": sent,
                "sent_count": len(sent),
                "skipped": skipped,
            },
            status=status.HTTP_200_OK,
        )


class CampusParentInviteSingleView(_CampusAdminOnlyMixin, APIView):
    """
    POST /campus/<campus_id>/students/<student_id>/parent-invite/
    body (optional): {"label": "Parent", "ttl_days": 180}

    Manual, one specific student — same generation as the bulk view but
    for exactly one row, and it hands the link straight back in the
    response too (not just a push), so an admin can copy/share it
    immediately without waiting on a notification.
    """
    permission_classes = [IsAuthenticated]
    # FIX — same "was completely unthrottled" gap as the bulk view above.
    throttle_classes = [CampusParentLinkVerifyThrottle]

    def post(self, request, campus_id, student_id):
        campus, err = self._require_campus_admin(request, campus_id)
        if err:
            return err

        if not StudentEnrollment.objects.filter(
            student_id=student_id,
            status=StudentEnrollment.Status.ACTIVE,
            section__school_class__campus_id=campus.id,
        ).exists():
            return Response(
                {"detail": "This user is not an actively enrolled student of this campus."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from django.contrib.auth import get_user_model
        User = get_user_model()
        student = User.objects.filter(id=student_id).first()
        if not student:
            return Response({"detail": "Student not found."}, status=status.HTTP_404_NOT_FOUND)

        label = (request.data.get("label") or "").strip()[:50] or "Parent"
        ttl_days = request.data.get("ttl_days")

        active_count = ParentAccessCode.objects.filter(student=student, is_active=True).count()
        if active_count >= getattr(ParentAccessCode, "MAX_ACTIVE_CODES", 5):
            return Response(
                {"detail": "This student already has the maximum number of active parent codes."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        kwargs = {"label": label}
        if ttl_days:
            kwargs["ttl_days"] = ttl_days
        code_obj = ParentAccessCode.generate_for(student, **kwargs)
        link = build_parent_invite_link(code=code_obj.code, campus_id=campus.id)

        try:
            create_bell_rows_for_push(
                recipient_ids=[student.id],
                notif_type="parent_invite_link",
                title="Add your parent",
                message=parent_invite_share_text(
                    student_name=student.get_full_name() or student.username, link=link,
                ),
                data={"type": "parent_invite_link", "link": link, "campus_id": str(campus.id)},
            )
        except Exception:
            pass  # never block the response — admin still gets the link below

        return Response(
            {
                "student_id": student.id,
                "student_name": student.get_full_name() or student.username,
                "code": code_obj.code,
                "link": link,
                "masked_code": code_obj.masked_code,
                "expires_at": code_obj.expires_at,
            },
            status=status.HTTP_201_CREATED,
        )


class CampusParentLinkConfirmView(APIView):
    """
    POST /campus/parent-link/confirm/    body: {"campus": <id>, "code": "..."}

    What runs when the parent taps the link and hits "Confirm". The
    logged-in user making this request IS the parent going forward (their
    own login.User account — campus's `CampusParentLink.parent` is a real
    FK, unlike tuitionclass's loginless Parent Mode). Resolves the student
    straight off `ParentAccessCode` (code + is_active + not expired) —
    deliberately NOT going through `bridge.resolve_parent_from_token()`
    (see this file's module docstring for why that path is broken).

    Idempotent: tapping the same link twice just returns the same
    already-existing link (get_or_create), same as the old
    `ParentLinkVerifyView` intended.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [CampusParentLinkVerifyThrottle]

    def post(self, request):
        code = (request.data.get("code") or "").strip()
        campus_id = request.data.get("campus")
        if not code or not campus_id:
            return Response({"detail": "campus and code are required."}, status=status.HTTP_400_BAD_REQUEST)

        campus = Campus.objects.filter(pk=campus_id).first()
        if not campus:
            return Response({"detail": "Campus not found."}, status=status.HTTP_404_NOT_FOUND)
        if not is_campus_approved(campus.id):
            return Response(
                {"detail": "This campus is pending platform verification and can't link parents yet."},
                status=status.HTTP_403_FORBIDDEN,
            )

        access_code = ParentAccessCode.objects.filter(code=code, is_active=True).select_related("student").first()
        if not access_code or access_code.is_expired:
            return Response({"detail": "Invalid or expired link."}, status=status.HTTP_400_BAD_REQUEST)

        student = access_code.student
        if not StudentEnrollment.objects.filter(
            student=student, status=StudentEnrollment.Status.ACTIVE, section__school_class__campus_id=campus.id,
        ).exists():
            return Response(
                {"detail": "This student isn't enrolled at this campus."}, status=status.HTTP_400_BAD_REQUEST,
            )

        link, created = CampusParentLink.objects.get_or_create(
            campus=campus, student=student, parent=request.user,
        )

        # Best-effort — let both the student and the campus admins know a
        # parent just linked (mirrors ClassroomParentCodeGenerateView's own
        # "student should know someone gained access" notify pattern).
        try:
            create_bell_rows_for_push(
                recipient_ids=[student.id],
                notif_type="parent_link_confirmed",
                title="Parent added",
                message=(
                    f"{request.user.get_full_name() or request.user.username} confirmed as your parent "
                    f"for {campus.name}."
                ),
                data={"type": "parent_link_confirmed", "campus_id": str(campus.id)},
            )
        except Exception:
            pass

        return Response(
            CampusParentLinkSerializer(link).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
