# campus/backup.py
"""
CAMPUS BACKUP / RESTORE — DB side. Rules (groups, modes, confirm word, defaults) live in `safety_rules.py`.

WHAT A BACKUP IS
  One gzip-JSON file per backup (`CampusBackup.file`, private media prefix `campus/backups/`), sha256-checked.
  Header + `models` map: `"campus.section": [{"pk": ..., "fields": {...}}, ...]` in Django's own serializer shape.

  group `setup`    academic sessions, departments, classes, sections, subjects, rooms, time slots, staff
                   profiles, class/subject teacher assignments, student enrollments, parent links, timetable,
                   exam terms, fee structures.
  group `records`  attendance, results, fee invoices, fee payments.

  NOT inside: the `Campus` row itself (name / switches stay as they are), user accounts (only their ids are
  referenced — a user row is never deleted by the campus, so ids stay valid), uploaded files (file *names* only),
  notices, doubts, chats, notes.

HOW A RESTORE BEHAVES
  * `missing_only` (default) — re-creates rows that are gone; a row that still exists is NEVER touched.
  * `overwrite`              — additionally puts the old field values back on rows that still exist.
  * Rows added after the backup are never deleted by either mode.
  * Always dry-run first (`dry_run=True` changes nothing and returns the same counts a real run would).
  * A real restore first takes a `pre_restore` backup of the same groups, so a restore can itself be undone.
  * Each row is saved inside its own savepoint after checking that every foreign key it points to exists; a row
    that cannot be restored (target missing, unique clash) is counted + listed in `errors`, it never aborts the rest.
  * Saved with `raw=True` (like `loaddata`): custom `save()` logic does not run, so values come back exactly as
    they were.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import tempfile
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.files import File
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import safety_rules as rules
from .models import Campus, CampusBackup

logger = logging.getLogger(__name__)

CHUNK = 1000
ERROR_SAMPLE_LIMIT = 20
READ_CHUNK = 1024 * 1024


class BackupError(Exception):
    """`code` is a short machine code the app maps to a human message."""

    def __init__(self, code: str, status: int = 400, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


# (group, model label, lookup that leads to the Campus). ORDER = dependency order (parents first).
BACKUP_PLAN = (
    (rules.GROUP_SETUP, "campus.AcademicSession", "campus"),
    (rules.GROUP_SETUP, "campus.Department", "campus"),
    (rules.GROUP_SETUP, "campus.SchoolClass", "campus"),
    (rules.GROUP_SETUP, "campus.Section", "school_class__campus"),
    (rules.GROUP_SETUP, "campus.Subject", "campus"),
    (rules.GROUP_SETUP, "campus.Room", "campus"),
    (rules.GROUP_SETUP, "campus.TimeSlot", "campus"),
    (rules.GROUP_SETUP, "campus.CampusHoliday", "campus"),
    (rules.GROUP_SETUP, "campus.CampusProfile", "campus"),
    (rules.GROUP_SETUP, "campus.StaffProfile", "campus"),
    (rules.GROUP_SETUP, "campus.ClassTeacherassigments", "section__school_class__campus"),
    (rules.GROUP_SETUP, "campus.SubjectTeacherassigments", "section__school_class__campus"),
    (rules.GROUP_SETUP, "campus.StudentEnrollment", "section__school_class__campus"),
    (rules.GROUP_SETUP, "campus.CampusParentLink", "campus"),
    (rules.GROUP_SETUP, "campus.TimetableEntry", "section__school_class__campus"),
    (rules.GROUP_SETUP, "campus.ExamTerm", "session__campus"),
    (rules.GROUP_SETUP, "campus.FeeStructure", "campus"),
    (rules.GROUP_RECORDS, "campus.Attendance", "enrollment__section__school_class__campus"),
    (rules.GROUP_RECORDS, "campus.ResultEntry", "enrollment__section__school_class__campus"),
    (rules.GROUP_RECORDS, "campus.FeeInvoice", "campus"),
    (rules.GROUP_RECORDS, "campus.FeePayment", "invoice__campus"),
)


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
def retention_days() -> int:
    return int(getattr(settings, "CAMPUS_BACKUP_RETENTION_DAYS", rules.DEFAULT_RETENTION_DAYS))


def max_per_campus() -> int:
    return int(getattr(settings, "CAMPUS_BACKUP_MAX_PER_CAMPUS", rules.DEFAULT_MAX_PER_CAMPUS))


def max_rows() -> int:
    return int(getattr(settings, "CAMPUS_BACKUP_MAX_ROWS", rules.DEFAULT_MAX_ROWS))


def max_unpacked_bytes() -> int:
    """Decompression-bomb guard when a backup file is read back (default 400 MB of JSON)."""
    return int(getattr(settings, "CAMPUS_BACKUP_MAX_UNPACKED_MB", 400)) * 1024 * 1024


def _plan_for(groups):
    return [(g, label, path) for (g, label, path) in BACKUP_PLAN if g in groups]


def _queryset(label, path, campus):
    model = apps.get_model(label)
    return model._base_manager.filter(**{path: campus}).order_by("pk")


def _clean_groups(groups):
    wanted = list(groups) if groups else [rules.GROUP_SETUP]
    if any(g not in rules.BACKUP_GROUPS for g in wanted):
        raise BackupError("invalid_group")
    return [g for g in rules.BACKUP_GROUPS if g in wanted]


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------
def count_rows(campus, groups) -> dict:
    return {label.lower(): _queryset(label, path, campus).count() for (_g, label, path) in _plan_for(groups)}


def create_backup(campus, *, groups=None, kind=rules.KIND_MANUAL, user=None, note="") -> CampusBackup:
    """
    Write one backup file + its `CampusBackup` row. Raises `BackupError`. A failure while writing leaves a
    `FAILED` row (so the admin sees that it was tried) and no file.
    """
    groups = _clean_groups(groups)
    if kind not in rules.BACKUP_KINDS:
        raise BackupError("invalid_kind")
    counts = count_rows(campus, groups)
    total = sum(counts.values())
    if total > max_rows():
        raise BackupError("too_many_rows", 413, f"{total} rows (limit {max_rows()})")

    backup = CampusBackup.objects.create(
        campus=campus, created_by=user, kind=kind, status=CampusBackup.Status.FAILED, groups=groups,
        note=(note or "")[:200], row_counts=counts,
    )
    try:
        with tempfile.TemporaryFile() as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6) as gz:
                text = io.TextIOWrapper(gz, encoding="utf-8", write_through=True)
                _write_json(text, campus, groups)
                text.flush()
                text.detach()  # keep `gz` open for the `with` block to close it (writes the gzip trailer)
            size = raw.tell()
            raw.seek(0)
            digest = hashlib.sha256()
            for block in iter(lambda: raw.read(READ_CHUNK), b""):
                digest.update(block)
            raw.seek(0)
            backup.file.save("backup.json.gz", File(raw, name="backup.json.gz"), save=False)
        backup.size_bytes = size
        backup.sha256 = digest.hexdigest()
        backup.status = CampusBackup.Status.READY
        backup.expires_at = timezone.now() + timedelta(days=retention_days()) if retention_days() > 0 else None
        backup.error = ""
        backup.save()
    except Exception as exc:
        logger.exception("campus backup failed (campus=%s)", campus.pk)
        backup.error = f"{type(exc).__name__}: {exc}"[:300]
        backup.status = CampusBackup.Status.FAILED
        backup.save(update_fields=["error", "status"])
        raise BackupError("backup_failed", 500, backup.error)
    return backup


def _write_json(out, campus, groups):
    header = {
        "format": rules.BACKUP_FORMAT,
        "version": rules.BACKUP_VERSION,
        "campus_id": str(campus.pk),
        "campus_name": campus.name,
        "created_at": timezone.now().isoformat(),
        "groups": groups,
    }
    out.write("{")
    out.write('"header":' + json.dumps(header))
    out.write(',"models":{')
    first_model = True
    for (_g, label, path) in _plan_for(groups):
        if not first_model:
            out.write(",")
        first_model = False
        out.write(json.dumps(label.lower()) + ":[")
        first_row = True
        batch = []

        def flush(batch):
            nonlocal first_row
            for obj in serializers.serialize("python", batch):
                if not first_row:
                    out.write(",")
                first_row = False
                out.write(json.dumps({"pk": obj["pk"], "fields": obj["fields"]}, cls=DjangoJSONEncoder))

        for instance in _queryset(label, path, campus).iterator(chunk_size=CHUNK):
            batch.append(instance)
            if len(batch) >= CHUNK:
                flush(batch)
                batch = []
        if batch:
            flush(batch)
        out.write("]")
    out.write("}}")
    out.flush()


# ---------------------------------------------------------------------------
# read back
# ---------------------------------------------------------------------------
def load_backup_file(backup: CampusBackup) -> dict:
    """Open, checksum-verify, size-cap and parse the backup file. Raises `BackupError`."""
    if backup.status != CampusBackup.Status.READY or not backup.file:
        raise BackupError("backup_not_ready", 409)
    try:
        with backup.file.open("rb") as fh:
            raw = fh.read()
    except Exception:
        logger.exception("campus backup file missing (backup=%s)", backup.pk)
        raise BackupError("backup_file_missing", 410)
    if backup.sha256 and hashlib.sha256(raw).hexdigest() != backup.sha256:
        raise BackupError("backup_corrupt", 409, "checksum does not match")
    limit = max_unpacked_bytes()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as gz:
            text = gz.read(limit + 1)
        if len(text) > limit:
            raise BackupError("backup_too_large", 413)
        data = json.loads(text.decode("utf-8"))
    except BackupError:
        raise
    except Exception:
        raise BackupError("backup_corrupt", 409, "cannot be read")
    header = data.get("header") or {}
    if header.get("format") != rules.BACKUP_FORMAT or header.get("version") != rules.BACKUP_VERSION:
        raise BackupError("backup_format_unsupported", 409)
    if header.get("campus_id") != str(backup.campus_id):
        raise BackupError("backup_wrong_campus", 409)
    if not isinstance(data.get("models"), dict):
        raise BackupError("backup_corrupt", 409, "no data")
    return data


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------
class _FkCheck:
    """Does the row a foreign key points to exist? Memoised; rows we insert are added so children can follow."""

    def __init__(self):
        self._seen = {}

    def exists(self, related_model, value) -> bool:
        key = (related_model, str(value))
        hit = self._seen.get(key)
        if hit is None:
            hit = related_model._base_manager.filter(pk=value).exists()
            self._seen[key] = hit
        return hit

    def add(self, model, pk):
        self._seen[(model, str(pk))] = True


def _missing_fk(model, fields, fk_check):
    """Name of the first foreign key in `fields` whose target is gone, else None."""
    for field in model._meta.concrete_fields:
        if not field.is_relation or field.many_to_many:
            continue
        value = fields.get(field.name)
        if value is None:
            continue
        if not fk_check.exists(field.related_model, value):
            return field.name
    return None


def _model_rows(data, label):
    return data["models"].get(label.lower()) or []


def _existing_pks(model, pks) -> set:
    found = set()
    pks = list(pks)
    for i in range(0, len(pks), CHUNK):
        found.update(str(p) for p in model._base_manager.filter(pk__in=pks[i:i + CHUNK]).values_list("pk", flat=True))
    return found


def restore_backup(backup: CampusBackup, *, groups=None, mode=rules.MODE_MISSING_ONLY, confirm="", dry_run=True,
                   user=None) -> dict:
    """
    -> {"dry_run", "mode", "groups", "models": {label: {...counts}}, "totals": {...}, "errors": [...],
        "pre_restore_backup": id | None}
    Raises `BackupError` for anything that stops the restore before it starts.
    """
    data = load_backup_file(backup)
    try:
        wanted, mode = rules.validate_restore_options(groups, mode, backup.groups, confirm, dry_run)
    except ValueError as exc:
        raise BackupError(str(exc))

    pre = None
    if not dry_run:
        # A restore must be undoable: snapshot the CURRENT state of the same groups first. If that fails, stop.
        try:
            pre = create_backup(backup.campus, groups=wanted, kind=rules.KIND_PRE_RESTORE, user=user,
                                note=f"Before restoring backup of {backup.created_at:%Y-%m-%d %H:%M}")
        except BackupError as exc:
            raise BackupError("pre_restore_failed", 500, exc.code)

    report = {"dry_run": dry_run, "mode": mode, "groups": wanted, "models": {}, "errors": [],
              "pre_restore_backup": str(pre.pk) if pre else None}
    fk_check = _FkCheck()

    def run():
        for (_g, label, _path) in _plan_for(wanted):
            report["models"][label.lower()] = _restore_model(
                label, _model_rows(data, label), mode, dry_run, fk_check, report["errors"])

    if dry_run:
        run()
    else:
        with transaction.atomic():
            run()
    totals = {"in_backup": 0, "created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    for stats in report["models"].values():
        totals["in_backup"] += stats["in_backup"]
        totals["created"] += stats["created"]
        totals["updated"] += stats["updated"]
        totals["unchanged"] += stats["unchanged"]
        totals["failed"] += stats["failed"]
    report["totals"] = totals
    return report


def _restore_model(label, rows, mode, dry_run, fk_check, errors) -> dict:
    model = apps.get_model(label)
    stats = {"in_backup": len(rows), "created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    existing = _existing_pks(model, (r["pk"] for r in rows))
    for row in rows:
        pk = row["pk"]
        fields = row["fields"]
        is_existing = str(pk) in existing
        if is_existing and mode == rules.MODE_MISSING_ONLY:
            stats["unchanged"] += 1
            continue
        bad_fk = _missing_fk(model, fields, fk_check)
        if bad_fk:
            _fail(stats, errors, label, pk, f"`{bad_fk}` no longer exists")
            continue
        if dry_run:
            stats["updated" if is_existing else "created"] += 1
            fk_check.add(model, pk)
            continue
        try:
            with transaction.atomic():
                for obj in serializers.deserialize(
                    "python", [{"model": label, "pk": pk, "fields": fields}], ignorenonexistent=True
                ):
                    obj.save()
        except (IntegrityError, ValueError, TypeError, serializers.base.DeserializationError) as exc:
            _fail(stats, errors, label, pk, f"{type(exc).__name__}: {str(exc)[:120]}")
            continue
        fk_check.add(model, pk)
        stats["updated" if is_existing else "created"] += 1
    return stats


def _fail(stats, errors, label, pk, why):
    stats["failed"] += 1
    if len(errors) < ERROR_SAMPLE_LIMIT:
        errors.append({"model": label.lower(), "pk": str(pk), "reason": why})


# ---------------------------------------------------------------------------
# housekeeping
# ---------------------------------------------------------------------------
def delete_backup(backup: CampusBackup) -> None:
    """Remove the file and the row."""
    try:
        if backup.file:
            backup.file.delete(save=False)
    except Exception:  # pragma: no cover - a missing file must not block removing the row
        logger.exception("could not delete backup file (backup=%s)", backup.pk)
    backup.delete()


def prune_backups(campus, now=None) -> int:
    """Drop expired backups and anything beyond the newest `max_per_campus()`. Returns how many were removed."""
    now = now or timezone.now()
    removed = 0
    for backup in CampusBackup.objects.filter(campus=campus, expires_at__isnull=False, expires_at__lt=now):
        delete_backup(backup)
        removed += 1
    keep = max_per_campus()
    if keep > 0:
        extra = CampusBackup.objects.filter(campus=campus).order_by("-created_at")[keep:]
        for backup in list(extra):
            delete_backup(backup)
            removed += 1
    return removed


def campuses_due_for_backup(now=None, every_days=None):
    """Campuses with no READY scheduled backup in the last `every_days` days (default 7)."""
    now = now or timezone.now()
    every_days = every_days or int(getattr(settings, "CAMPUS_BACKUP_SCHEDULE_DAYS", 7))
    cutoff = now - timedelta(days=every_days)
    recent = set(
        CampusBackup.objects.filter(kind=rules.KIND_SCHEDULED, status=CampusBackup.Status.READY, created_at__gte=cutoff)
        .values_list("campus_id", flat=True)
    )
    return [c for c in Campus.objects.all().iterator() if c.pk not in recent]


def row_dict(b: CampusBackup) -> dict:
    return {
        "id": str(b.id),
        "kind": b.kind,
        "status": b.status,
        "groups": b.groups,
        "size_bytes": b.size_bytes,
        "sha256": b.sha256,
        "row_counts": b.row_counts,
        "note": b.note,
        "error": b.error,
        "created_at": b.created_at,
        "expires_at": b.expires_at,
        "created_by": (
            {"id": b.created_by_id, "username": b.created_by.username} if b.created_by_id and b.created_by else None
        ),
    }
