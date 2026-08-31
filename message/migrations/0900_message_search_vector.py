# message/migrations/0900_message_search_vector.py
"""
🔥 NAYA — `Message.search_vector` field, uska GIN index, aur `text` pe
trigram GIN index — teeno `models.py` me already declared hain (field +
`Meta.indexes`), lekin unhe DB me actually banane wali migration missing
thi. Ye migration wahi karti hai, plus:

  - `pg_trgm` extension enable karta hai (trigram similarity/index ke
    liye zaroori — bina iske `gin_trgm_ops` opclass hi nahi milega).
  - Ek Postgres trigger banata hai jo INSERT/UPDATE(text) pe
    `search_vector` ko `to_tsvector('english', text)` se auto-populate
    karta hai — isliye application code (serializer/view) ko kabhi
    manually `search_vector` set nahi karna padta.
  - Existing rows ko backfill karta hai (trigger sirf NAYE insert/update
    pe chalega, purane rows ka search_vector migration se pehle NULL hi
    rahega).

✅ FIXED: `dependencies` neeche pehle placeholder (`XXXX_previous_migration`)
tha jo `makemigrations`/`migrate` dependency-graph error deta tha
(`NodeNotFoundError`). Ab `0012_conversationparticipant_draft_text_and_more`
pe point karta hai — jo is upload ke waqt `message/migrations/` folder ki
sabse latest (highest-numbered, sequentially-named) migration thi. Agar
is file ko banane ke baad koi naya `000X_...` migration add hua ho, ye
dependency phir se check kar lena.
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
    # NOTE (fix — SQLite/other non-Postgres backends): TRIGGER_SQL is
    # Postgres plpgsql syntax ("CREATE OR REPLACE FUNCTION ... LANGUAGE
    # plpgsql", "BEFORE INSERT OR UPDATE OF text ..."). The old bare
    # `migrations.RunSQL(sql=TRIGGER_SQL, ...)` had no vendor guard —
    # unlike TrigramExtension()/GinIndex above (which Django's own
    # contrib.postgres operations already no-op on non-Postgres backends)
    # and unlike backfill_search_vector below (which already checked
    # `schema_editor.connection.vendor`), RunSQL always executes verbatim
    # regardless of backend. On SQLite this raised
    # `sqlite3.OperationalError: near "OR": syntax error` the instant
    # `migrate` reached this step — every local/test run on SQLite would
    # have broken here. Same vendor check as backfill_search_vector,
    # applied consistently to this step too.
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(TRIGGER_SQL)


def drop_search_vector_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(REVERSE_TRIGGER_SQL)


def backfill_search_vector(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return  # sqlite (tests/local) me search_vector column hi meaningful nahi
    Message = apps.get_model('message', 'Message')
    # `all_objects` — soft-deleted rows bhi backfill karo, warna un
    # messages ka search_vector hamesha ke liye NULL reh jaayega agar
    # kabhi restore/undelete logic aaya.
    Message.all_objects.exclude(text__isnull=True).exclude(text='').update(
        search_vector=SearchVector('text', config='english')
    )


def noop_reverse(apps, schema_editor):
    pass  # backfill ko reverse karne ki zaroorat nahi — field khud drop ho jaayega


class Migration(migrations.Migration):

    dependencies = [
        ('message', '0012_conversationparticipant_draft_text_and_more'),
    ]

    operations = [
        # `text` column pe gin_trgm_ops index ke liye pg_trgm chahiye.
        TrigramExtension(),

        migrations.AddField(
            model_name='message',
            name='search_vector',
            field=SearchVectorField(null=True, blank=True, editable=False),
        ),

        migrations.AddIndex(
            model_name='message',
            index=GinIndex(fields=['search_vector'], name='message_search_vector_gin'),
        ),
        migrations.AddIndex(
            model_name='message',
            index=GinIndex(fields=['text'], name='message_text_trgm_gin', opclasses=['gin_trgm_ops']),
        ),

        migrations.RunPython(create_search_vector_trigger, drop_search_vector_trigger),

        migrations.RunPython(backfill_search_vector, noop_reverse),
    ]