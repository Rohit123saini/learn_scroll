# Hand-written for P-F5 (no `makemigrations` was run). Please run `python manage.py makemigrations --check --dry-run`;
# it must report "No changes detected". If it shows a diff, regenerate this file with `makemigrations social_learning`.
import django.db.models.deletion
import django.db.models.expressions
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("message", "0015_devicetoken_push_reliability"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SocialPrefs",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("season_opt_out", models.BooleanField(default=False)),
                ("accepts_partner_requests", models.BooleanField(default=True)),
                ("partner_reminders", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="social_prefs", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="PomodoroTimer",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("phase", models.CharField(choices=[("idle", "Idle"), ("focus", "Focus"), ("short_break", "Short break"), ("long_break", "Long break")], default="idle", max_length=12)),
                ("is_running", models.BooleanField(db_index=True, default=False)),
                ("phase_started_at", models.DateTimeField(blank=True, null=True)),
                ("phase_ends_at", models.DateTimeField(blank=True, null=True)),
                ("paused_remaining_seconds", models.PositiveIntegerField(blank=True, null=True)),
                ("focus_minutes", models.PositiveSmallIntegerField(default=25)),
                ("short_break_minutes", models.PositiveSmallIntegerField(default=5)),
                ("long_break_minutes", models.PositiveSmallIntegerField(default=15)),
                ("cycles_before_long", models.PositiveSmallIntegerField(default=4)),
                ("cycle_index", models.PositiveSmallIntegerField(default=0)),
                ("version", models.PositiveIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("conversation", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="pomodoro_timer", to="message.conversation")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="PomodoroRound",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("idle", "Idle"), ("focus", "Focus"), ("short_break", "Short break"), ("long_break", "Long break")], max_length=12)),
                ("started_at", models.DateTimeField()),
                ("planned_seconds", models.PositiveIntegerField()),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("completed", models.BooleanField(default=False)),
                ("timer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="rounds", to="social_learning.pomodorotimer")),
            ],
        ),
        migrations.AddField(
            model_name="pomodorotimer",
            name="current_round",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="social_learning.pomodororound"),
        ),
        migrations.AddIndex(
            model_name="pomodororound",
            index=models.Index(fields=["timer", "-started_at"], name="sl_round_timer_started_idx"),
        ),
        migrations.CreateModel(
            name="PomodoroCompletion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("minutes", models.PositiveSmallIntegerField()),
                ("completed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("local_date", models.DateField(db_index=True)),
                ("conversation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="message.conversation")),
                ("round", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="completions", to="social_learning.pomodororound")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pomodoro_completions", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="pomodorocompletion",
            constraint=models.UniqueConstraint(fields=("user", "round"), name="sl_pomodoro_once_per_round"),
        ),
        migrations.AddIndex(
            model_name="pomodorocompletion",
            index=models.Index(fields=["user", "local_date"], name="sl_pomo_user_date_idx"),
        ),
        migrations.CreateModel(
            name="AccountabilityPair",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("pair_key", models.CharField(db_index=True, max_length=80)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("active", "Active"), ("declined", "Declined"), ("ended", "Ended")], db_index=True, default="pending", max_length=10)),
                ("daily_goal_minutes", models.PositiveSmallIntegerField(default=30)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("best_streak", models.PositiveIntegerField(default=0)),
                ("ended_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("partner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="partner_requests_received", to=settings.AUTH_USER_MODEL)),
                ("requester", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="partner_requests_sent", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="accountabilitypair",
            constraint=models.UniqueConstraint(condition=models.Q(("status__in", ["pending", "active"])), fields=("pair_key",), name="sl_one_live_pair_per_two_users"),
        ),
        migrations.AddIndex(
            model_name="accountabilitypair",
            index=models.Index(fields=["requester", "status"], name="sl_pair_req_status_idx"),
        ),
        migrations.AddIndex(
            model_name="accountabilitypair",
            index=models.Index(fields=["partner", "status"], name="sl_pair_par_status_idx"),
        ),
        migrations.CreateModel(
            name="ManualStudyLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("date", models.DateField()),
                ("minutes", models.PositiveSmallIntegerField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="manual_study_logs", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="manualstudylog",
            constraint=models.UniqueConstraint(fields=("user", "date"), name="sl_manual_log_per_day"),
        ),
        migrations.CreateModel(
            name="AccountabilityNudge",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("manual", "Manual"), ("auto", "Auto evening reminder")], default="manual", max_length=8)),
                ("template", models.CharField(default="keep_going", max_length=24)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("pair", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="nudges", to="social_learning.accountabilitypair")),
                ("sender", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name="accountabilitynudge",
            index=models.Index(fields=["pair", "kind", "created_at"], name="sl_nudge_pair_kind_idx"),
        ),
        migrations.CreateModel(
            name="AccountabilityMilestone",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("days", models.PositiveSmallIntegerField()),
                ("granted_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("pair", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="milestones", to="social_learning.accountabilitypair")),
            ],
        ),
        migrations.AddConstraint(
            model_name="accountabilitymilestone",
            constraint=models.UniqueConstraint(fields=("pair", "days"), name="sl_pair_milestone_once"),
        ),
        migrations.CreateModel(
            name="StudySeason",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("number", models.PositiveIntegerField(unique=True)),
                ("title", models.CharField(max_length=80)),
                ("starts_at", models.DateTimeField(db_index=True)),
                ("ends_at", models.DateTimeField(db_index=True)),
                ("status", models.CharField(choices=[("upcoming", "Upcoming"), ("active", "Active"), ("closed", "Closed")], db_index=True, default="upcoming", max_length=10)),
                ("config", models.JSONField(blank=True, default=dict)),
                ("finalized_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={"ordering": ["-starts_at"]},
        ),
        migrations.AddConstraint(
            model_name="studyseason",
            constraint=models.CheckConstraint(condition=models.Q(("ends_at__gt", django.db.models.expressions.F("starts_at"))), name="sl_season_ends_after_start"),
        ),
        migrations.CreateModel(
            name="SeasonStanding",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("score", models.PositiveIntegerField(default=0)),
                ("rank", models.PositiveIntegerField()),
                ("minutes", models.PositiveIntegerField(default=0)),
                ("pomodoros", models.PositiveIntegerField(default=0)),
                ("active_days", models.PositiveSmallIntegerField(default=0)),
                ("is_final", models.BooleanField(default=False)),
                ("computed_at", models.DateTimeField(auto_now=True)),
                ("season", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="standings", to="social_learning.studyseason")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="season_standings", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["rank"]},
        ),
        migrations.AddConstraint(
            model_name="seasonstanding",
            constraint=models.UniqueConstraint(fields=("season", "user"), name="sl_one_standing_per_user"),
        ),
        migrations.AddIndex(
            model_name="seasonstanding",
            index=models.Index(fields=["season", "rank"], name="sl_standing_rank_idx"),
        ),
        migrations.CreateModel(
            name="SeasonReward",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tier", models.CharField(max_length=16)),
                ("rank", models.PositiveIntegerField()),
                ("score", models.PositiveIntegerField(default=0)),
                ("badge_code", models.CharField(blank=True, default="", max_length=50)),
                ("freezes_requested", models.PositiveSmallIntegerField(default=0)),
                ("freezes_granted", models.PositiveSmallIntegerField(default=0)),
                ("granted_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("seen_at", models.DateTimeField(blank=True, null=True)),
                ("season", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="rewards", to="social_learning.studyseason")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="season_rewards", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="seasonreward",
            constraint=models.UniqueConstraint(fields=("season", "user"), name="sl_one_reward_per_season_user"),
        ),
        migrations.AddIndex(
            model_name="seasonreward",
            index=models.Index(fields=["user", "-granted_at"], name="sl_reward_user_idx"),
        ),
    ]
