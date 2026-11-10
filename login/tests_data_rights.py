"""S5 tests (DPDP data rights).   python manage.py test login.tests_data_rights

Written by reading the code only -- NOT run. Celery is never contacted: `build_data_export.delay` is patched and the
ZIP builder is called directly.
"""
import io
import json
import zipfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import data_rights
from .audit import Event
from .models import (
    AccountDeletionRequest,
    AuthAuditLog,
    AuthToken,
    ConsentRecord,
    DataExportRequest,
)

User = get_user_model()

BASE = "/login/auth/data/"
DELETE_URL = "/login/auth/delete-account/"


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="dina", password="pw12345!", email="dina@example.com", first_name="Dina", bio="hello"
        )
        self.api = APIClient()
        self.api.force_authenticate(self.user)


# ───────────────────────────── export ─────────────────────────────

@override_settings(DATA_EXPORT_MIN_INTERVAL_HOURS=24)
class ExportRequestTests(_Base):
    def _post(self, api=None):
        # the real task needs a broker; patch .delay and fire on_commit callbacks so the queueing path runs too
        with mock.patch("login.tasks.build_data_export.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                r = (api or self.api).post(BASE + "export/", {}, format="json")
        return r, delay

    def test_login_required(self):
        self.assertIn(APIClient().post(BASE + "export/", {}, format="json").status_code, (401, 403))
        self.assertIn(APIClient().get(BASE + "overview/").status_code, (401, 403))

    def test_request_queues_a_job_and_returns_202(self):
        r, delay = self._post()
        self.assertEqual(r.status_code, 202, r.content)
        exp = DataExportRequest.objects.get(user=self.user)
        self.assertEqual(exp.status, DataExportRequest.STATUS_QUEUED)
        self.assertEqual(r.json()["export"]["id"], exp.pk)
        self.assertFalse(r.json()["export"]["can_download"])
        delay.assert_called_once_with(exp.pk)
        self.assertTrue(AuthAuditLog.objects.filter(user=self.user, event=Event.DATA_EXPORT_REQUESTED).exists())

    def test_second_request_while_running_is_refused(self):
        self._post()
        r, _ = self._post()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "export_in_progress")

    def test_one_request_per_day(self):
        exp = DataExportRequest.objects.create(user=self.user, status=DataExportRequest.STATUS_READY,
                                               file_name="x", expires_at=timezone.now() + timedelta(days=3))
        r, _ = self._post()
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["code"], "export_too_soon")
        self.assertGreater(r.json()["retry_after"], 0)
        self.assertIn("Retry-After", r)
        # a day later it is fine again
        DataExportRequest.objects.filter(pk=exp.pk).update(requested_at=timezone.now() - timedelta(hours=25))
        r, _ = self._post()
        self.assertEqual(r.status_code, 202, r.content)

    def test_failed_run_does_not_use_up_the_daily_request(self):
        DataExportRequest.objects.create(user=self.user, status=DataExportRequest.STATUS_FAILED)
        r, _ = self._post()
        self.assertEqual(r.status_code, 202, r.content)

    def test_broker_down_still_accepts_the_request(self):
        with mock.patch("login.tasks.build_data_export.delay", side_effect=RuntimeError("no broker")):
            with self.captureOnCommitCallbacks(execute=True):
                r = self.api.post(BASE + "export/", {}, format="json")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(DataExportRequest.objects.get(user=self.user).status, DataExportRequest.STATUS_QUEUED)


class ExportBuildTests(_Base):
    def _build(self):
        exp = DataExportRequest.objects.create(user=self.user)
        data_rights.build_export(exp)
        exp.refresh_from_db()
        return exp

    def _zip(self, exp):
        with default_storage.open(exp.file_name, "rb") as f:
            return zipfile.ZipFile(io.BytesIO(f.read()))

    def tearDown(self):
        for exp in DataExportRequest.objects.exclude(file_name=""):
            try:
                default_storage.delete(exp.file_name)
            except Exception:
                pass

    def test_zip_has_the_person_data_and_a_manifest(self):
        exp = self._build()
        self.assertEqual(exp.status, DataExportRequest.STATUS_READY, exp.error)
        self.assertGreater(exp.size_bytes, 0)
        self.assertTrue(exp.expires_at > timezone.now() + timedelta(days=6))
        z = self._zip(exp)
        names = set(z.namelist())
        for must in ("profile.json", "manifest.json", "README.txt", "consent_history.json", "messages_sent.json"):
            self.assertIn(must, names)
        profile = json.loads(z.read("profile.json"))
        self.assertEqual((profile["username"], profile["email"], profile["bio"]), ("dina", "dina@example.com", "hello"))
        self.assertNotIn("password", profile)
        manifest = json.loads(z.read("manifest.json"))
        self.assertEqual(manifest["skipped_sections"], [], manifest)

    def test_other_peoples_data_is_not_in_the_zip(self):
        other = User.objects.create_user(username="zed", password="x", email="zed@example.com", bio="zed secret")
        exp = self._build()
        blob = b"".join(self._zip(exp).read(n) for n in self._zip(exp).namelist())
        self.assertNotIn(b"zed secret", blob)
        self.assertNotIn(b"zed@example.com", blob)
        self.assertNotEqual(other.pk, self.user.pk)

    def test_one_broken_section_is_skipped_not_fatal(self):
        def boom(user):
            raise RuntimeError("bad table")

        sections = tuple((n, boom if n == "likes.json" else b) for n, b in data_rights.SECTIONS)
        with mock.patch.object(data_rights, "SECTIONS", sections):
            exp = self._build()
        self.assertEqual(exp.status, DataExportRequest.STATUS_READY)
        z = self._zip(exp)
        self.assertNotIn("likes.json", z.namelist())
        self.assertEqual(json.loads(z.read("manifest.json"))["skipped_sections"][0]["file"], "likes.json")

    def test_ready_email_is_sent_once(self):
        mail.outbox.clear()
        exp = self._build()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIsNotNone(exp.notified_at)

    def test_task_builds_only_queued_requests(self):
        from .tasks import build_data_export

        exp = DataExportRequest.objects.create(user=self.user)
        out = build_data_export(exp.pk)
        self.assertEqual(out["status"], DataExportRequest.STATUS_READY)
        self.assertEqual(build_data_export(exp.pk), {"skipped": DataExportRequest.STATUS_READY})
        self.assertEqual(build_data_export(999999), {"skipped": "missing"})


class ExportStudyActivityTests(_Base):
    """S5b -- the ZIP carries the person's study activity (streak, time per day, badges), and only theirs."""

    def setUp(self):
        super().setUp()
        from user_profile.models import Badge, DailyUsage, Streak, UserBadge

        self.other = User.objects.create_user(username="zed", password="x", email="zed@example.com")
        Streak.objects.create(user=self.user, current_streak=4, longest_streak=9, total_active_days=21)
        DailyUsage.objects.create(user=self.user, date=timezone.localdate(), seconds=900)
        DailyUsage.objects.create(user=self.user, date=timezone.localdate() - timedelta(days=1), seconds=1800)
        badge = Badge.objects.create(code="streak_7", title="7-day streak", rule_type="streak_7")
        UserBadge.objects.create(user=self.user, badge=badge)
        # somebody else's activity must never show up
        Streak.objects.create(user=self.other, current_streak=77, longest_streak=88, total_active_days=99)
        DailyUsage.objects.create(user=self.other, date=timezone.localdate(), seconds=424242)

    def tearDown(self):
        for exp in DataExportRequest.objects.exclude(file_name=""):
            try:
                default_storage.delete(exp.file_name)
            except Exception:
                pass

    def _study(self):
        exp = DataExportRequest.objects.create(user=self.user)
        data_rights.build_export(exp)
        exp.refresh_from_db()
        self.assertEqual(exp.status, DataExportRequest.STATUS_READY, exp.error)
        with default_storage.open(exp.file_name, "rb") as f:
            z = zipfile.ZipFile(io.BytesIO(f.read()))
        self.assertIn("study_activity.json", z.namelist())
        manifest = json.loads(z.read("manifest.json"))
        self.assertEqual(manifest["skipped_sections"], [], manifest)
        return json.loads(z.read("study_activity.json")), z

    def test_streak_usage_and_badges_are_in_the_zip(self):
        data, _ = self._study()
        self.assertNotIn("unavailable", data)
        self.assertEqual(
            (data["streak"]["current_streak"], data["streak"]["longest_streak"], data["streak"]["total_active_days"]),
            (4, 9, 21),
        )
        self.assertEqual([r["seconds"] for r in data["daily_usage"]], [900, 1800])  # newest day first
        self.assertEqual(data["daily_usage"][0]["minutes"], 15.0)
        self.assertEqual([b["code"] for b in data["badges"]], ["streak_7"])
        self.assertEqual((data["weekly_recaps"], data["test_attempts"], data["class_attendance"]), ([], [], []))

    def test_other_peoples_activity_is_not_in_the_zip(self):
        data, z = self._study()
        blob = b"".join(z.read(n) for n in z.namelist())
        self.assertNotIn(b"424242", blob)
        self.assertNotEqual(data["streak"]["current_streak"], 77)

    def test_person_without_any_activity_still_gets_the_file(self):
        from user_profile.models import DailyUsage, Streak, UserBadge

        UserBadge.objects.filter(user=self.user).delete()
        DailyUsage.objects.filter(user=self.user).delete()
        Streak.objects.filter(user=self.user).delete()
        data, _ = self._study()
        self.assertIsNone(data["streak"])
        self.assertEqual((data["daily_usage"], data["badges"]), ([], []))

    def test_one_broken_part_is_named_and_the_rest_survive(self):
        import user_profile.models as upm

        with mock.patch.object(upm.Streak.objects, "filter", side_effect=RuntimeError("bad table")):
            data, _ = self._study()
        self.assertEqual(data["unavailable"], [{"part": "streak", "reason": "RuntimeError"}])
        self.assertNotIn("streak", data)
        self.assertEqual(len(data["daily_usage"]), 2)  # the other parts are still there

    def test_readme_and_intro_mention_study_activity(self):
        _, z = self._study()
        self.assertIn("study activity", z.read("README.txt").decode())


class ExportDownloadTests(_Base):
    def setUp(self):
        super().setUp()
        self.exp = DataExportRequest.objects.create(user=self.user)
        data_rights.build_export(self.exp)
        self.exp.refresh_from_db()

    def tearDown(self):
        for exp in DataExportRequest.objects.exclude(file_name=""):
            try:
                default_storage.delete(exp.file_name)
            except Exception:
                pass

    def url(self, pk=None):
        return f"{BASE}export/{pk or self.exp.pk}/download/"

    def test_owner_downloads_a_zip_and_it_is_counted(self):
        r = self.api.get(self.url())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/zip")
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertEqual(r["Cache-Control"], "no-store")
        body = b"".join(r.streaming_content)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(body)))
        self.exp.refresh_from_db()
        self.assertEqual(self.exp.download_count, 1)
        self.assertTrue(AuthAuditLog.objects.filter(user=self.user, event=Event.DATA_EXPORT_DOWNLOADED).exists())

    def test_someone_elses_export_is_404(self):
        other = User.objects.create_user(username="eve", password="x")
        c = APIClient()
        c.force_authenticate(other)
        r = c.get(self.url())
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "export_not_found")

    def test_anonymous_cannot_download(self):
        self.assertIn(APIClient().get(self.url()).status_code, (401, 403))

    def test_expired_export_is_410_and_file_is_deleted(self):
        key = self.exp.file_name
        DataExportRequest.objects.filter(pk=self.exp.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        r = self.api.get(self.url())
        self.assertEqual(r.status_code, 410)
        self.assertEqual(r.json()["code"], "export_expired")
        self.exp.refresh_from_db()
        self.assertEqual(self.exp.status, DataExportRequest.STATUS_EXPIRED)
        self.assertFalse(default_storage.exists(key))

    def test_queued_export_is_409(self):
        q = DataExportRequest.objects.create(user=self.user)
        r = self.api.get(self.url(q.pk))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "export_not_ready")

    def test_overview_shows_download_path_only_when_ready(self):
        body = self.api.get(BASE + "overview/").json()
        self.assertTrue(body["export"]["can_download"])
        self.assertEqual(body["export"]["download_path"], f"/login/auth/data/export/{self.exp.pk}/download/")
        self.assertNotIn("file_name", body["export"])  # the storage key never leaves the server

    def test_sweeper_expires_old_zips_and_fails_stuck_jobs(self):
        DataExportRequest.objects.filter(pk=self.exp.pk).update(expires_at=timezone.now() - timedelta(days=1))
        stuck = DataExportRequest.objects.create(
            user=self.user, status=DataExportRequest.STATUS_PROCESSING, started_at=timezone.now() - timedelta(hours=2)
        )
        out = data_rights.sweep_exports()
        self.assertEqual(out, {"expired": 1, "stuck_failed": 1})
        stuck.refresh_from_db()
        self.assertEqual((stuck.status, stuck.error), (DataExportRequest.STATUS_FAILED, "timeout"))

    def test_sweeper_task_builds_requests_the_broker_never_delivered(self):
        from .tasks import sweep_data_exports

        late = DataExportRequest.objects.create(user=self.user)
        DataExportRequest.objects.filter(pk=late.pk).update(requested_at=timezone.now() - timedelta(minutes=10))
        out = sweep_data_exports()
        self.assertEqual(out["built_late"], 1)
        late.refresh_from_db()
        self.assertEqual(late.status, DataExportRequest.STATUS_READY)


# ───────────────────────────── delete with grace ─────────────────────────────

@override_settings(ACCOUNT_DELETION_GRACE_DAYS=30)
class DeleteWithGraceTests(_Base):
    def _delete(self):
        return self.api.post(DELETE_URL, {"password": "pw12345!", "confirm": "DELETE"}, format="json")

    def test_delete_schedules_instead_of_erasing(self):
        r = self._delete()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body["scheduled"])
        self.assertEqual(body["grace_days"], 30)
        self.assertIn(body["days_left"], (30, 31))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.user.username, "dina")  # nothing erased yet
        self.assertTrue(AuthAuditLog.objects.filter(user=self.user, event=Event.ACCOUNT_DELETION_SCHEDULED).exists())

    def test_confirmation_email_is_sent_once_even_if_tapped_twice(self):
        mail.outbox.clear()
        self._delete()
        self._delete()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(AccountDeletionRequest.objects.filter(user=self.user).count(), 1)

    def test_wrong_password_and_missing_confirm_still_refused(self):
        self.assertEqual(self.api.post(DELETE_URL, {"password": "nope", "confirm": "DELETE"}, format="json").status_code, 403)
        self.assertEqual(self.api.post(DELETE_URL, {"password": "pw12345!"}, format="json").status_code, 400)
        self.assertFalse(AccountDeletionRequest.objects.filter(user=self.user).exists())

    def test_withdrawal_in_flight_blocks_scheduling(self):
        from user_profile.models import CoinWithdrawalRequest

        self.user.coin = 500
        self.user.save()
        CoinWithdrawalRequest.objects.create(
            user=self.user, coins=500, amount_inr="500.00", status=CoinWithdrawalRequest.Status.PENDING
        )
        self.assertEqual(self._delete().status_code, 409)
        self.assertFalse(AccountDeletionRequest.objects.filter(user=self.user).exists())

    def test_cancel_keeps_the_account_and_a_new_request_can_follow(self):
        self._delete()
        r = self.api.post(BASE + "deletion/cancel/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()["deletion"]["pending"])
        self.assertEqual(AccountDeletionRequest.objects.get(user=self.user).status, AccountDeletionRequest.STATUS_CANCELLED)
        self.assertTrue(AuthAuditLog.objects.filter(user=self.user, event=Event.ACCOUNT_DELETION_CANCELLED).exists())
        self.assertEqual(self._delete().status_code, 200)  # unique-pending rule allows a fresh one after a cancel
        self.assertEqual(AccountDeletionRequest.objects.filter(user=self.user).count(), 2)

    def test_cancel_with_nothing_scheduled_is_404(self):
        r = self.api.post(BASE + "deletion/cancel/", {}, format="json")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "deletion_not_scheduled")

    def test_status_and_preflight_expose_the_erase_text(self):
        pre = self.api.get(DELETE_URL).json()
        self.assertEqual(pre["grace_days"], 30)
        self.assertTrue(pre["erasure"]["erased"] and pre["erasure"]["kept"])
        self._delete()
        st = self.api.get(BASE + "deletion/").json()
        self.assertTrue(st["deletion"]["pending"])
        self.assertEqual(st["erasure"], data_rights.erasure_summary())

    def test_account_keeps_working_during_grace(self):
        self._delete()
        self.assertEqual(self.api.get(BASE + "overview/").status_code, 200)

    def test_sweeper_erases_only_after_grace_ends(self):
        self._delete()
        AuthToken.objects.create(user=self.user, jti="j1", last_used_at=timezone.now())
        out = data_rights.run_due_deletions()
        self.assertEqual(out["deleted"], 0)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

        AccountDeletionRequest.objects.filter(user=self.user).update(scheduled_for=timezone.now() - timedelta(minutes=1))
        out = data_rights.run_due_deletions()
        self.assertEqual(out["deleted"], 1, out)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.username, f"deleted_{self.user.pk}")
        self.assertEqual(AuthToken.objects.filter(user_id=self.user.pk).count(), 0)
        self.assertEqual(AccountDeletionRequest.objects.get(user=self.user).status, AccountDeletionRequest.STATUS_COMPLETED)
        self.assertTrue(AuthAuditLog.objects.filter(user=self.user, event=Event.ACCOUNT_DELETED, reason="grace_ended").exists())

    def test_sweeper_waits_while_a_withdrawal_is_in_flight(self):
        from user_profile.models import CoinWithdrawalRequest

        self._delete()
        CoinWithdrawalRequest.objects.create(
            user=self.user, coins=500, amount_inr="500.00", status=CoinWithdrawalRequest.Status.PENDING
        )
        AccountDeletionRequest.objects.filter(user=self.user).update(scheduled_for=timezone.now() - timedelta(minutes=1))
        out = data_rights.run_due_deletions()
        self.assertEqual((out["deleted"], out["postponed"]), (0, 1))
        req = AccountDeletionRequest.objects.get(user=self.user)
        self.assertEqual(req.status, AccountDeletionRequest.STATUS_PENDING)
        self.assertGreater(req.scheduled_for, timezone.now())

    @override_settings(ACCOUNT_DELETION_REMINDER_DAYS=3)
    def test_reminder_goes_out_once(self):
        self._delete()
        AccountDeletionRequest.objects.filter(user=self.user).update(scheduled_for=timezone.now() + timedelta(days=2))
        mail.outbox.clear()
        self.assertEqual(data_rights.run_due_deletions()["reminded"], 1)
        self.assertEqual(data_rights.run_due_deletions()["reminded"], 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_erasing_the_account_deletes_its_export_files(self):
        exp = DataExportRequest.objects.create(user=self.user)
        data_rights.build_export(exp)
        exp.refresh_from_db()
        key = exp.file_name
        self._delete()
        AccountDeletionRequest.objects.filter(user=self.user).update(scheduled_for=timezone.now() - timedelta(minutes=1))
        data_rights.run_due_deletions()
        self.assertFalse(default_storage.exists(key))
        exp.refresh_from_db()
        self.assertEqual(exp.status, DataExportRequest.STATUS_EXPIRED)

    @override_settings(ACCOUNT_DELETION_GRACE_DAYS=0)
    def test_grace_zero_is_the_old_immediate_erase(self):
        r = self._delete()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["scheduled"])
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(AccountDeletionRequest.objects.filter(user=self.user).exists())

    def test_erasing_the_account_also_erases_study_activity_and_settings(self):
        from core.models import NotificationPreference
        from user_profile.models import (
            Badge, DailyUsage, PrivacySettings, Streak, UserBadge, UserPreference, WeeklyRecap,
        )

        today = timezone.localdate()
        Streak.objects.create(user=self.user, current_streak=3)
        DailyUsage.objects.create(user=self.user, date=today, seconds=60)
        WeeklyRecap.objects.create(user=self.user, week_start=today - timedelta(days=7), week_end=today - timedelta(days=1))
        UserBadge.objects.create(user=self.user, badge=Badge.objects.create(code="streak_7", title="t", rule_type="streak_7"))
        UserPreference.objects.get_or_create(user=self.user)
        PrivacySettings.objects.get_or_create(user=self.user)
        NotificationPreference.objects.get_or_create(user=self.user)

        eve = User.objects.create_user(username="eve", password="x")
        Streak.objects.create(user=eve, current_streak=5)  # someone else's row stays

        self._delete()
        AccountDeletionRequest.objects.filter(user=self.user).update(scheduled_for=timezone.now() - timedelta(minutes=1))
        self.assertEqual(data_rights.run_due_deletions()["deleted"], 1)

        for model in (Streak, DailyUsage, WeeklyRecap, UserBadge, UserPreference, PrivacySettings, NotificationPreference):
            self.assertFalse(model.objects.filter(user=self.user).exists(), model.__name__)
        self.assertTrue(Streak.objects.filter(user=eve).exists())

    def test_erase_text_names_study_activity_and_what_stays(self):
        text = " ".join(data_rights.erasure_summary()["erased"])
        self.assertIn("study activity", text.lower())
        kept = " ".join(data_rights.erasure_summary()["kept"]).lower()
        self.assertIn("messages you sent", kept)
        self.assertIn("certificates", kept)

    def test_deletion_does_not_leak_between_users(self):
        other = User.objects.create_user(username="eve", password="x")
        self._delete()
        c = APIClient()
        c.force_authenticate(other)
        self.assertFalse(c.get(BASE + "deletion/").json()["deletion"]["pending"])
        self.assertEqual(c.post(BASE + "deletion/cancel/", {}, format="json").status_code, 404)


# ───────────────────────────── consent ─────────────────────────────

class ConsentTests(_Base):
    def _set(self, purpose, granted):
        return self.api.post(BASE + "consents/", {"purpose": purpose, "granted": granted}, format="json")

    def test_never_asked_is_null(self):
        rows = {c["purpose"]: c for c in self.api.get(BASE + "consents/").json()["consents"]}
        self.assertEqual(set(rows), {"marketing", "analytics", "personalisation"})
        self.assertTrue(all(r["granted"] is None for r in rows.values()))

    def test_grant_then_withdraw_is_an_append_only_history(self):
        self.assertEqual(self._set("analytics", True).status_code, 200)
        r = self._set("analytics", False)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["changed"])
        self.assertEqual(ConsentRecord.objects.filter(user=self.user, purpose="analytics").count(), 2)
        cur = {c["purpose"]: c["granted"] for c in r.json()["consents"]}
        self.assertIs(cur["analytics"], False)
        hist = self.api.get(BASE + "consents/").json()["history"]
        self.assertEqual([h["granted"] for h in hist if h["purpose"] == "analytics"], [False, True])
        self.assertTrue(AuthAuditLog.objects.filter(user=self.user, event=Event.CONSENT_CHANGED).exists())

    def test_same_answer_twice_writes_nothing(self):
        self._set("personalisation", True)
        r = self._set("personalisation", True)
        self.assertFalse(r.json()["changed"])
        self.assertEqual(ConsentRecord.objects.filter(user=self.user).count(), 1)

    def test_rows_cannot_be_edited(self):
        rec = data_rights.record_consent(self.user, "analytics", True)
        rec.granted = False
        with self.assertRaises(ValueError):
            rec.save()

    def test_bad_input_is_400(self):
        r = self._set("sell_my_soul", True)
        self.assertEqual((r.status_code, r.json()["code"]), (400, "consent_purpose_invalid"))
        r = self.api.post(BASE + "consents/", {"purpose": "marketing", "granted": "maybe"}, format="json")
        self.assertEqual((r.status_code, r.json()["code"]), (400, "consent_value_invalid"))

    def test_string_booleans_are_accepted(self):
        self.assertEqual(self._set("marketing", "false").status_code, 200)
        self.assertIs(
            {c["purpose"]: c["granted"] for c in data_rights.current_consents(self.user)}["marketing"], False
        )

    def test_withdrawing_marketing_switches_the_channels_off_for_real(self):
        from core.models import NotificationPreference

        pref, _ = NotificationPreference.objects.get_or_create(user=self.user)
        NotificationPreference.objects.filter(pk=pref.pk).update(
            sms_enabled=True, whatsapp_enabled=True, content_digest_frequency="daily"
        )
        self._set("marketing", True)
        pref.refresh_from_db()
        self.assertTrue(pref.sms_enabled)  # granting does not touch anything
        self._set("marketing", False)
        pref.refresh_from_db()
        self.assertEqual((pref.sms_enabled, pref.whatsapp_enabled, pref.content_digest_frequency), (False, False, "off"))

    def test_withdrawing_analytics_leaves_marketing_channels_alone(self):
        from core.models import NotificationPreference

        pref, _ = NotificationPreference.objects.get_or_create(user=self.user)
        NotificationPreference.objects.filter(pk=pref.pk).update(sms_enabled=True)
        self._set("analytics", False)
        pref.refresh_from_db()
        self.assertTrue(pref.sms_enabled)

    def test_consent_history_is_per_user(self):
        other = User.objects.create_user(username="eve", password="x")
        data_rights.record_consent(other, "marketing", True)
        self.assertEqual(self.api.get(BASE + "consents/").json()["history"], [])
