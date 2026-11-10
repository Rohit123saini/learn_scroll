# Parent-teacher meetings (PTM) + rules-based teacher<->parent chat.
# Hand-written to match campus.models (ParentTeacherSettings, PTMEvent, PTMSlot,
# PTMBooking, ParentChatThread, ParentChatMessage); index + constraint names are
# explicit in the models so `makemigrations --check` reports no drift.
#
# NOTE: if your tree already has a later campus migration than 0006, change the
# dependency below to your latest one (all six tables are new).

import datetime
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0006_substitute_teacher"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ParentTeacherSettings",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("chat_enabled", models.BooleanField(default=True)),
                ("chat_start_time", models.TimeField(default=datetime.time(8, 0))),
                ("chat_end_time", models.TimeField(default=datetime.time(20, 0))),
                ("chat_max_unanswered", models.PositiveSmallIntegerField(default=3)),
                ("chat_message_max_chars", models.PositiveSmallIntegerField(default=1000)),
                ("ptm_enabled", models.BooleanField(default=True)),
                ("ptm_booking_cutoff_minutes", models.PositiveIntegerField(default=60)),
                ("ptm_cancel_cutoff_minutes", models.PositiveIntegerField(default=120)),
                ("ptm_max_bookings_per_parent", models.PositiveSmallIntegerField(default=6)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("campus", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="parent_teacher_settings", to="campus.campus")),
            ],
            options={
                "verbose_name_plural": "parent-teacher settings",
            },
        ),
        migrations.CreateModel(
            name="PTMEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=120)),
                ("description", models.CharField(blank=True, max_length=500)),
                ("date", models.DateField()),
                ("start_time", models.TimeField()),
                ("end_time", models.TimeField()),
                ("slot_minutes", models.PositiveSmallIntegerField(default=10)),
                ("mode", models.CharField(choices=[("online", "Online"), ("offline", "Offline")], default="offline", max_length=10)),
                ("venue", models.CharField(blank=True, max_length=200)),
                ("all_sections", models.BooleanField(default=True)),
                ("booking_opens_at", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("published", "Published"), ("closed", "Closed"), ("cancelled", "Cancelled")], db_index=True, default="draft", max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ptm_events", to="campus.campus")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ptm_events_created", to=settings.AUTH_USER_MODEL)),
                ("sections", models.ManyToManyField(blank=True, related_name="ptm_events", to="campus.section")),
            ],
            options={
                "ordering": ["-date", "-created_at"],
                "indexes": [
                    models.Index(fields=["campus", "date"], name="ptm_event_campus_date_idx"),
                    models.Index(fields=["campus", "status"], name="ptm_event_campus_status_idx"),
                ],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("end_time__gt", models.F("start_time"))), name="ptm_event_window_valid"),
                ],
            },
        ),
        migrations.CreateModel(
            name="PTMSlot",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("start_time", models.TimeField()),
                ("end_time", models.TimeField()),
                ("location", models.CharField(blank=True, max_length=200)),
                ("is_blocked", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="slots", to="campus.ptmevent")),
                ("staff", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ptm_slots", to="campus.staffprofile")),
            ],
            options={
                "ordering": ["start_time", "staff_id"],
                "indexes": [
                    models.Index(fields=["event", "staff"], name="ptm_slot_event_staff_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("event", "staff", "start_time"), name="ptm_unique_slot_per_teacher_time"),
                    models.CheckConstraint(condition=models.Q(("end_time__gt", models.F("start_time"))), name="ptm_slot_window_valid"),
                ],
            },
        ),
        migrations.CreateModel(
            name="PTMBooking",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("topic", models.CharField(blank=True, max_length=300)),
                ("status", models.CharField(choices=[("booked", "Booked"), ("cancelled", "Cancelled"), ("completed", "Completed"), ("no_show", "No show")], db_index=True, default="booked", max_length=10)),
                ("cancelled_by_role", models.CharField(blank=True, max_length=10)),
                ("cancel_reason", models.CharField(blank=True, max_length=200)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("summary_for_parent", models.TextField(blank=True)),
                ("private_note", models.TextField(blank=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("reminder_24h_sent_at", models.DateTimeField(blank=True, null=True)),
                ("reminder_1h_sent_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ptm_bookings", to="campus.campus")),
                ("cancelled_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ptm_bookings_cancelled", to=settings.AUTH_USER_MODEL)),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="bookings", to="campus.ptmevent")),
                ("parent", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ptm_bookings_as_parent", to=settings.AUTH_USER_MODEL)),
                ("rescheduled_from", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="rescheduled_to", to="campus.ptmbooking")),
                ("slot", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="bookings", to="campus.ptmslot")),
                ("staff", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ptm_bookings", to="campus.staffprofile")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ptm_bookings", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["event__date", "slot__start_time", "created_at"],
                "indexes": [
                    models.Index(fields=["parent", "status"], name="ptm_booking_parent_idx"),
                    models.Index(fields=["staff", "status"], name="ptm_booking_staff_idx"),
                    models.Index(fields=["event", "status"], name="ptm_booking_event_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(condition=models.Q(("status__in", ["booked", "completed", "no_show"])), fields=("slot",), name="ptm_one_live_booking_per_slot"),
                    models.UniqueConstraint(condition=models.Q(("status__in", ["booked", "completed", "no_show"])), fields=("event", "student", "staff"), name="ptm_one_live_booking_per_student_teacher"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ParentChatThread",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("open", "Open"), ("closed", "Closed")], db_index=True, default="open", max_length=10)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("close_reason", models.CharField(blank=True, max_length=200)),
                ("flagged_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("flag_reason", models.CharField(blank=True, max_length=300)),
                ("parent_last_read_at", models.DateTimeField(blank=True, null=True)),
                ("teacher_last_read_at", models.DateTimeField(blank=True, null=True)),
                ("parent_muted", models.BooleanField(default=False)),
                ("teacher_muted", models.BooleanField(default=False)),
                ("last_message_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_message_preview", models.CharField(blank=True, max_length=120)),
                ("last_sender_role", models.CharField(blank=True, max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="parent_chat_threads", to="campus.campus")),
                ("closed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="parent_chat_threads_closed", to=settings.AUTH_USER_MODEL)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="parent_chat_threads_created", to=settings.AUTH_USER_MODEL)),
                ("flagged_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="parent_chat_threads_flagged", to=settings.AUTH_USER_MODEL)),
                ("parent", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="parent_chat_threads_as_parent", to=settings.AUTH_USER_MODEL)),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="parent_chat_threads_as_student", to=settings.AUTH_USER_MODEL)),
                ("teacher", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="parent_chat_threads", to="campus.staffprofile")),
            ],
            options={
                "ordering": ["-last_message_at", "-created_at"],
                "indexes": [
                    models.Index(fields=["campus", "status"], name="pchat_thread_campus_status_idx"),
                    models.Index(fields=["parent", "-last_message_at"], name="pchat_thread_parent_idx"),
                    models.Index(fields=["teacher", "-last_message_at"], name="pchat_thread_teacher_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("campus", "student", "parent", "teacher"), name="pchat_unique_thread"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ParentChatMessage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("sender_role", models.CharField(choices=[("parent", "Parent"), ("teacher", "Teacher")], max_length=10)),
                ("body", models.TextField()),
                ("after_hours", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("sender", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="parent_chat_messages", to=settings.AUTH_USER_MODEL)),
                ("thread", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="campus.parentchatthread")),
            ],
            options={
                "ordering": ["created_at"],
                "indexes": [
                    models.Index(fields=["thread", "created_at"], name="pchat_msg_thread_idx"),
                ],
            },
        ),
    ]
