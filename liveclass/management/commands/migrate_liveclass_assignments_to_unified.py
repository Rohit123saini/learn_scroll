# liveclass/management/commands/migrate_liveclass_assignments_to_unified.py
"""
Task 12 — one-off DATA migration (no schema/`makemigrations` involved):
copies every existing `liveclass.Assignment`/`AssignmentSubmission` row
into the unified `assignment` app, so historical assignments survive the
cutover to `liveclass/views.py`'s new thin proxy (which only ever reads/
writes through `assignment` from here on).

This deliberately does NOT go through `liveclass.bridge.create_assignment()`
/ `assignment.bridge.create_context_assignment()` — those always
bulk-pre-create fresh `MISSING` rows for the CURRENT active-pass roster,
which is the right behavior for a brand-new assignment but wrong for a
backfill: a backfilled assignment needs each student's REAL historical
`submitted_at`/`file`/`score`/`feedback`/`graded_at`, which that path has
no parameters for at all. So this talks to `assignment.models` directly
— a one-off exception to the "liveclass never imports assignment.models
outside bridge.py" rule (see liveclass/bridge.py's own docstring), same
as `assignment`'s own docstring allows for a deliberate, one-time,
audited exception rather than a silent violation.

IDEMPOTENT: every migrated `Assignment` gets
`data["legacy_liveclass_assignment_id"] = str(old.id)` stamped on it;
re-running the command skips any old assignment that already has a
matching new row (checked via `data__legacy_liveclass_assignment_id`,
a Postgres jsonb lookup — matches the `GinIndex`-on-Postgres assumption
already made elsewhere in liveclass/models.py). Per-submission rows are
additionally protected by `bulk_create(..., ignore_conflicts=True)`
against `unique_submission_per_student`, so a partial prior run (crashed
mid-assignment) can't double-insert submissions even if the parent
Assignment check above were ever bypassed.

ROLLBACK LOG: every successfully migrated assignment appends one JSON
line to `--log-file` (default below) of the form
`{"old_assignment_id": <int>, "new_assignment_id": "<uuid>",
"old_submission_ids": [...], "new_submission_ids": ["<uuid>", ...]}`.
`--rollback <path>` reads that file back and deletes exactly those new
rows (submissions first, then assignments — FK order), so a bad run can
be undone without guessing which new rows came from this command versus
any real usage that happened in between.

USAGE:
    python manage.py migrate_liveclass_assignments_to_unified --dry-run
    python manage.py migrate_liveclass_assignments_to_unified
    python manage.py migrate_liveclass_assignments_to_unified --rollback migration_log.jsonl
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

DEFAULT_LOG_FILE = "liveclass_assignment_migration_log.jsonl"


class Command(BaseCommand):
    help = (
        "Backfills liveclass's local Assignment/AssignmentSubmission rows into the "
        "unified assignment app. Idempotent — safe to re-run. Does not touch "
        "liveclass/models.py's schema."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be migrated without writing anything.",
        )
        parser.add_argument(
            "--log-file", default=DEFAULT_LOG_FILE,
            help=f"Path to append the rollback log to (default: {DEFAULT_LOG_FILE}).",
        )
        parser.add_argument(
            "--rollback", metavar="LOG_FILE",
            help="Undo a previous run using the given rollback-log file, then exit.",
        )

    def handle(self, *args, **options):
        if options["rollback"]:
            return self._rollback(options["rollback"])
        return self._migrate(dry_run=options["dry_run"], log_path=options["log_file"])

    # -----------------------------------------------------------------
    # MIGRATE
    # -----------------------------------------------------------------
    def _migrate(self, *, dry_run: bool, log_path: str):
        # Local imports — liveclass/models.py's old Assignment/
        # AssignmentSubmission and assignment.models are both only ever
        # needed inside this one-off command, never at liveclass import
        # time.
        from assignment.models import Assignment as UnifiedAssignment
        from assignment.models import AssignmentSource, AssignmentSubmission as UnifiedSubmission

        from liveclass.models import Assignment as LegacyAssignment
        from liveclass.models import PassPurchase
        from django.utils import timezone

        migrated, skipped, failed = 0, 0, 0
        log_lines = []

        old_assignments = LegacyAssignment.objects.select_related("classroom", "classroom__teacher").order_by("id")
        total = old_assignments.count()
        self.stdout.write(f"Found {total} legacy liveclass assignment(s).")

        for old in old_assignments.iterator():
            already_migrated = UnifiedAssignment.objects.filter(
                source=AssignmentSource.LIVECLASS,
                data__legacy_liveclass_assignment_id=str(old.id),
            ).exists()
            if already_migrated:
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(f"[dry-run] would migrate assignment id={old.id} '{old.title}'")
                migrated += 1
                continue

            try:
                with transaction.atomic():
                    new_assignment = UnifiedAssignment.objects.create(
                        source=AssignmentSource.LIVECLASS,
                        context_type="classroom",
                        context_id=old.classroom_id,
                        posted_by=old.classroom.teacher,
                        title=old.title,
                        description=old.description,
                        attachment=old.attachment,
                        due_date=old.due_date.date() if old.due_date else None,
                        total_marks=old.max_score,
                        data={
                            "context_type": "classroom",
                            "context_id": str(old.classroom_id),
                            "legacy_liveclass_assignment_id": str(old.id),
                            "legacy_max_score": old.max_score,
                        },
                    )

                    old_submissions = list(old.submissions.select_related("student"))
                    submitted_student_ids = {s.student_id for s in old_submissions}

                    # Roster = whoever actually submitted historically,
                    # UNION whoever currently holds an active pass — so a
                    # student who submitted but has since let their pass
                    # lapse still keeps their real submission, and a
                    # current student with no historical submission still
                    # gets a MISSING placeholder, matching what
                    # bridge.create_assignment() would have produced for
                    # them going forward.
                    active_pass_student_ids = set(
                        PassPurchase.objects.filter(
                            class_pass__classroom_id=old.classroom_id,
                            status=PassPurchase.Status.SUCCESS,
                            is_active=True,
                            expires_at__gt=timezone.now(),
                        ).values_list("student_id", flat=True)
                    )
                    roster_student_ids = submitted_student_ids | active_pass_student_ids

                    new_rows = []
                    old_sub_by_student = {s.student_id: s for s in old_submissions}
                    for student_id in roster_student_ids:
                        old_sub = old_sub_by_student.get(student_id)
                        if old_sub is None:
                            new_rows.append(
                                UnifiedSubmission(
                                    assignment=new_assignment,
                                    student_id=student_id,
                                    status=UnifiedSubmission.SubmissionStatus.MISSING,
                                )
                            )
                            continue
                        is_graded = old_sub.graded_at is not None
                        is_late = old_sub.submitted_at > old.due_date if old.due_date else False
                        new_rows.append(
                            UnifiedSubmission(
                                assignment=new_assignment,
                                student_id=student_id,
                                file=old_sub.file,
                                status=(
                                    UnifiedSubmission.SubmissionStatus.CHECKED
                                    if is_graded
                                    else (
                                        UnifiedSubmission.SubmissionStatus.LATE
                                        if is_late
                                        else UnifiedSubmission.SubmissionStatus.SUBMITTED
                                    )
                                ),
                                # Free-text `grade` (not total_marks_awarded,
                                # which is structured-path-only — a legacy
                                # liveclass assignment is always free-form)
                                # keeps the original score AND its scale
                                # together, since the unified model has no
                                # separate "max_score" slot to preserve it in.
                                grade=(
                                    f"{old_sub.score}/{old.max_score}" if old_sub.score is not None else ""
                                ),
                                feedback=old_sub.feedback,
                                submitted_at=old_sub.submitted_at,
                                checked_at=old_sub.graded_at,
                            )
                        )
                    UnifiedSubmission.objects.bulk_create(new_rows, ignore_conflicts=True)

                new_submission_ids = list(
                    UnifiedSubmission.objects.filter(assignment=new_assignment).values_list("id", flat=True)
                )
                log_lines.append(
                    json.dumps(
                        {
                            "old_assignment_id": old.id,
                            "new_assignment_id": str(new_assignment.id),
                            "old_submission_ids": [s.id for s in old_submissions],
                            "new_submission_ids": [str(i) for i in new_submission_ids],
                        }
                    )
                )
                migrated += 1
            except Exception as exc:
                failed += 1
                self.stderr.write(f"FAILED migrating assignment id={old.id}: {exc}")

        if not dry_run and log_lines:
            with open(log_path, "a") as fh:
                fh.write("\n".join(log_lines) + "\n")

        self.stdout.write(
            self.style.SUCCESS(
                f"{'[dry-run] ' if dry_run else ''}Done. migrated={migrated} "
                f"already_migrated(skipped)={skipped} failed={failed} "
                f"log={'(dry-run, not written)' if dry_run else log_path}"
            )
        )
        if failed:
            raise CommandError(f"{failed} assignment(s) failed to migrate — see stderr above.")

    # -----------------------------------------------------------------
    # ROLLBACK
    # -----------------------------------------------------------------
    def _rollback(self, log_path: str):
        from assignment.models import Assignment as UnifiedAssignment
        from assignment.models import AssignmentSubmission as UnifiedSubmission

        try:
            with open(log_path) as fh:
                lines = [json.loads(line) for line in fh if line.strip()]
        except FileNotFoundError:
            raise CommandError(f"Rollback log not found: {log_path}")

        submission_ids = [sid for entry in lines for sid in entry["new_submission_ids"]]
        assignment_ids = [entry["new_assignment_id"] for entry in lines]

        with transaction.atomic():
            deleted_subs, _ = UnifiedSubmission.objects.filter(id__in=submission_ids).delete()
            deleted_assignments, _ = UnifiedAssignment.objects.filter(id__in=assignment_ids).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Rolled back {len(lines)} logged assignment(s) — deleted "
                f"{deleted_assignments} assignment row(s), {deleted_subs} submission row(s)."
            )
        )