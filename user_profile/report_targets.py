"""
user_profile/report_targets.py - turns (target_type, target_id) of a report into
the real object, the ACCOUNT behind it, and a short evidence text. Also knows how
to hide / restore each kind of target. Audit item 16 (reports on DMs, class chat,
live class, profile photo) lives here.

Reporting a private thing is only allowed for people who can already SEE it:
  message       you are a participant of that conversation
  class_chat    you have access to that classroom (teacher / pass / trial / staff)
  live_session  same
  profile_photo anybody who can see the profile (not blocked either way)
Raises LookupError (nothing to report / not allowed) or ValueError (own content).
The reported person is never told who reported.
"""

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

EVIDENCE_LEN = 500
# Targets that are hidden automatically when a threshold is reached.
AUTO_HOLD_TARGETS = ("post", "comment")
# Targets that are ONLY hidden for child safety (or by a staff 'hide' action).
STAFF_HIDE_TARGETS = ("story", "message", "class_chat", "profile_photo")
COPYRIGHT_STATES = ("copyright_hold", "copyright_removed", "legal_removed")


def _clip(text):
    return (text or "").strip()[:EVIDENCE_LEN]


def resolve_target(reporter, target_type, target_id):
    """-> (reported_user, evidence_text). See module docstring for the errors."""
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError

    User = get_user_model()
    target_id = str(target_id)
    reported_user, evidence = None, ""
    try:
        if target_type == "user":
            reported_user = User.objects.filter(id=int(target_id), is_active=True).first()
        elif target_type == "post":
            from post.models import Post

            obj = Post.objects.filter(id=target_id, is_deleted=False).select_related("user").first()
            reported_user = obj.user if obj else None
            evidence = _clip(f"{obj.title or ''} {obj.content or ''}") if obj else ""
        elif target_type == "comment":
            from post.models import PostComment

            obj = PostComment.objects.filter(id=target_id, is_deleted=False).select_related("user").first()
            reported_user = obj.user if obj else None
            evidence = _clip(getattr(obj, "content", "")) if obj else ""
        elif target_type == "story":
            from post.models import Story

            obj = Story.objects.filter(id=target_id, is_deleted=False).select_related("user").first()
            reported_user = obj.user if obj else None
        elif target_type == "message":
            from message.models import ConversationParticipant, Message

            obj = Message.objects.filter(id=target_id, deleted_for_everyone=False).select_related("sender").first()
            if obj and not ConversationParticipant.objects.filter(conversation_id=obj.conversation_id, user=reporter).exists():
                obj = None  # not your conversation -> same answer as "does not exist"
            reported_user = obj.sender if obj else None
            evidence = _clip(obj.text) if obj else ""
        elif target_type == "class_chat":
            from tuitionclass.models import ChatMessage

            obj = ChatMessage.objects.filter(id=int(target_id), is_deleted=False).select_related(
                "sender", "session__classroom").first()
            if obj and not _classroom_access(obj.session.classroom, reporter):
                obj = None
            reported_user = obj.sender if obj else None
            evidence = _clip(obj.message) if obj else ""
        elif target_type == "live_session":
            from tuitionclass.models import ClassSession

            obj = ClassSession.objects.filter(id=int(target_id)).select_related("classroom__teacher").first()
            if obj and not _classroom_access(obj.classroom, reporter):
                obj = None
            reported_user = obj.classroom.teacher if obj else None
            evidence = _clip(f"live class: {getattr(obj, 'title', '') or obj.classroom}") if obj else ""
        elif target_type == "profile_photo":
            u = User.objects.filter(id=int(target_id), is_active=True).first()
            if u is not None and not u.profile_photo:
                u = None
            if u is not None:
                from .views import is_blocked_between

                if is_blocked_between(reporter, u):
                    u = None
            reported_user = u
            evidence = _clip(u.profile_photo.name) if u else ""
    except (ValueError, TypeError, ValidationError):
        reported_user = None
    if reported_user is None:
        raise LookupError("Nothing to report here.")
    if reported_user.id == reporter.id:
        raise ValueError("You can't report your own account or content.")
    return reported_user, evidence


def _classroom_access(classroom, user):
    try:
        from tuitionclass.views import _has_room_access

        return bool(classroom.teacher_id == user.id or _has_room_access(classroom, user))
    except Exception:
        logger.exception("classroom access check failed")
        return False


# --------------------------------------------------------------------------
# hide / restore
# --------------------------------------------------------------------------
def _adjust_comment_count(comment_id, delta):
    """Post.comments_count only counts VISIBLE top-level comments (CommentHideAPIView does the same +/-1).
    Best-effort: a failure here must not undo the hide / restore itself."""
    try:
        from django.db.models import F
        from django.db.models.functions import Greatest

        from post.models import Post, PostComment

        row = PostComment.objects.filter(pk=comment_id).values_list("post_id", "parent_id").first()
        if row and row[1] is None:
            Post.objects.filter(pk=row[0]).update(comments_count=Greatest(F("comments_count") + delta, 0))
    except Exception:
        logger.exception("comments_count adjust failed (comment=%s)", comment_id)



def _quarantine_photo(user):
    """Copy the photo aside BEFORE clearing it (the User model deletes the old file
    when the field changes) - evidence must survive. Returns the stored path."""
    f = user.profile_photo
    if not f:
        return ""
    try:
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        name = str(f.name).rsplit("/", 1)[-1]
        f.open("rb")
        try:
            data = f.read()
        finally:
            f.close()
        return default_storage.save(f"moderation_quarantine/{user.pk}/{name}", ContentFile(data))
    except Exception:
        logger.exception("profile photo quarantine failed (user=%s)", user.pk)
        return ""


def hide(target_type, target_id, *, mode="staff"):
    """Hide one target. mode 'hold' = automatic & reversible, 'staff' = moderator decision.
    Returns (changed: bool, extra: str) - `extra` is e.g. the quarantine path."""
    try:
        if target_type == "post":
            from post.models import Post

            qs = Post.objects.filter(pk=target_id).exclude(moderation_status__in=COPYRIGHT_STATES)
            if mode == "hold":
                return qs.filter(moderation_status="approved").update(moderation_status="flagged") > 0, ""
            return qs.update(moderation_status="rejected") > 0, ""
        if target_type == "comment":
            from post.models import PostComment

            changed = PostComment.objects.filter(pk=target_id, is_hidden=False).update(is_hidden=True) > 0
            if changed:
                _adjust_comment_count(target_id, -1)  # a hidden top-level comment is not counted (same as CommentHideAPIView)
            return changed, ""
        if target_type == "story":
            from post.models import Story

            return Story.objects.filter(pk=target_id, is_deleted=False).update(
                is_deleted=True, deleted_at=timezone.now()) > 0, ""
        if target_type == "message":
            from message.models import Message

            return Message.objects.filter(pk=target_id, deleted_for_everyone=False).update(deleted_for_everyone=True) > 0, ""
        if target_type == "class_chat":
            from tuitionclass.models import ChatMessage

            return ChatMessage.objects.filter(pk=target_id, is_deleted=False).update(is_deleted=True, is_flagged=True) > 0, ""
        if target_type == "profile_photo":
            from django.contrib.auth import get_user_model

            user = get_user_model().objects.filter(pk=int(target_id)).first()
            if user is None or not user.profile_photo:
                return False, ""
            path = _quarantine_photo(user)
            if not path:
                return False, ""  # never clear a photo we could not copy first
            user.profile_photo = None
            user.save(update_fields=["profile_photo"])
            return True, path
    except Exception:
        logger.exception("hide failed (%s:%s)", target_type, target_id)
    return False, ""  # user / live_session: a human decides, nothing is hidden automatically


def restore(target_type, target_id, *, quarantine_path="", held_only=False):
    """Undo hide(). Returns True if something changed. `held_only=True` restores a post
    only if it is in the automatic 'flagged' hold - never one a person (or the word-list
    screen) rejected."""
    try:
        if target_type == "post":
            from post.models import Post

            states = ("flagged",) if held_only else ("flagged", "rejected")
            return Post.objects.filter(pk=target_id, moderation_status__in=states).update(
                moderation_status="approved") > 0
        if target_type == "comment":
            from post.models import PostComment

            changed = PostComment.objects.filter(pk=target_id, is_hidden=True).update(is_hidden=False) > 0
            if changed:
                _adjust_comment_count(target_id, +1)
            return changed
        if target_type == "story":
            # Not restorable: is_deleted is also how an ENDED or user-deleted story is stored, so
            # un-deleting here could bring back a story nobody wanted back (copyrights/targets.py
            # has its own `takedown_hidden` flag for copyright takedowns). Stories are therefore
            # not appealable - see resolve_reports.
            return False
        if target_type == "message":
            from message.models import Message

            return Message.objects.filter(pk=target_id, deleted_for_everyone=True).update(deleted_for_everyone=False) > 0
        if target_type == "class_chat":
            from tuitionclass.models import ChatMessage

            return ChatMessage.objects.filter(pk=target_id, is_deleted=True).update(is_deleted=False) > 0
        if target_type == "profile_photo" and quarantine_path:
            from django.contrib.auth import get_user_model
            from django.core.files.storage import default_storage

            user = get_user_model().objects.filter(pk=int(target_id)).first()
            if user is None or user.profile_photo or not default_storage.exists(quarantine_path):
                return False
            user.profile_photo.name = quarantine_path  # point at the quarantined copy
            user.save(update_fields=["profile_photo"])
            return True
    except Exception:
        logger.exception("restore failed (%s:%s)", target_type, target_id)
    return False


def owner_of(target_type, target_id):
    """The account behind a target (used by appeals / audit). None if gone."""
    try:
        if target_type in ("user", "profile_photo"):
            from django.contrib.auth import get_user_model

            return get_user_model().objects.filter(pk=int(target_id)).first()
        if target_type == "post":
            from post.models import Post

            o = Post.objects.filter(pk=target_id).select_related("user").first()
            return o.user if o else None
        if target_type == "comment":
            from post.models import PostComment

            o = PostComment.objects.filter(pk=target_id).select_related("user").first()
            return o.user if o else None
        if target_type == "story":
            from post.models import Story

            o = Story.objects.filter(pk=target_id).select_related("user").first()
            return o.user if o else None
        if target_type == "message":
            from message.models import Message

            o = Message.objects.filter(pk=target_id).select_related("sender").first()
            return o.sender if o else None
        if target_type == "class_chat":
            from tuitionclass.models import ChatMessage

            o = ChatMessage.objects.filter(pk=int(target_id)).select_related("sender").first()
            return o.sender if o else None
        if target_type == "live_session":
            from tuitionclass.models import ClassSession

            o = ClassSession.objects.filter(pk=int(target_id)).select_related("classroom__teacher").first()
            return o.classroom.teacher if o else None
    except Exception:
        logger.exception("owner_of failed (%s:%s)", target_type, target_id)
    return None
