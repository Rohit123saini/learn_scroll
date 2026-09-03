from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    # Last migration visible in the project per the current migrations/
    # folder listing — update this if a newer migration lands before this
    # one is applied.
    dependencies = [
        ("liveclass", "0015_chatmessagereport_notificationpreference_passgift_and_more"),
    ]

    operations = [
        # ---------------------------------------------------------------
        # 1. Create BreakoutRoom (models.py §6B — host splits a live
        #    session into smaller numbered rooms). A row only exists while
        #    a breakout is actively running; ClassSessionViewSet.breakout_close
        #    deletes every row for the session in one shot.
        # ---------------------------------------------------------------
        migrations.CreateModel(
            name="BreakoutRoom",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("room_number", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="breakout_rooms",
                        to="liveclass.classsession",
                    ),
                ),
            ],
            options={
                "ordering": ["room_number"],
            },
        ),
        # ---------------------------------------------------------------
        # 2. Add SessionParticipant.breakout_room (null = main room; set/
        #    cleared by ClassSessionViewSet.breakout_assign; SET_NULL so
        #    deleting the BreakoutRoom clears every participant's
        #    assignment for free).
        # ---------------------------------------------------------------
        migrations.AddField(
            model_name="sessionparticipant",
            name="breakout_room",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="participants",
                to="liveclass.breakoutroom",
            ),
        ),
        # ---------------------------------------------------------------
        # 3. Uniqueness constraint: one room-number per session (matches
        #    Meta.unique_together on the BreakoutRoom model).
        # ---------------------------------------------------------------
        migrations.AlterUniqueTogether(
            name="breakoutroom",
            unique_together={("session", "room_number")},
        ),
    ]