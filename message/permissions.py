# message/permissions.py
from rest_framework import permissions

from .models import ConversationParticipant, GroupMember


class IsConversationParticipant(permissions.BasePermission):
    """
    Sirf wahi user access kare jo is conversation ka active member hai.
    Detail routes pe `pk` conversation/message dono ho sakta hai isliye
    has_object_permission me obj se hi conversation nikaal lete hain.
    """
    message = "Aap is conversation ke member nahi hain."

    def has_permission(self, request, view):
        conversation_id = view.kwargs.get('conversation_id') or view.kwargs.get('pk')
        if not conversation_id or not request.user or not request.user.is_authenticated:
            return bool(request.user and request.user.is_authenticated)
        # Message routes ke liye pk message-id hota hai, wahan object-level check
        # hi authoritative hai — yahan sirf coarse pre-check hai.
        return True

    def has_object_permission(self, request, view, obj):
        conversation = getattr(obj, 'conversation', obj)
        return ConversationParticipant.objects.filter(
            conversation=conversation, user=request.user, left_at__isnull=True
        ).exists()


class IsMessageSender(permissions.BasePermission):
    """Edit/delete sirf apna hi message kar sakta hai."""
    message = "Aap sirf apna message edit/delete kar sakte hain."

    def has_object_permission(self, request, view, obj):
        return obj.sender_id == request.user.id


class IsGroupAdminOrModerator(permissions.BasePermission):
    """Group settings / member management sirf admin ya moderator kar sakta hai."""
    message = "Sirf group admin/moderator ye action kar sakte hain."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        group_id = view.kwargs.get('group_id') or view.kwargs.get('pk')
        if not group_id:
            return True
        return GroupMember.objects.filter(
            group_id=group_id, user=request.user, role__in=['admin', 'moderator'], is_banned=False
        ).exists()