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

from common.moderation import has_contact_info, screen_nsfw, screen_text, severity_for

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
    # P-F15: adult-content words are a separate reason from swearing (same review queue, severity high).
    # A DM is only flagged, never hidden, and can be switched off on its own.
    if not (surface == "dm" and getattr(settings, "DM_NSFW_ACTION", "flag") == "off"):
        nsfw, nsfw_reason = screen_nsfw(text)
        if nsfw:
            return record_flag(
                target_type=target_type, target_id=target_id, user_id=user_id, text=text, reason=nsfw_reason,
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


def comment_text_action():
    action = str(getattr(settings, "COMMENT_TEXT_ACTION", "hold")).lower()
    return action if action in ("hold", "flag", "off") else "hold"


def hold_comment_if_needed(instance, *, created=False, update_fields=None):
    """P-F15: a comment with profanity / adult content is created but HIDDEN (is_hidden) and the author gets a notice with
    an Appeal button, same as a report auto-hold. Only looks at new comments and at content edits. Returns True when held.
    Never raises. The hide is a queryset update (no second signal); the in-memory instance is updated too so the
    response that is built from it already says hidden."""
    from . import moderation_log as mlog
    from .models import ModerationAction

    if comment_text_action() != "hold":
        return False
    if not created and update_fields is not None and "content" not in set(update_fields):
        return False
    if getattr(instance, "is_hidden", False) or getattr(instance, "is_deleted", False):
        return False
    text = (instance.content or "").strip()
    if not text:
        return False
    bad, reason = screen_text(text, "comment")
    if not (bad and reason == "profanity"):
        bad, reason = screen_nsfw(text)
    if not bad:
        return False
    type(instance).objects.filter(pk=instance.pk, is_hidden=False).update(is_hidden=True)
    instance.is_hidden = True
    record_flag(target_type="comment", target_id=instance.pk, user_id=instance.user_id, text=text, reason=reason)
    row = mlog.log_action(
        ModerationAction.Action.AUTO_HOLD, target_type="comment", target_id=instance.pk, owner=instance.user,
        reason=reason, count=0, note=f"Held at posting: {reason}", owner_visible=True, can_appeal=True,
    )
    mlog.tell_owner(
        instance.user, row, "Your comment is hidden for now",
        "It has words we don't allow, so we hid it while we check it. If you think this is a mistake, "
        "you can appeal in Settings > Notices.",
    )
    return True


@_safe
def _on_comment(sender, instance, **kwargs):
    if getattr(instance, "is_deleted", False):
        return
    if comment_text_action() == "off":
        return
    held = hold_comment_if_needed(
        instance, created=bool(kwargs.get("created")), update_fields=kwargs.get("update_fields"))
    if held:
        return  # already flagged + noticed above
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


# P11 - profile text a stranger reads besides the bio (name, label under the name, pronouns, city, link titles, @username).
PROFILE_TEXT_FIELDS = frozenset({
    "bio", "first_name", "last_name", "username", "category_label", "pronouns", "location", "links",
})


def profile_extras_text(user) -> str:
    """The non-bio profile text, one string (what the cheap word-list pass screens)."""
    titles = [str(l.get("title", "")) for l in (user.links or []) if isinstance(l, dict)] if isinstance(user.links, list) else []
    parts = [user.first_name, user.last_name, user.username, user.category_label, user.pronouns, user.location, *titles]
    return " ".join(str(p).strip() for p in parts if p and str(p).strip())


@_safe
def _on_user(sender, instance, **kwargs):
    # User rows are saved on every login (last_login); only look at the profile text
    # when it could have changed.
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and not (PROFILE_TEXT_FIELDS & set(update_fields)):
        return
    if instance.bio:
        screen_and_flag(target_type="bio", target_id=instance.pk, user_id=instance.pk, text=instance.bio, surface="bio")
    # P11: the rest of the visible profile text (flag-only, same review queue; reason shown with the snippet)
    extras = profile_extras_text(instance)
    if extras:
        # cheap word-list pass ONLY (no queued AI call): these fields are re-checked on every login-time User save,
        # so an AI call per save would be a cost leak; the AI pass stays for the free-text bio above.
        flagged, reason = screen_text(extras, "bio")
        if flagged:
            record_flag(target_type="user", target_id=instance.pk, user_id=instance.pk, text=extras, reason=reason)
    # P11: an under-18 account must not publish a phone number / chat-app handle where strangers can read it.
    if getattr(instance, "is_minor", False):
        visible = " ".join(x for x in (instance.bio, extras) if x)
        if has_contact_info(visible):
            record_flag(
                target_type="user", target_id=instance.pk, user_id=instance.pk, text=visible,
                reason="minor_contact_info",
            )


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
