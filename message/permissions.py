# message/permissions.py
from django.utils import timezone
from rest_framework import permissions

from .group_rules import is_group_admin_or_mod
from .models import ConversationParticipant, Group, GroupMember, ParentToken


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
        # 🔥 FIX — pehle yahan apna alag raw `GroupMember.objects.filter(...)`
        # query tha. Poori app me isi "admin/mod, not banned" rule ki 4
        # independent copies mil gayi thi (yahan, `views.py`'s
        # `_require_admin`, `disappearing_messages`,
        # `add_participant_to_conversation`) — sab ab `group_rules.
        # is_group_admin_or_mod` (cached, single source of truth) use
        # karte hain.
        group = Group.objects.filter(id=group_id).first()
        if group is None:
            return False
        return is_group_admin_or_mod(group, request.user.id)


# ======================================================================
# 🔥 NAYA — Parent/Guardian Mode (Feature 8)
# ======================================================================
class HasValidParentToken(permissions.BasePermission):
    """
    Parent Mode read-only endpoints ke liye. Normal student login
    (`IsAuthenticated` / `request.user`) yahan lagu NAHI hota — parent ka
    koi `User` row hi nahi hai.

    Parent apna `parent_token` (verify-code step se mila, see
    `views_parent.ParentVerifyCodeView`) `X-Parent-Token` header me
    bhejta hai. Yahan validate karke `request.parent_access_code` aur
    `request.parent_student` attach kar dete hain, taaki view ko dobara
    DB hit na karna pade.

    Jaan-boojh kar `request.user` ko chhua nahi gaya — agar hum
    `request.user` ko student ka `User` bana dete to kisi bhi normal
    permission/view me accidentally "student khud request kar raha hai"
    jaisa treat ho sakta tha. Poori tarah alag, single-purpose attribute
    rakha hai taaki blast-radius chhota rahe.

    🔧 GAP FIX — TTL/expiry. Pehle sirf `parent_access_code__is_active
    =True` check hota tha — code/token dono hamesha valid rehte the jab
    tak student khud revoke na kare (parent ka phone kho jaaye to
    unauthorized access indefinitely chalta rehta). Ab do independent
    checks add hue hain (dono `models.py` me implement kiye — `ParentAccessCode.
    expires_at`/`is_expired` aur `ParentToken.INACTIVITY_TTL_DAYS`/`is_expired`):

      1. Code ki apni absolute expiry (`ParentAccessCode.is_expired`) —
         cross ho gayi to poora access band, is code ke SAARE devices
         ke liye — jab tak student `renew()` na kare
         (`ParentAccessCodeRenewView`).
      2. Is EK token ki apni rolling inactivity expiry
         (`ParentToken.is_expired`, default 30 din) — sirf isi device
         ko affect karta hai, code ya baaki devices ko nahi. Ek stale
         phone apne aap cut ho jaata hai bina student ko kuch karna
         pade.
    """
    message = "Parent access code/token invalid, expire ya revoke ho chuka hai."

    def has_permission(self, request, view):
        token = request.headers.get('X-Parent-Token', '')
        if not token:
            return False

        parent_token = ParentToken.objects.select_related(
            'parent_access_code', 'parent_access_code__student',
        ).filter(
            token=token, parent_access_code__is_active=True,
        ).first()
        if not parent_token:
            return False

        # 🔧 GAP FIX — code-level TTL. `is_active=True` filter ke baad bhi
        # code apni `expires_at` cross kar chuka ho sakta hai (student ne
        # kabhi kuch nahi kiya, bas time nikal gaya) — is_active=False
        # jaisa hi treat karo.
        if parent_token.parent_access_code.is_expired:
            return False

        # 🔧 GAP FIX — token-level inactivity TTL, sirf is ek device ko
        # affect karta hai.
        if parent_token.is_expired:
            return False

        request.parent_access_code = parent_token.parent_access_code
        request.parent_student = parent_token.parent_access_code.student
        parent_token.last_seen_at = timezone.now()
        parent_token.save(update_fields=['last_seen_at', 'updated_at'])
        return True