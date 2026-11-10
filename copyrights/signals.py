"""
copyrights/signals.py - hold a NEW upload that is a copy of content we removed earlier:
  1. identical bytes            (BlockedMediaHash, SHA-256)
  2. same picture / video / song / PDF text, even re-encoded, resized or mirrored
                                (MediaFingerprint, copyrights/fingerprint.py)
Hashing / fingerprinting a big file is done after the commit, in Celery (inline when
COPYRIGHT_MEDIA_ASYNC=False). Does nothing while nothing is blocked. A match never removes anything:
it opens an AUTO claim, the content is held, a level-2 reviewer decides.
(The synchronous "refuse the upload" check for exact copies lives in services.precheck_upload.)
"""

import logging

from django.db.models.signals import post_save

logger = logging.getLogger(__name__)


def check_upload(target_type, target_id, name):
    """Hash `name`; if it matches a blocked file, open an automatic claim. Never raises."""
    try:
        from . import fingerprint, media, services
        from .models import BlockedMediaHash

        if not name:
            return None
        have_hashes = BlockedMediaHash.objects.filter(active=True).exists()
        if not have_hashes and not fingerprint.any_active():
            return None
        digest = ""
        if have_hashes:
            digest = media.sha256_of(name)
            if digest and BlockedMediaHash.objects.filter(sha256=digest, active=True).exists():
                return services.open_auto_claim(target_type, str(target_id), digest)
        match = fingerprint.match_storage_file(name)
        if match is not None:
            return services.open_auto_claim(target_type, str(target_id), digest, match=match)
    except Exception:
        logger.exception("copyright upload check failed (%s %s)", target_type, target_id)
    return None


def _queue(target_type, target_id, name):
    from . import fingerprint, media
    from .models import BlockedMediaHash

    if not name or not (BlockedMediaHash.objects.filter(active=True).exists() or fingerprint.any_active()):
        return

    media._run_later(lambda: check_upload(target_type, target_id, name),
                     "copyrights.check_upload", target_type, str(target_id), name)


def _post_media_saved(sender, instance, created, **kw):
    if created and instance.file:
        _queue("post", instance.post_id, instance.file.name)


def _attachment_saved(sender, instance, created, **kw):
    # Resource posts (PDF / DOC / PPT / lecture video). A "replace with a newer version" keeps the same row
    # (created=False), so post/resource_versions.py calls queue_check() itself after the swap.
    if created and instance.file:
        _queue("post", instance.post_id, instance.file.name)


def queue_check(target_type, target_id, name):
    """Public: look at one freshly stored file after the DB commit (same path as the post_save hooks)."""
    try:
        _queue(target_type, target_id, name)
    except Exception:
        logger.exception("copyright queue_check failed (%s %s)", target_type, target_id)


def _story_saved(sender, instance, created, **kw):
    if created and instance.media:
        _queue("story", instance.pk, instance.media.name)


def _material_saved(sender, instance, created, **kw):
    if created and instance.file:
        _queue("class_material", instance.pk, instance.file.name)


def connect():
    from django.apps import apps

    pairs = (("post", "PostMedia", _post_media_saved), ("post", "PostAttachment", _attachment_saved),
             ("post", "Story", _story_saved),
             ("tuitionclass", "ClassMaterial", _material_saved))
    for app_label, model_name, handler in pairs:
        try:
            post_save.connect(handler, sender=apps.get_model(app_label, model_name),
                              dispatch_uid=f"copyrights_{model_name}", weak=False)
        except LookupError:
            logger.warning("copyrights: model %s.%s not found, upload check not connected", app_label, model_name)
