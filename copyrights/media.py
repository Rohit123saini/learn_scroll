"""
copyrights/media.py - what happens to the FILES behind a takedown.

  evidence snapshot   when a notice arrives a copy of the reported files + their SHA-256 is stored
                      (CopyrightEvidence kind=content_snapshot): deleting the post can't erase the proof.
  quarantine          when content is REMOVED (upheld claim / legal order) the files are moved to
                      `copyright_quarantine/<name>` through Django's storage API (works for local disk
                      and S3 alike). The old public URL - also one saved in a chat or copied from a
                      feed - stops working. Restoring moves them back to the exact same name.
  CDN                 optional: set COPYRIGHT_CLOUDFRONT_DISTRIBUTION_ID (+ AWS credentials) and the old
                      paths are invalidated, otherwise the CDN may keep serving them until its TTL ends.
  blocked hashes      the SHA-256 of removed files goes into BlockedMediaHash; signals.py holds any new
                      upload with identical bytes (exact copies only).
  fingerprints        ALSO a perceptual fingerprint (fingerprint.py: image / video / audio / PDF) goes into
                      MediaFingerprint, so a re-encoded, resized, mirrored or re-exported copy is held too.

Settings: COPYRIGHT_MEDIA_ASYNC (True: heavy work runs in Celery after the DB commit; False: inline),
          COPYRIGHT_QUARANTINE_PREFIX ("copyright_quarantine"), COPYRIGHT_CLOUDFRONT_DISTRIBUTION_ID,
          COPYRIGHT_CDN_PATH_PREFIX ("").
Everything here swallows and logs its own errors: a storage problem must never break a takedown.
"""

import hashlib
import logging
import os
import uuid

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction

logger = logging.getLogger(__name__)


def _prefix():
    return getattr(settings, "COPYRIGHT_QUARANTINE_PREFIX", "copyright_quarantine").strip("/")


def sha256_of(name):
    """SHA-256 of a stored file, or "" if it can't be read."""
    try:
        h = hashlib.sha256()
        with default_storage.open(name, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        logger.warning("sha256 failed for %s", name, exc_info=True)
        return ""


def _copy(src, dst):
    """Copy src -> dst inside the storage. Returns the name actually written (None on failure)."""
    try:
        with default_storage.open(src, "rb") as f:
            saved = default_storage.save(dst, File(f))
        if default_storage.size(saved) != default_storage.size(src):
            default_storage.delete(saved)
            return None
        return saved
    except Exception:
        logger.exception("copy %s -> %s failed", src, dst)
        return None


# --------------------------------------------------------------------------
# evidence snapshot
# --------------------------------------------------------------------------
def snapshot_claim(claim):
    """Copy the reported files into CopyrightEvidence and freeze facts in claim.content_meta."""
    from . import targets
    from .models import CopyrightEvidence

    spec = targets.spec_for(claim.target_type)
    obj = spec.load_any(claim.target_id) if spec else None
    if obj is None:
        return 0
    hold = None
    from .models import TargetHold

    hold = TargetHold.objects.filter(target_type=claim.target_type, target_id=claim.target_id).first()
    files, n = [], 0
    for name in spec.media_names(obj, hold):
        if CopyrightEvidence.objects.filter(claim=claim, kind="content_snapshot", original_name=name[:200]).exists():
            continue
        digest = sha256_of(name)
        try:
            with default_storage.open(name, "rb") as f:
                ev = CopyrightEvidence(claim=claim, kind="content_snapshot", original_name=name[:200],
                                       sha256=digest, size_bytes=default_storage.size(name))
                ev.file.save(os.path.basename(name), File(f), save=False)
                ev.save()
            n += 1
        except Exception:
            logger.exception("evidence snapshot failed for %s", name)
            continue
        files.append({"name": name, "sha256": digest, "size": ev.size_bytes})
    owner = spec.owner(obj)
    meta = dict(claim.content_meta or {})
    meta.update({
        "owner_id": getattr(owner, "id", None), "owner_username": getattr(owner, "username", ""),
        "created_at": (getattr(obj, "created_at", None) or getattr(obj, "uploaded_at", None) or "") and
                      str(getattr(obj, "created_at", None) or getattr(obj, "uploaded_at", None)),
        "files": (meta.get("files") or []) + files,
    })
    claim.content_meta = meta
    claim.save(update_fields=["content_meta", "updated_at"])
    return n


def schedule_snapshot(claim_id):
    _run_later(lambda: _snapshot_now(claim_id), "copyrights.snapshot_claim", claim_id)


def _snapshot_now(claim_id):
    from .models import CopyrightClaim

    claim = CopyrightClaim.objects.filter(pk=claim_id).first()
    return snapshot_claim(claim) if claim else 0


# --------------------------------------------------------------------------
# quarantine / release
# --------------------------------------------------------------------------
def _cdn_invalidate(names):
    dist = getattr(settings, "COPYRIGHT_CLOUDFRONT_DISTRIBUTION_ID", "")
    if not dist or not names:
        return
    try:
        import boto3

        pre = getattr(settings, "COPYRIGHT_CDN_PATH_PREFIX", "").rstrip("/")
        paths = ["/" + (pre + "/" if pre else "") + n.lstrip("/") for n in names]
        boto3.client("cloudfront").create_invalidation(
            DistributionId=dist,
            InvalidationBatch={"Paths": {"Quantity": len(paths), "Items": paths}, "CallerReference": uuid.uuid4().hex},
        )
    except Exception:
        logger.exception("CDN invalidation failed")


def _blocked_source(hold):
    """(claim_id, legal_order_id) responsible for the removal."""
    from .models import CopyrightTakedown, LegalOrderItem

    td = CopyrightTakedown.objects.filter(target_type=hold.target_type, target_id=hold.target_id, state="removed").first()
    if td is not None:
        return td.claim_id, None
    it = LegalOrderItem.objects.filter(target_type=hold.target_type, target_id=hold.target_id, state="removed").first()
    return None, (it.order_id if it else None)


def _register_fingerprints(name, claim_id, order_id, digest):
    """Perceptual fingerprint of a file about to be removed. Never raises, never blocks the takedown."""
    try:
        from . import fingerprint

        return fingerprint.register_removed(name, claim_id=claim_id, legal_order_id=order_id, sha256=digest)
    except Exception:
        logger.exception("fingerprint registration failed for %s", name)
        return []


def _deactivate_fingerprints(manifest_entry):
    try:
        from . import fingerprint

        fingerprint.deactivate(ids=manifest_entry.get("fp_ids") or (), sha256s=(manifest_entry.get("sha256"),))
    except Exception:
        logger.exception("fingerprint deactivation failed")


def quarantine_media(hold_id):
    """Move the files of a removed target to quarantine. Safe to repeat."""
    from . import targets
    from .models import BlockedMediaHash, TargetHold

    hold = TargetHold.objects.filter(pk=hold_id).first()
    if hold is None or targets.RANK.get(hold.level, 0) < targets.RANK[targets.REMOVED]:
        return 0
    spec = targets.spec_for(hold.target_type)
    obj = spec.load_any(hold.target_id) if spec else None
    if obj is None:
        return 0
    manifest = list(hold.media_manifest or [])
    done = {m["name"] for m in manifest}
    claim_id, order_id = _blocked_source(hold)
    moved = []
    primary = set(spec.primary_media_names(obj, hold))
    for name in spec.media_names(obj, hold):
        if not name or name in done or not default_storage.exists(name):
            continue
        digest = sha256_of(name)
        q = _copy(name, f"{_prefix()}/{name}")
        if q is None:
            continue  # keep the original: never lose a file because the copy failed
        # fingerprint while the original still exists (it is deleted just below)
        fp_ids = _register_fingerprints(name, claim_id, order_id, digest) if name in primary else []
        try:
            default_storage.delete(name)
        except Exception:
            logger.exception("could not delete %s after quarantine copy", name)
            default_storage.delete(q)
            continue
        manifest.append({"name": name, "quarantine": q, "sha256": digest, "fp_ids": fp_ids})
        moved.append(name)
        if digest:
            BlockedMediaHash.objects.update_or_create(
                sha256=digest, defaults={"active": True, "claim_id": claim_id, "legal_order_id": order_id})
    hold.media_manifest = manifest
    hold.media_quarantined = bool(manifest)
    hold.save(update_fields=["media_manifest", "media_quarantined", "updated_at"])
    _cdn_invalidate(moved)
    return len(moved)


def release_media(hold):
    """Put quarantined files back under their original name and re-allow their hashes."""
    from .models import BlockedMediaHash

    remaining = []
    for m in list(hold.media_manifest or []):
        name, q = m["name"], m["quarantine"]
        ok = False
        try:
            if default_storage.exists(name):
                ok = True  # something already lives there (re-uploaded): leave it, drop the quarantine copy
            elif default_storage.exists(q):
                saved = _copy(q, name)
                if saved == name:
                    ok = True
                elif saved:
                    default_storage.delete(saved)  # storage renamed it - would break the DB reference
                    logger.error("release_media: %s would be renamed to %s; kept in quarantine", name, saved)
            else:
                ok = True  # quarantine copy is gone (purged): nothing to restore
            if ok and default_storage.exists(q):
                default_storage.delete(q)
        except Exception:
            logger.exception("release failed for %s", name)
            ok = False
        if ok:
            if m.get("sha256"):
                BlockedMediaHash.objects.filter(sha256=m["sha256"]).update(active=False)
            _deactivate_fingerprints(m)
        else:
            remaining.append(m)
    hold.media_manifest = remaining
    hold.media_quarantined = bool(remaining)
    hold.save(update_fields=["media_manifest", "media_quarantined", "updated_at"])
    _cdn_invalidate([m["name"] for m in (hold.media_manifest or [])] or [])
    return not remaining


# --------------------------------------------------------------------------
# running heavy work after commit, in Celery (inline when COPYRIGHT_MEDIA_ASYNC=False)
# --------------------------------------------------------------------------
def _run_later(inline, task_name, *args):
    def go():
        if not getattr(settings, "COPYRIGHT_MEDIA_ASYNC", True):
            inline()
            return
        try:
            from celery import current_app

            current_app.send_task(task_name, args=list(args))
        except Exception:
            logger.exception("could not queue %s - running inline", task_name)
            inline()

    transaction.on_commit(go)


def schedule_quarantine(hold_id):
    _run_later(lambda: quarantine_media(hold_id), "copyrights.quarantine_media", hold_id)
