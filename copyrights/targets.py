"""
copyrights/targets.py - WHAT can be taken down, and the one engine that hides / restores it.

Why an engine: a post can have a copyright claim, a second claim and a court order at the same time.
Each one used to flip the content on its own, so closing one claim could bring back something another
still held. Now claims / orders only change THEIR OWN record; then refresh(target) recomputes the
visible state from every active record:

    strongest active level wins:  legal_removed  >  copyright_removed  >  copyright_hold
    no active record left      ->  content goes back to the state saved in TargetHold.previous_state

Target types (how "hidden" is done - everything is reversible and nothing is deleted):
  post             Post.moderation_status = the level (feeds / detail / react / comment / save already honour it)
  story            is_deleted + Story.takedown_hidden (the flag also hides it from highlights)
  class_material   file and external_link are blanked (old values kept in TargetHold) - every serializer
                   that reads them shows nothing, no view had to change
  class_recording  ClassSession.recording_url blanked the same way
  test_series      status -> archived (not visible / not purchasable / not attemptable); claims about a
                   single question are filed against its series
When the level is a REMOVAL (not just a hold) the files are also moved to quarantine (media.py) so a
direct S3 / CDN link stops working.
"""

import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

HOLD = "copyright_hold"
REMOVED = "copyright_removed"
LEGAL = "legal_removed"
RANK = {HOLD: 1, REMOVED: 2, LEGAL: 3}
OUR_POST_STATES = tuple(RANK)


class Spec:
    key = ""
    label = ""

    def load(self, target_id):  # content that can be CLAIMED today (None = "doesn't exist")
        return self.load_any(target_id)

    def load_any(self, target_id):  # content even if we are hiding it right now
        raise NotImplementedError

    def owner(self, obj):
        raise NotImplementedError

    def text(self, obj):
        return ""

    def capture(self, obj):
        return {}

    def hide(self, obj, level):
        raise NotImplementedError

    def restore(self, obj, prev):
        raise NotImplementedError

    def media_names(self, obj, hold=None):
        return []

    def primary_media_names(self, obj, hold=None):
        """The files a person actually uploaded (no generated thumbnails / variants): these are the ones
        that get a perceptual fingerprint when removed (fingerprint.py)."""
        return self.media_names(obj, hold)

    def preview(self, obj, hold=None):
        """[(kind, url_or_text)] for the staff preview in Django admin; kind: image|video|file|text."""
        return []


def _first(qs, target_id, **extra):
    """qs.filter(pk=...).first(), or None for a malformed id (bad uuid text etc.)."""
    from django.core.exceptions import ValidationError

    try:
        return qs.filter(pk=target_id, **extra).first()
    except (ValueError, TypeError, ValidationError):
        return None


def _url(f):
    try:
        return f.url if f else ""
    except Exception:
        return ""


class PostSpec(Spec):
    key, label = "post", "Post / reel"

    def load(self, target_id):
        from post.models import Post

        return _first(Post.objects.select_related("user"), target_id, is_deleted=False)

    def load_any(self, target_id):
        from post.models import Post

        return _first(Post.objects.select_related("user"), target_id)

    def owner(self, obj):
        return obj.user

    def text(self, obj):
        return (getattr(obj, "title", "") or getattr(obj, "content", "") or "").strip()

    def capture(self, obj):
        st = obj.moderation_status
        return {"moderation_status": "approved" if st in OUR_POST_STATES else st}

    def hide(self, obj, level):
        from post.models import Post

        Post.objects.filter(pk=obj.pk).update(moderation_status=level)

    def restore(self, obj, prev):
        from post.models import Post

        Post.objects.filter(pk=obj.pk, moderation_status__in=OUR_POST_STATES).update(
            moderation_status=(prev or {}).get("moderation_status") or "approved")

    # Resource posts keep their PDF / DOC / PPT / lecture video in PostAttachment (+ older versions in
    # PostAttachmentVersion), NOT in PostMedia. They must be quarantined, fingerprinted and blocked like any
    # other post file, or a removed PDF could simply be uploaded again.
    @staticmethod
    def _attachment_files(obj):
        """[(file_field, thumb_field_or_None)] of every live attachment and every older version."""
        rows = []
        try:
            for a in obj.attachments.all():
                rows.append((a.file, a.thumbnail))
                for v in a.versions.all():
                    rows.append((v.file, v.thumbnail))
        except Exception:
            logger.exception("copyright: could not list attachments of post %s", getattr(obj, "pk", None))
        return rows

    def media_names(self, obj, hold=None):
        names = []
        for m in obj.media.all():
            for f in (m.file, m.thumbnail, m.thumb_320, m.medium_720):
                if f and f.name:
                    names.append(f.name)
        for f, thumb in self._attachment_files(obj):
            for x in (f, thumb):
                if x and x.name:
                    names.append(x.name)
        return list(dict.fromkeys(names))  # same file twice would be quarantined twice

    def primary_media_names(self, obj, hold=None):
        names = [m.file.name for m in obj.media.all() if m.file and m.file.name]
        names += [f.name for f, _thumb in self._attachment_files(obj) if f and f.name]
        return list(dict.fromkeys(names))

    def preview(self, obj, hold=None):
        out = [("text", self.text(obj))] if self.text(obj) else []
        for m in obj.media.all():
            kind = "image" if m.media_type in ("image", "gif") else "video" if m.media_type == "video" else "file"
            out.append((kind, _url(m.file)))
        try:
            for a in obj.attachments.all():
                out.append(("image" if a.kind == "image" else "video" if a.kind == "video" else "file", _url(a.file)))
        except Exception:
            logger.exception("copyright: attachment preview failed for post %s", getattr(obj, "pk", None))
        return out


class StorySpec(Spec):
    key, label = "story", "Story"

    def load(self, target_id):
        from post.models import HighlightItem, Story

        st = _first(Story.objects.select_related("user"), target_id)
        if st is None:
            return None
        if not st.is_deleted:
            return st
        # a story that ended by itself but lives on in a highlight is still claimable
        if st.deleted_at and st.deleted_at >= st.expires_at and HighlightItem.objects.filter(story=st).exists():
            return st
        return None

    def load_any(self, target_id):
        from post.models import Story

        return _first(Story.objects.select_related("user"), target_id)

    def owner(self, obj):
        return obj.user

    def text(self, obj):
        return (obj.caption or "").strip()

    def capture(self, obj):
        return {"is_deleted": obj.is_deleted, "deleted_at": obj.deleted_at.isoformat() if obj.deleted_at else None}

    def hide(self, obj, level):
        from post.models import Story

        Story.objects.filter(pk=obj.pk).update(
            takedown_hidden=True, is_deleted=True, deleted_at=obj.deleted_at or timezone.now())

    def restore(self, obj, prev):
        from django.utils.dateparse import parse_datetime
        from post.models import Story

        prev = prev or {}
        if not prev.get("is_deleted"):
            if obj.expires_at > timezone.now():           # still live: back on air
                Story.objects.filter(pk=obj.pk).update(takedown_hidden=False, is_deleted=False, deleted_at=None)
            else:                                         # ended while we held it: ended "by itself"
                Story.objects.filter(pk=obj.pk).update(takedown_hidden=False, is_deleted=True, deleted_at=obj.expires_at)
        else:
            Story.objects.filter(pk=obj.pk).update(
                takedown_hidden=False, is_deleted=True,
                deleted_at=parse_datetime(prev["deleted_at"]) if prev.get("deleted_at") else obj.deleted_at)

    def media_names(self, obj, hold=None):
        return [obj.media.name] if obj.media and obj.media.name else []

    def preview(self, obj, hold=None):
        kind = "video" if obj.media_type == "video" else "image"
        out = [("text", self.text(obj))] if self.text(obj) else []
        return out + [(kind, _url(obj.media))]


class ClassMaterialSpec(Spec):
    key, label = "class_material", "Class material"

    def load_any(self, target_id):
        from tuitionclass.models import ClassMaterial

        return _first(ClassMaterial.objects.select_related("uploaded_by", "classroom"), target_id)

    def owner(self, obj):
        return obj.uploaded_by

    def text(self, obj):
        return (obj.title or "").strip()

    def capture(self, obj):
        return {"file": obj.file.name if obj.file else "", "external_link": obj.external_link or ""}

    def hide(self, obj, level):
        from tuitionclass.models import ClassMaterial

        ClassMaterial.objects.filter(pk=obj.pk).update(file="", external_link="")

    def restore(self, obj, prev):
        from tuitionclass.models import ClassMaterial

        prev = prev or {}
        ClassMaterial.objects.filter(pk=obj.pk).update(file=prev.get("file") or "", external_link=prev.get("external_link") or "")

    def media_names(self, obj, hold=None):
        name = (hold.previous_state.get("file") if hold is not None else (obj.file.name if obj.file else "")) or ""
        return [name] if name else []

    def preview(self, obj, hold=None):
        out = [("text", f"{obj.title} - {obj.classroom.title}")]
        name = (hold.previous_state.get("file") if hold is not None else (obj.file.name if obj.file else "")) or ""
        if name:
            from django.core.files.storage import default_storage

            try:
                out.append(("file", default_storage.url(name)))
            except Exception:
                pass
        link = (hold.previous_state.get("external_link") if hold is not None else obj.external_link) or ""
        if link:
            out.append(("text", link))
        return out


class ClassRecordingSpec(Spec):
    key, label = "class_recording", "Live-class recording"

    def load(self, target_id):
        from tuitionclass.models import ClassSession

        return _first(ClassSession.objects.select_related("classroom__teacher"), target_id) if self._has_url(target_id) else None

    def _has_url(self, target_id):
        from tuitionclass.models import ClassSession

        return ClassSession.objects.filter(pk=target_id).exclude(recording_url="").exists()

    def load_any(self, target_id):
        from tuitionclass.models import ClassSession

        return _first(ClassSession.objects.select_related("classroom__teacher"), target_id)

    def owner(self, obj):
        return obj.classroom.teacher

    def text(self, obj):
        return (getattr(obj, "title", "") or obj.classroom.title or "").strip()

    def capture(self, obj):
        return {"recording_url": obj.recording_url or ""}

    def hide(self, obj, level):
        from tuitionclass.models import ClassSession

        ClassSession.objects.filter(pk=obj.pk).update(recording_url="")

    def restore(self, obj, prev):
        from tuitionclass.models import ClassSession

        ClassSession.objects.filter(pk=obj.pk).update(recording_url=(prev or {}).get("recording_url") or "")

    def preview(self, obj, hold=None):
        url = (hold.previous_state.get("recording_url") if hold is not None else obj.recording_url) or ""
        return [("text", self.text(obj))] + ([("video", url)] if url else [])


class TestSeriesSpec(Spec):
    key, label = "test_series", "Test series"

    def load(self, target_id):
        from testseries.models import TestSeries

        return _first(TestSeries.objects.select_related("creator"), target_id, status=TestSeries.Status.PUBLISHED)

    def load_any(self, target_id):
        from testseries.models import TestSeries

        return _first(TestSeries.objects.select_related("creator"), target_id)

    def owner(self, obj):
        return obj.creator

    def text(self, obj):
        return (getattr(obj, "title", "") or "").strip()

    def capture(self, obj):
        return {"status": obj.status}

    def hide(self, obj, level):
        from testseries.models import TestSeries

        TestSeries.objects.filter(pk=obj.pk).update(status=TestSeries.Status.ARCHIVED)

    def restore(self, obj, prev):
        from testseries.models import TestSeries

        TestSeries.objects.filter(pk=obj.pk).update(
            status=(prev or {}).get("status") or TestSeries.Status.PUBLISHED)

    def preview(self, obj, hold=None):
        return [("text", self.text(obj))]


SPECS = {s.key: s for s in (PostSpec(), StorySpec(), ClassMaterialSpec(), ClassRecordingSpec(), TestSeriesSpec())}


def spec_for(target_type):
    return SPECS.get(target_type)


def load_target(target_type, target_id):
    """(obj, owner) of content that can be claimed now, else (None, None)."""
    spec = SPECS.get(target_type)
    if spec is None:
        return None, None
    obj = spec.load(str(target_id))
    return (obj, spec.owner(obj)) if obj is not None else (None, None)


def snapshot_text(target_type, obj):
    return SPECS[target_type].text(obj)[:300]


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------
def active_levels(target_type, target_id):
    from .models import CopyrightTakedown, LegalOrderItem

    target_id = str(target_id)
    levels = []
    for st in CopyrightTakedown.objects.filter(
            target_type=target_type, target_id=target_id, state__in=("held", "removed")).values_list("state", flat=True):
        levels.append(HOLD if st == "held" else REMOVED)
    if LegalOrderItem.objects.filter(target_type=target_type, target_id=target_id, state="removed").exists():
        levels.append(LEGAL)
    return levels


def is_held(target_type, target_id):
    return bool(active_levels(target_type, target_id))


@transaction.atomic
def refresh(target_type, target_id):
    """Recompute the visible state of one piece of content. Idempotent; call after ANY change to a
    CopyrightTakedown or LegalOrderItem of that content."""
    from . import media
    from .models import TargetHold

    spec = SPECS.get(target_type)
    if spec is None:
        return None
    target_id = str(target_id)
    obj = spec.load_any(target_id)
    hold = TargetHold.objects.select_for_update().filter(target_type=target_type, target_id=target_id).first()
    levels = active_levels(target_type, target_id)

    if obj is None:  # content is gone for good
        if hold is not None:
            hold.delete()
        return None

    if levels:
        level = max(levels, key=RANK.get)
        if hold is None:
            hold = TargetHold.objects.create(
                target_type=target_type, target_id=target_id, previous_state=spec.capture(obj))
        spec.hide(obj, level)
        hold.level = level
        hold.save(update_fields=["level", "updated_at"])
        if RANK[level] >= RANK[REMOVED]:
            media.schedule_quarantine(hold.pk)
        elif hold.media_quarantined:  # downgraded to a plain hold: files may come back
            media.release_media(hold)
        return hold

    if hold is not None:
        if hold.media_quarantined:
            media.release_media(hold)
        spec.restore(obj, hold.previous_state)
        hold.delete()
    return None
