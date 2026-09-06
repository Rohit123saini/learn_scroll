# message/migrations/0900_message_search_vector.py
"""
See original docstring — unchanged except for the GIN-index steps below.

🔧 FIX (this pass) — the two `migrations.AddIndex(index=GinIndex(...))`
operations had no Postgres guard, unlike `TrigramExtension()` (which
Django's own `CreateExtension.database_forwards()` already no-ops on
non-Postgres backends) and unlike the trigger/backfill steps below
(which explicitly check `schema_editor.connection.vendor`). Bare
`AddIndex` has no such check, and `GinIndex.create_sql()` always emits
`CREATE INDEX ... USING gin (...)` — syntax SQLite's parser rejects
outright. Since this project runs on SQLite for local dev/tests
(`search_utils.py`'s whole `_is_postgres()` fallback path exists
because of this), `migrate`/`manage.py test` would hit
`sqlite3.OperationalError: near "USING": syntax error` on the first of
these two steps, before the trigger/backfill even run.

Fix: wrap both indexes in `SeparateDatabaseAndState` — `state_operations`
keeps the `AddIndex` calls so the migration graph still matches
`models.py`'s `Meta.indexes` (no `makemigrations` drift), but the actual
`CREATE INDEX` only runs through a vendor-guarded `RunPython`, same
pattern already used for `create_search_vector_trigger` below.
"""
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import TrigramExtension
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import migrations


TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION message_search_vector_trigger() RETURNS trigger AS $$
begin
  new.search_vector := to_tsvector('english', coalesce(new.text, ''));
  return new;
end
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS message_search_vector_update ON message_message;
CREATE TRIGGER message_search_vector_update
BEFORE INSERT OR UPDATE OF text ON message_message
FOR EACH ROW EXECUTE FUNCTION message_search_vector_trigger();
"""

REVERSE_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS message_search_vector_update ON message_message;
DROP FUNCTION IF EXISTS message_search_vector_trigger();
"""


def create_search_vector_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(TRIGGER_SQL)


def drop_search_vector_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(REVERSE_TRIGGER_SQL)


def backfill_search_vector(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    Message = apps.get_model('message', 'Message')
    Message.all_objects.exclude(text__isnull=True).exclude(text='').update(
        search_vector=SearchVector('text', config='english')
    )


def noop_reverse(apps, schema_editor):
    pass


# 🔧 NEW — GIN index creation, guarded exactly like the trigger above.
def create_gin_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        "CREATE INDEX message_search_vector_gin "
        "ON message_message USING GIN (search_vector);"
    )
    schema_editor.execute(
        "CREATE INDEX message_text_trgm_gin "
        "ON message_message USING GIN (text gin_trgm_ops);"
    )


def drop_gin_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute("DROP INDEX IF EXISTS message_search_vector_gin;")
    schema_editor.execute("DROP INDEX IF EXISTS message_text_trgm_gin;")


class Migration(migrations.Migration):

    dependencies = [
        ('message', '0012_conversationparticipant_draft_text_and_more'),
    ]

    operations = [
        TrigramExtension(),

        migrations.AddField(
            model_name='message',
            name='search_vector',
            field=SearchVectorField(null=True, blank=True, editable=False),
        ),

        # 🔧 CHANGED — state-only AddIndex + vendor-guarded RunPython,
        # instead of bare AddIndex(GinIndex(...)) which broke SQLite.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='message',
                    index=GinIndex(fields=['search_vector'], name='message_search_vector_gin'),
                ),
                migrations.AddIndex(
                    model_name='message',
                    index=GinIndex(fields=['text'], name='message_text_trgm_gin', opclasses=['gin_trgm_ops']),
                ),
            ],
            database_operations=[
                migrations.RunPython(create_gin_indexes, drop_gin_indexes),
            ],
        ),

        migrations.RunPython(create_search_vector_trigger, drop_search_vector_trigger),

        migrations.RunPython(backfill_search_vector, noop_reverse),
    ]