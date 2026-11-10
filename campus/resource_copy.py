# campus/resource_copy.py
"""
"Copy to other sections" for class resources.

A teacher who takes Maths for 10-A, 10-B and 10-C posts a worksheet ONCE and
copies it to the sibling sections instead of uploading it three times.

Rules (all enforced here, never trusted from the client):
  * the caller must be able to manage the SOURCE resource (checked by the
    viewset's permission class) AND each TARGET (section, same subject) —
    `ManageScope.can`, the same rule as posting there directly;
  * a target must be in the same campus; the source's own section is skipped;
  * the same subject is kept; the folder path (root, or root > sub-folder) is
    found by name in the target section or created there;
  * the uploaded file / thumbnail is DUPLICATED in storage, never shared:
    replacing or removing a file later deletes the old object from storage
    (`ClassResourceSerializer.update`), which would break the other sections'
    copies if they pointed at the same file;
  * a copy of something the target folder already holds (same title + kind +
    same file name / link) is skipped, so tapping "copy" twice is harmless;
  * copies are new rows: not pinned, no views, `notified_at` empty (they get
    their own push through the normal task when published).

Every target is its own transaction: one failing section never rolls back the
others, and its reason is reported in `skipped`.
"""
import logging
import os

from django.conf import settings
from django.core.files import File
from django.db import transaction

from .models import ClassResource, ResourceFolder, Section

logger = logging.getLogger(__name__)

MAX_COPY_SECTIONS = getattr(settings, "CAMPUS_RESOURCE_COPY_MAX_SECTIONS", 10)

# machine-readable `skipped[].reason` values
SAME_SECTION = "same_section"
NOT_FOUND = "section_not_found"
OTHER_CAMPUS = "other_campus"
FORBIDDEN = "not_allowed"
ALREADY_THERE = "already_exists"
FOLDER_FULL = "folder_full"
TOO_MANY_FOLDERS = "too_many_folders"
FAILED = "copy_failed"


def _find_or_create_root(section, subject_id, name, created_by, max_folders):
    folder = ResourceFolder.objects.filter(
        section=section, subject_id=subject_id, parent__isnull=True, is_active=True, name__iexact=name
    ).first()
    if folder:
        return folder
    existing = ResourceFolder.objects.filter(
        section=section, subject_id=subject_id, parent__isnull=True, is_active=True
    ).count()
    if existing >= max_folders:
        return None
    return ResourceFolder.objects.create(
        section=section, subject_id=subject_id, parent=None, name=name, created_by=created_by
    )


def _find_or_create_child(parent, name, created_by):
    folder = ResourceFolder.objects.filter(parent=parent, is_active=True, name__iexact=name).first()
    if folder:
        return folder
    return ResourceFolder.objects.create(parent=parent, name=name, created_by=created_by)


def _already_in(folder, source):
    qs = ClassResource.objects.filter(
        folder=folder, is_active=True, kind=source.kind, title__iexact=source.title
    )
    if source.external_url:
        qs = qs.filter(external_url=source.external_url)
    else:
        qs = qs.filter(original_name=source.original_name, file_size=source.file_size)
    return qs.exists()


def _duplicate_file(field_file, target_field):
    """Stream `field_file` into `target_field` (a FieldFile of the new row) as a
    brand-new storage object. No-op for an empty field."""
    if not field_file:
        return
    with field_file.open("rb") as handle:
        target_field.save(os.path.basename(field_file.name), File(handle), save=False)


def _discard_files(copy):
    """A rolled-back copy must not leave its freshly written files in storage."""
    if copy is None:
        return
    for field in (copy.file, copy.thumbnail):
        if field and getattr(field, "_committed", False):
            try:
                field.storage.delete(field.name)
            except Exception:  # pragma: no cover - best effort
                pass


def copy_resource_to_sections(source, section_ids, *, user, scope, max_resources_per_folder, max_folders):
    """Copy `source` into each section id. Returns `(created, skipped)`:
    `created` = list of new `ClassResource` rows, `skipped` = list of
    `{"section": id, "reason": <code>}`. `scope` is the caller's `ManageScope`."""
    created, skipped = [], []
    source_folder = source.folder
    root = source_folder.parent if source_folder.parent_id else source_folder
    child = source_folder if source_folder.parent_id else None

    seen = set()
    for raw_id in section_ids[:MAX_COPY_SECTIONS]:
        key = str(raw_id)
        if key in seen:
            continue
        seen.add(key)
        if key == str(source.section_id):
            skipped.append({"section": key, "reason": SAME_SECTION})
            continue
        try:
            section = Section.objects.select_related("school_class").filter(pk=raw_id).first()
        except Exception:  # malformed uuid
            section = None
        if section is None:
            skipped.append({"section": key, "reason": NOT_FOUND})
            continue
        if section.school_class.campus_id != source.campus_id:
            skipped.append({"section": key, "reason": OTHER_CAMPUS})
            continue
        if not scope.can(source.campus_id, section.pk, source.subject_id):
            skipped.append({"section": key, "reason": FORBIDDEN})
            continue
        copy = None
        try:
            with transaction.atomic():
                target_root = _find_or_create_root(section, source.subject_id, root.name, user, max_folders)
                if target_root is None:
                    skipped.append({"section": key, "reason": TOO_MANY_FOLDERS})
                    continue
                target_folder = _find_or_create_child(target_root, child.name, user) if child else target_root
                if _already_in(target_folder, source):
                    skipped.append({"section": key, "reason": ALREADY_THERE})
                    continue
                if ClassResource.objects.filter(folder=target_folder, is_active=True).count() >= max_resources_per_folder:
                    skipped.append({"section": key, "reason": FOLDER_FULL})
                    continue

                copy = ClassResource(
                    folder=target_folder,
                    posted_by=user,
                    kind=source.kind,
                    title=source.title,
                    description=source.description,
                    external_url=source.external_url,
                    duration_seconds=source.duration_seconds,
                    is_published=source.is_published,
                    is_pinned=False,
                    order=0,
                    # `save()` only fills these for a fresh upload; a stream-copied
                    # file is already "committed", so carry them over explicitly.
                    original_name=source.original_name,
                    file_size=source.file_size,
                )
                _duplicate_file(source.file, copy.file)
                _duplicate_file(source.thumbnail, copy.thumbnail)
                copy.save()
                created.append(copy)
        except Exception:
            logger.exception("copy class resource %s -> section %s failed.", source.pk, key)
            _discard_files(copy)
            skipped.append({"section": key, "reason": FAILED})
    return created, skipped
