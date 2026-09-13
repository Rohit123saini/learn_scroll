# campus/management/commands/migrate_campus_assigmentss_to_unified.py
"""
[Task 11] One-time historical backfill: every existing `campus.assigments`
row (and its `campus.assigmentsSubmission` children) is copied into the
unified `assigments` app (`assigments.models.assigments`,
`source="campus"`, `context_type="section"`) so campus's now-thin-proxy
`assigmentsViewSet`/`assigmentsSubmissionViewSet` (views.py) have
something to actually serve. The OLD `campus.assigments`/
`assigmentsSubmission` rows are left in place, untouched, as read-only
history (see those models' own `[DEPRECATED]` docstrings in models.py) —
this command only ever reads them, never mutates or deletes them.

IDEMPOTENT: safe to run more than once. Each migrated
`assigments.assigments` row is tagged with
`data["migrated_from_campus_assigments_id"] = str(<old assigments.id>)`
at creation time; a re-run looks for that marker before creating a new
row, and reuses the existing one instead of duplicating it. Submissions
are upserted via `update_or_create(assigments=..., student=...)`, which
piggybacks on that model's own real `unique_submission_per_student` DB
constraint for the same idempotency guarantee at the row level — a
second run just re-writes the same rows with (by definition) the same
values, never adds duplicates.

ROSTER FIDELITY: builds each migrated assigments's roster from its OLD
`assigmentsSubmission` rows directly (whoever the old system actually
recorded a submission — or a pre-created MISSING placeholder — for),
NOT from `StudentEnrollment`'s CURRENT active roster. A student who has
since left the section (transferred/graduated) but has real historical
submission data must not lose that data just because they're no longer
in the section's active roster today; re-deriving from current
enrollment would silently drop such rows. `roll_number`/`enrollment_no`
are snapshotted from whatever `StudentEnrollment` row exists for that
(student, section) right now, best-effort — this is genuinely
best-effort, not a real historical snapshot, since the old
`assigmentsSubmission` model never stored its own roll_number/
enrollment_no at submission time to carry forward exactly (flagged, not
silently pretended to be exact).

STATUS/FIELD MAPPING — deliberately literal, not reinterpreted:
  - `status` ("submitted"/"late"/"missing") copied verbatim — those three
    string values are identical between the old 3-state model and the
    unified model's `SubmissionStatus`, so no translation table is
    needed. Never upgraded to "checked"/"partially_checked" even when
    `grade`/`feedback` are non-blank — the old model had no such
    transition, and inventing one during migration would be rewriting
    history, not backfilling it.
  - `file` is reattached by its stored path (`old_sub.file.name`), not
    re-uploaded/copied — both models' `FileField`s share the same
    underlying storage backend, so the existing file at that path is
    reused as-is.
  - `checked_at` is left `None` for every migrated row — the old model
    never recorded a distinct "checked" timestamp (grading there was
    just `grade`/`feedback` being set on the SAME `status`), so there is
    no historical value to backfill.

ROLLBACK LOG: every run (unless `--dry-run`) writes a timestamped JSON
log under `--log-dir` (default `campus_migration_logs/`) listing every
NEW `assigments.assigments` id this specific run created (existing,
already-migrated assigmentss it merely re-touched are NOT listed, so a
later `--rollback` can never delete something a previous run legitimately
created and this run just upserted submissions onto). `--rollback
<log-file>` reads that file back and deletes exactly those assigments
rows (their assigmentsSubmission children cascade-delete with them) —
nothing else.
"""
import json
import logging
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill campus.assigments/assigmentsSubmission into the unified assigments app (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--log-dir",
            default="campus_migration_logs",
            help="Directory to write/read the rollback log JSON file (default: campus_migration_logs/).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be migrated without writing anything.",
        )
        parser.add_argument(
            "--rollback",
            metavar="LOG_FILE",
            default=None,
            help="Delete exactly the assigments.assigments rows a previous run's rollback log created, then exit.",
        )

    def handle(self, *args, **options):
        if options["rollback"]:
            self._rollback(options["rollback"])
            return
        self._migrate(log_dir=options["log_dir"], dry_run=options["dry_run"])

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------
    def _migrate(self, *, log_dir, dry_run):
        from assigments.models import assigments as Unifiedassigments
        from assigments.models import assigmentsSource, assigmentsSubmission as UnifiedassigmentsSubmission

        from campus.models import assigments as Legacyassigments
        from campus.models import StudentEnrollment

        created_assigments_ids = []
        touched_assigments_count = 0
        touched_submission_count = 0
        failed_ids = []

        legacy_assigmentss = Legacyassigments.objects.select_related(
            "section__school_class", "subject", "posted_by__user"
        ).prefetch_related("submissions__student")

        for legacy in legacy_assigmentss:
            new_assigments_id = None
            try:
                with transaction.atomic():
                    marker = str(legacy.id)
                    unified = (
                        Unifiedassigments.objects.filter(
                            source=assigmentsSource.CAMPUS,
                            data__migrated_from_campus_assigments_id=marker,
                        )
                        .first()
                    )
                    is_new = unified is None
                    if dry_run:
                        action = "would create" if is_new else "would reuse (already migrated)"
                        self.stdout.write(f"[dry-run] {action} assigments.assigments for campus.assigments {legacy.id}")
                        continue

                    if unified is None:
                        unified = Unifiedassigments.objects.create(
                            source=assigmentsSource.CAMPUS,
                            context_type="section",
                            context_id=legacy.section_id,
                            posted_by=legacy.posted_by.user if legacy.posted_by_id else None,
                            title=legacy.title,
                            description=legacy.description,
                            attachment=legacy.attachment.name if legacy.attachment else None,
                            due_date=legacy.due_date,
                            data={
                                "context_type": "section",
                                "context_id": str(legacy.section_id) if legacy.section_id else None,
                                "subject_id": str(legacy.subject_id) if legacy.subject_id else None,
                                "migrated_from_campus_assigments_id": marker,
                            },
                        )
                        # Recorded only once we're sure the rest of this
                        # assigments's migration (submissions, below)
                        # also succeeds — appended to the real
                        # `created_assigments_ids` list only after this
                        # `with` block exits without raising, so a
                        # mid-block failure (which rolls this whole
                        # atomic block back, including this create())
                        # never logs an id for a row that no longer
                        # exists in the database.
                        new_assigments_id = str(unified.id)

                    for legacy_sub in legacy.submissions.all():
                        enrollment = StudentEnrollment.objects.filter(
                            student_id=legacy_sub.student_id, section_id=legacy.section_id
                        ).first()
                        UnifiedassigmentsSubmission.objects.update_or_create(
                            assigments=unified,
                            student_id=legacy_sub.student_id,
                            defaults={
                                "roll_number": enrollment.roll_number if enrollment else "",
                                "enrollment_no": enrollment.enrollment_no if enrollment else "",
                                "status": legacy_sub.status,  # identical string values, see module docstring
                                "submitted_at": legacy_sub.submitted_at,
                                "file": legacy_sub.file.name if legacy_sub.file else "",
                                "grade": legacy_sub.grade,
                                "feedback": legacy_sub.feedback,
                            },
                        )
                        touched_submission_count += 1
                    touched_assigments_count += 1
            except Exception:
                failed_ids.append(str(legacy.id))
                logger.exception(
                    "campus.migrate_campus_assigmentss_to_unified failed for campus.assigments id=%s", legacy.id
                )
                continue
            if new_assigments_id is not None:
                created_assigments_ids.append(new_assigments_id)

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"[dry-run] {legacy_assigmentss.count()} campus assigments(s) inspected."))
            return

        log_path = self._write_rollback_log(log_dir, created_assigments_ids)
        self.stdout.write(
            self.style.SUCCESS(
                f"Migrated {touched_assigments_count} assigments(s), {touched_submission_count} submission(s). "
                f"{len(created_assigments_ids)} new assigments.assigments row(s) created — logged to {log_path}."
            )
        )
        if failed_ids:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(failed_ids)} campus.assigments row(s) failed and were skipped (see logs): "
                    f"{', '.join(failed_ids)}"
                )
            )

    def _write_rollback_log(self, log_dir, created_assigments_ids):
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = directory / f"migrate_campus_assigmentss_{timestamp}.json"
        log_path.write_text(
            json.dumps(
                {
                    "created_at": timestamp,
                    "created_assigments_ids": created_assigments_ids,
                },
                indent=2,
            )
        )
        return log_path

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------
    def _rollback(self, log_file):
        from assigments.models import assigments as Unifiedassigments

        path = Path(log_file)
        if not path.exists():
            raise CommandError(f"Rollback log not found: {log_file}")
        payload = json.loads(path.read_text())
        assigments_ids = payload.get("created_assigments_ids", [])
        if not assigments_ids:
            self.stdout.write("Nothing to roll back — log lists no created assigmentss.")
            return
        # Submissions cascade-delete with their parent assigments
        # (on_delete=CASCADE) — only assigments rows need an explicit
        # delete() call here.
        deleted_count, _ = Unifiedassigments.objects.filter(id__in=assigments_ids).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Rolled back {len(assigments_ids)} assigments(s) from {log_file} "
                f"({deleted_count} row(s) actually deleted, including cascaded submissions)."
            )
        )