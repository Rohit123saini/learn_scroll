from django.db import migrations, models


class Migration(migrations.Migration):
    """Year-end promotion: StudentEnrollment.Status gains `promoted` (choices only — no data change)."""

    dependencies = [
        ("campus", "0016_fee_online_payment"),
    ]

    operations = [
        migrations.AlterField(
            model_name="studentenrollment",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("transferred", "Transferred"),
                    ("graduated", "Graduated"),
                    ("promoted", "Promoted"),
                    ("withdrawn", "Withdrawn"),
                ],
                db_index=True,
                default="active",
                max_length=15,
            ),
        ),
    ]
