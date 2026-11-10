"""
tuitionclass/views/parent.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

from core.models import Notification
from core.services import create_notification
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from ..models import Classroom, ParentMessageTemplate, ParentTeacherMessage
from ..serializers import (
    ParentMessageTemplateSerializer,
    ParentTeacherMessageReplySerializer,
    ParentTeacherMessageSerializer,
)

from ._common import TuitionClassPagination
from .access import _can_manage_classroom, _can_view_classroom_internals


# ---------------------------------------------------------------------------
# 20a. PARENT MESSAGE TEMPLATE / PARENT-TEACHER MESSAGE (task 69)
#
# See the module note above ParentMessageTemplate in models.py for the full
# design. In short: a parent/student can only ever send one of a fixed set
# of admin-authored template sentences to a classroom's teacher — never
# free text — which is why ParentMessageTemplateViewSet below is read-only
# (list/retrieve; managed via the Django admin) and
# ParentTeacherMessageSerializer.resolved_message is always the server's
# own `template.resolve(...)` output, never anything taken from the request
# body. The teacher's reply is deliberately NOT template-locked (see
# ParentTeacherMessageReplySerializer) since that side is already gated by
# _can_manage_classroom, the same trusted-staff boundary ClassQuery.answer
# relies on above.
# ---------------------------------------------------------------------------
class ParentMessageTemplateViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read-only catalog of the platform-curated templates a parent/student
    can pick from. Not classroom-scoped — this is a single, global list
    (like PollTemplate but platform-authored rather than per-teacher) — so
    every authenticated user sees the same active templates. Optional
    `?category=<ParentMessageTemplate.Category>` filter for the picker UI.
    """

    serializer_class = ParentMessageTemplateSerializer
    permission_classes = [IsAuthenticated]
    queryset = ParentMessageTemplate.objects.filter(is_active=True)
    pagination_class = None  # small, fixed catalog — no pagination needed

    def get_queryset(self):
        qs = super().get_queryset()
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        return qs


class ParentTeacherMessageViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    """An enrolled parent/student sends one of the fixed templates to a
    classroom's teacher; the classroom's teacher/co-teacher/moderator
    replies via the `reply` action. No update/destroy on this resource at
    all — a sent message is a fixed audit record (see resolved_message's
    docstring in models.py), not something either side edits after the fact.

    Visibility mirrors ClassQueryViewSet:
        - GET .../parent-messages/?classroom=<id> — if the caller manages
          that classroom, they see every message sent to it; anyone else
          only sees the ones they personally sent.
        - GET .../parent-messages/ (no classroom filter) — "my sent
          messages" across every classroom.
    """

    serializer_class = ParentTeacherMessageSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination
    queryset = ParentTeacherMessage.objects.select_related(
        "classroom", "session", "template", "sender", "replied_by"
    )

    # NOTE (fix — misuse-prevention gap, task 69 itself): the whole point of
    # this feature is that a parent/student can't spam a teacher with
    # free text — but with no rate limit at all on create(), the same
    # abuser could still spam a teacher with the fixed templates instead,
    # one create() call per second, exactly like ChatMessageViewSet.create
    # could before its own chat_message_create scope was added above. Same
    # pattern here: scoped so it only throttles create(), never
    # list()/retrieve(), and add e.g. {"parent_message_create": "10/min"}
    # to DRF's DEFAULT_THROTTLE_RATES in settings.py for this to take
    # effect — see session_join/coupon_validate/chat_message_create for
    # the established pattern (skipping this step raises
    # ImproperlyConfigured on the very first message a parent sends).
    def get_throttles(self):
        if self.action == "create":
            self.throttle_scope = "parent_message_create"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get("classroom")
        if classroom_id:
            qs = qs.filter(classroom_id=classroom_id)
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            if classroom and _can_manage_classroom(classroom, self.request.user):
                return qs
        return qs.filter(sender=self.request.user)

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if not _can_view_classroom_internals(classroom, self.request.user):
            raise PermissionDenied("A pass (active or expired) is required to message this classroom's teacher.")

        user = self.request.user
        template = serializer.validated_data["template"]
        resolved_message = template.resolve(
            child_name=user.get_full_name() or user.username,
            classroom_title=classroom.title,
        )
        message = serializer.save(sender=user, resolved_message=resolved_message)

        create_notification(
            recipient=classroom.teacher,
            notif_type=Notification.NotifType.PARENT_MESSAGE_RECEIVED,
            title="New message from a parent/student",
            message=f"{user.get_full_name() or user.username}: {resolved_message[:80]}",
            classroom=classroom,
            session=message.session,
        )
        # NOTE: no _safe_delay(notify_parent_message_received, ...) push/
        # digest queue here yet — every sibling action in this file queues
        # one (see notify_query_answered above), but the real Celery
        # task module wasn't part of this review, so a task import here
        # can't be verified against it. The in-app Notification row above
        # is created regardless; wire up the push/WhatsApp fan-out task the
        # same way once tasks.py is available.

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        message = self.get_object()
        if not _can_manage_classroom(message.classroom, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can reply.")
        if message.status == ParentTeacherMessage.Status.RESPONDED:
            raise ValidationError("This message has already been replied to.")
        serializer = ParentTeacherMessageReplySerializer(message, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(
            replied_by=request.user, replied_at=timezone.now(), status=ParentTeacherMessage.Status.RESPONDED
        )
        create_notification(
            recipient=message.sender,
            notif_type=Notification.NotifType.PARENT_MESSAGE_REPLIED,
            title="Your message was replied to",
            message=f"'{message.resolved_message[:60]}' got a reply in '{message.classroom.title}'.",
            classroom=message.classroom,
            session=message.session,
        )
        return Response(ParentTeacherMessageSerializer(message).data)
