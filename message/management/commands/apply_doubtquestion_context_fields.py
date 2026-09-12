"""
message/management/commands/apply_doubtquestion_context_fields.py

Task 16 — schema change for `DoubtQuestion` (make `group`/`conversation`
nullable, add `context_type`/`context_id`, add the supporting index +
CheckConstraint), applied directly via raw SQL instead of a numbered
migration file — so there's no migration-dependency name to fill in by
hand.

⚠️ ASSUMPTIONS, flagged rather than guessed (same "flag the gap"
convention the rest of this codebase uses):
  - Postgres. The project already uses `django.contrib.postgres`
    (`GinIndex`/`SearchVectorField` on `Message`), so this uses Postgres-
    only syntax (`DO $$ ... EXCEPTION ...`, `ADD COLUMN IF NOT EXISTS`).
    It will NOT work as-is on MySQL/SQLite.
  - Table name `message_doubtquestion` and FK columns `group_id` /
    `conversation_id` — Django's default naming for app_label='message',
    model 'DoubtQuestion'. If your app_label or `db_table` Meta override
    differs, fix `TABLE` below.
  - This does NOT update Django's own migration history
    (`django_migrations` table), so `makemigrations` will likely still
    want to generate a migration for these same field changes afterward.
    That's expected — it'll produce a no-op migration (columns already
    exist) that just brings migration state in sync with the DB; run
    `makemigrations --check` or just apply the generated migration
    normally, it won't try to re-create anything that's already there.

Idempotent — every step is guarded, so running this command more than
once is safe.

Usage:
    python manage.py apply_doubtquestion_context_fields
"""
from django.core.management.base import BaseCommand
from django.db import connection


TABLE = "message_doubtquestion"

SQL = """
-- 1. group_id / conversation_id -> nullable
ALTER TABLE {table} ALTER COLUMN group_id DROP NOT NULL;
ALTER TABLE {table} ALTER COLUMN conversation_id DROP NOT NULL;

-- 2. new columns (IF NOT EXISTS makes this safe to re-run)
ALTER TABLE {table} ADD COLUMN IF NOT EXISTS context_type varchar(30) NULL;
ALTER TABLE {table} ADD COLUMN IF NOT EXISTS context_id uuid NULL;

-- 3. lookup index for (context_type, context_id)
CREATE INDEX IF NOT EXISTS message_doub_context_idx
    ON {table} (context_type, context_id);

-- 4. every doubt has EITHER a group OR a full context pointer, never
--    neither. Wrapped in a DO block since Postgres has no
--    "ADD CONSTRAINT IF NOT EXISTS".
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'doubtquestion_has_group_or_context'
    ) THEN
        ALTER TABLE {table}
        ADD CONSTRAINT doubtquestion_has_group_or_context
        CHECK (
            group_id IS NOT NULL
            OR (context_type IS NOT NULL AND context_id IS NOT NULL)
        );
    END IF;
END $$;
""".format(table=TABLE)


class Command(BaseCommand):
    help = (
        "Applies the Task 16 DoubtQuestion schema change (nullable "
        "group/conversation, new context_type/context_id, supporting "
        "index + CheckConstraint) directly via SQL. Postgres only."
    )

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute(SQL)
        self.stdout.write(self.style.SUCCESS(
            f"DoubtQuestion context fields applied on '{TABLE}'."
        ))