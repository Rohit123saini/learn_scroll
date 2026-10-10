"""
user_profile/automod.py

Wires auto-moderation (common/moderation.py) to every user-generated
surface via post_save receivers, so ANY code path that writes the content
(API view, admin, shell, import script) is covered without touching each view.

Surfaces -> model/field screened:
    post     post.Post.title + content
    comment  post.PostComment.content
    story    post.Story.caption
    bio      login.User.bio
    dm       message.Message.text   (switch off: AUTOMOD_SCREEN_DMS = False)

Everything here is best-effort and FLAG-ONLY: it must never break or delay
the save that triggered it, never hides/deletes content. Failures are logged.
When the cheap pass finds nothing and AUTOMOD_AI_ENABLED is on, the AI
second pass is queued to Celery (after commit), not run inline.
"""

import hashlib
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models.signals import post_save

from common.moderation import screen_text, severity_for

logger = logging.getLogger(__name__)

SNIPPET_LEN = 200


def _hash(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8", "ignore")).hexdigest()


def record_flag(*, target_type, target_id, user_id, text, reason, source="wordlist"):
    """Create the flag unless the same (target, text) is already flagged.
    Returns the AutoModerationFlag or None. Never raises."""
    from .models import AutoModerationFlag

    try:
        with transaction.atomic():
            flag, _ = AutoModerationFlag.objects.get_or_create(
                target_type=target_type,
                target_id=str(target_id),
                text_hash=_hash(text),
                defaults={
                    "user_id": user_id,
                    "reason": reason,
                    "source": source,
                    "severity": severity_for(reason),
                    "snippet": text.strip()[:SNIPPET_LEN],
                },
            )
        return flag
    except IntegrityError:
        return None  # lost a race to an identical flag — fine
    except Exception:
        logger.exception("automod: could not record flag for %s:%s", target_type, target_id)
        return None


def screen_and_flag(*, target_type, target_id, user_id, text, surface):
    """Cheap pass now; AI pass queued if the cheap pass is clean."""
    text = (text or "").strip()
    if not text:
        return None
    flagged, reason = screen_text(text, surface)
    if flagged:
        return record_flag(
            target_type=target_type, target_id=target_id, user_id=user_id, text=text, reason=reason,
        )
    if getattr(settings, "AUTOMOD_AI_ENABLED", False):
        try:
            from .tasks import automod_ai_screen

            payload = dict(target_type=target_type, target_id=str(target_id), user_id=user_id, text=text[:2000])
            transaction.on_commit(lambda: automod_ai_screen.delay(**payload))
        except Exception:
            logger.exception("automod: could not queue AI screen for %s:%s", target_type, target_id)
    return None


# --------------------------------------------------------------------------
# receivers
# --------------------------------------------------------------------------
def _safe(fn):
    def wrapper(sender, instance, **kwargs):
        if kwargs.get("raw"):  # loaddata / fixtures
            return
        try:
            fn(sender, instance, **kwargs)
        except Exception:
            logger.exception("automod receiver %s failed", fn.__name__)

    wrapper.__name__ = fn.__name__
    return wrapper


@_safe
def _on_post(sender, instance, **kwargs):
    text = " ".join(filter(None, [getattr(instance, "title", None), getattr(instance, "content", None)]))
    screen_and_flag(target_type="post", target_id=instance.pk, user_id=instance.user_id, text=text, surface="post")


@_safe
def _on_comment(sender, instance, **kwargs):
    if getattr(instance, "is_deleted", False):
        return
    screen_and_flag(
        target_type="comment", target_id=instance.pk, user_id=instance.user_id,
        text=instance.content, surface="comment",
    )


@_safe
def _on_story(sender, instance, **kwargs):
    screen_and_flag(
        target_type="story", target_id=instance.pk, user_id=instance.user_id,
        text=instance.caption, surface="story",
    )


@_safe
def _on_user(sender, instance, **kwargs):
    # User rows are saved on every login (last_login); only look at the bio
    # when it could have changed.
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "bio" not in update_fields:
        return
    if not instance.bio:
        return
    screen_and_flag(target_type="bio", target_id=instance.pk, user_id=instance.pk, text=instance.bio, surface="bio")


@_safe
def _on_message(sender, instance, **kwargs):
    if not getattr(settings, "AUTOMOD_SCREEN_DMS", True):
        return
    if getattr(instance, "is_system_message", False) or getattr(instance, "deleted_for_everyone", False):
        return
    screen_and_flag(
        target_type="message", target_id=instance.pk, user_id=instance.sender_id,
        text=instance.text, surface="dm",
    )


def connect():
    """Called from UserProfileConfig.ready(). String senders resolve lazily,
    so this works regardless of app load order."""
    pairs = (
        ("post.Post", _on_post),
        ("post.PostComment", _on_comment),
        ("post.Story", _on_story),
        (settings.AUTH_USER_MODEL, _on_user),
        ("message.Message", _on_message),
    )
    for sender, handler in pairs:
        post_save.connect(handler, sender=sender, weak=False, dispatch_uid=f"automod_{handler.__name__}")
