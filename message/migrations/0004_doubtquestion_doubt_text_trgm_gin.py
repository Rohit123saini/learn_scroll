# T1.5 (D5) — trigram GIN index for the "similar doubt" suggestion
# (message/doubt_similarity.py).
#
# `TrigramExtension()` is `CREATE EXTENSION IF NOT EXISTS pg_trgm` — already
# applied by 0001_initial, repeated here so this migration is self-contained
# (e.g. a DB where 0001 was faked, or an environment where the extension was
# dropped). It is a no-op when the extension exists, and a no-op on non-
# PostgreSQL backends. NOTE: creating an extension needs a DB role with
# CREATE privilege on the database (or the extension pre-created by a
# superuser: `CREATE EXTENSION pg_trgm;`).
import django.contrib.postgres.indexes
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('message', '0003_doubtquestion_public_page'),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name='doubtquestion',
            index=django.contrib.postgres.indexes.GinIndex(
                fields=['text'],
                name='doubt_text_trgm_gin',
                opclasses=['gin_trgm_ops'],
            ),
        ),
    ]
