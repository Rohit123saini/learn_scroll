# SOFT DELETE, step 2 of 2 — unique constraints only apply to ALIVE rows, so a name / date / result
# can be re-created after the old one went to the trash.
#
# Safe ordering: the NEW partial unique index is created FIRST, the old one dropped AFTER, so there
# is never a moment without uniqueness protection. Both are reversible (reverse re-creates the old
# full index and drops the new one — which fails loudly, correctly, if duplicates were created
# while soft-deleted rows existed; purge or rename those first).
#
# `unique_invoice_no_per_campus` is deliberately NOT changed: an invoice number is never re-used,
# even after its invoice is deleted.
#
# On a very large live PostgreSQL table build these concurrently instead (AddIndexConcurrently is not
# available for constraints; create the partial unique index with CREATE UNIQUE INDEX CONCURRENTLY by
# hand, then fake this migration). At school scale the plain version below locks for seconds.
from django.db import migrations, models
from django.db.models import Q

ALIVE = Q(is_deleted=False)

# (model, fields, old name, new name)
CONSTRAINTS = [
    ("section", ["school_class", "name"], "unique_section_per_class", "unique_section_per_class_alive"),
    ("staffprofile", ["campus", "user"], "unique_staff_profile_per_campus", "unique_staff_profile_per_campus_alive"),
    ("subjectteacherassigments", ["section", "subject", "staff"], "unique_subject_teacher_assigments", "unique_subject_teacher_assigments_alive"),
    ("resultentry", ["enrollment", "subject", "exam_term"], "unique_result_per_exam", "unique_result_per_exam_alive"),
    ("feeinvoice", ["enrollment", "fee_structure"], "unique_invoice_per_structure", "unique_invoice_per_structure_alive"),
    ("campusholiday", ["campus", "date"], "campus_holiday_unique_date", "campus_holiday_unique_date_alive"),
]


def _ops():
    add, remove = [], []
    for model, fields, old, new in CONSTRAINTS:
        add.append(migrations.AddConstraint(
            model_name=model,
            constraint=models.UniqueConstraint(fields=fields, condition=ALIVE, name=new),
        ))
        remove.append(migrations.RemoveConstraint(model_name=model, name=old))
    return add + remove


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0025_soft_delete_columns"),
    ]

    operations = _ops()
