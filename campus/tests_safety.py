# campus/tests_safety.py
"""
Safety API tests: audit log filter + search, backup / restore, data delete request, consent log.

Run: `python manage.py test campus.tests_safety`
(The pure rules have their own DB-free file: `campus/tests_safety_rules.py`.)

Written against the code only — NOT RUN when written (no Django in that environment).
"""
import gzip
import json
import shutil
import tempfile
from datetime import datetime, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from . import backup as backup_svc
from . import consent as consent_svc
from . import data_deletion as deletion_svc
from . import safety_rules as rules
from . import tasks as campus_tasks
from .audit import log_action
from .models import (
    Campus,
    CampusAuditLog,
    CampusBackup,
    CampusConsentRecord,
    CampusDataDeletionRequest,
    CampusDoubt,
    CampusParentLink,
    DigitalIDCard,
    Section,
    StaffProfile,
    StudentNote,
)
from .tests_t4_efg import T4Base

User = get_user_model()


def rows(resp):
    data = resp.data
    return data.get("results", data) if isinstance(data, dict) else data


class SafetyBase(T4Base):
    """T4Base gives: campus, admin (campus ADMIN), teacher(s), students sa1/sa2/sb, parent (linked to sa1)."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self._media = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._media)
        self._override.enable()
        # bell rows / pushes are not what these tests are about
        self._notify = mock.patch("campus.bridge.notify")
        self.notify = self._notify.start()
        self.principal = User.objects.create_user(username="principal", password="pass12345")
        StaffProfile.objects.create(campus=self.campus, user=self.principal, role=StaffProfile.Role.PRINCIPAL_HOD)
        self.outsider = User.objects.create_user(username="outsider", password="pass12345")

    def tearDown(self):
        self._notify.stop()
        self._override.disable()
        shutil.rmtree(self._media, ignore_errors=True)
        super().tearDown()

    # ---- url helpers
    def u(self, section, *tail):
        """`u("backups")`, `u("backups", id, "download")`, `u("deletion-requests", id, "approve")` ..."""
        base = reverse(f"campus-safety-{section}", args=[str(self.campus.id)])
        return base + "".join(f"{part}/" for part in tail)

    def as_(self, user):
        self.client.force_authenticate(user)
        return self.client

    def audit_actions(self):
        return set(CampusAuditLog.objects.filter(campus=self.campus).values_list("action", flat=True))


# ===========================================================================
# 1. AUDIT LOG — filter + search + facets + export
# ===========================================================================
class AuditLogTests(SafetyBase):
    def setUp(self):
        super().setUp()
        CampusAuditLog.objects.filter(campus=self.campus).delete()   # start from a known log
        log_action(self.campus.id, self.admin, "section.create", target_type="Section", target_id="s1",
                   summary="Created section A of Class 10")
        log_action(self.campus.id, self.admin, "fee.waive", target_type="FeeInvoice", target_id="inv9",
                   summary="Waived fine for Aarav")
        log_action(self.campus.id, self.principal, "fee_waive", summary="old style action name")
        log_action(self.campus.id, self.principal, "enrollment.transfer", target_type="StudentEnrollment",
                   target_id="e7", summary="Moved student to Class 9")
        log_action(self.campus.id, self.admin, "attendance.clear", summary="=HYPERLINK(\"http://x\")")
        other = Campus.objects.create(name="Other", created_by=self.admin)
        log_action(other.id, self.admin, "section.create", summary="belongs to another campus")

    def url(self, suffix=""):
        return reverse("campus-audit-log", args=[str(self.campus.id)]) + suffix

    def get(self, **params):
        return self.as_(self.admin).get(self.url(), params)

    def test_lists_only_this_campus_newest_first(self):
        resp = self.get()
        self.assertEqual(resp.status_code, 200)
        got = rows(resp)
        self.assertEqual(len(got), 5)
        self.assertNotIn("belongs to another campus", [r["summary"] for r in got])
        self.assertEqual(got[0]["action"], "attendance.clear")

    def test_order_asc(self):
        got = rows(self.get(order="asc"))
        self.assertEqual(got[0]["action"], "section.create")

    def test_bad_order_is_400(self):
        self.assertEqual(self.get(order="sideways").status_code, 400)

    def test_search_matches_summary_actor_and_target(self):
        self.assertEqual({r["action"] for r in rows(self.get(q="Aarav"))}, {"fee.waive"})
        self.assertEqual({r["action"] for r in rows(self.get(q="principal"))}, {"fee_waive", "enrollment.transfer"})
        self.assertEqual({r["action"] for r in rows(self.get(q="inv9"))}, {"fee.waive"})

    def test_search_is_case_insensitive(self):
        self.assertEqual({r["action"] for r in rows(self.get(q="aARAV"))}, {"fee.waive"})

    def test_action_prefix_filter(self):
        self.assertEqual({r["action"] for r in rows(self.get(action="fee"))}, {"fee.waive", "fee_waive"})
        self.assertEqual({r["action"] for r in rows(self.get(action="section."))}, {"section.create"})
        self.assertEqual(
            {r["action"] for r in rows(self.get(action="section.create,enrollment."))},
            {"section.create", "enrollment.transfer"})

    def test_category_filter_is_the_part_before_the_dot(self):
        self.assertEqual({r["action"] for r in rows(self.get(category="fee"))}, {"fee.waive"})
        # an action with no dot is its own category
        self.assertEqual({r["action"] for r in rows(self.get(category="fee_waive"))}, {"fee_waive"})

    def test_actor_filter(self):
        got = rows(self.get(actor=self.principal.pk))
        self.assertEqual({r["actor"]["username"] for r in got}, {"principal"})
        self.assertEqual(len(got), 2)

    def test_bad_actor_is_400(self):
        self.assertEqual(self.get(actor="not-a-number").status_code, 400)

    def test_target_filters(self):
        self.assertEqual(len(rows(self.get(target_type="Section"))), 1)
        self.assertEqual(len(rows(self.get(target_type="FeeInvoice", target_id="inv9"))), 1)
        self.assertEqual(len(rows(self.get(target_type="FeeInvoice", target_id="nope"))), 0)

    def test_until_date_includes_the_whole_last_day(self):
        """The old code compared `created_at <= '2026-10-20'` (= midnight) and dropped that whole day."""
        row = CampusAuditLog.objects.get(action="enrollment.transfer", campus=self.campus)
        stamp = timezone.make_aware(datetime(2026, 3, 5, 18, 45))
        CampusAuditLog.objects.filter(pk=row.pk).update(created_at=stamp)
        got = {r["action"] for r in rows(self.get(since="2026-03-05", until="2026-03-05"))}
        self.assertEqual(got, {"enrollment.transfer"})
        self.assertEqual(rows(self.get(until="2026-03-04")), [])

    def test_since_after_until_is_400(self):
        self.assertEqual(self.get(since="2026-10-20", until="2026-10-01").status_code, 400)

    def test_junk_dates_are_400(self):
        self.assertEqual(self.get(since="yesterday").status_code, 400)
        self.assertEqual(self.get(until="31/12/2026").status_code, 400)

    def test_filters_combine_with_and(self):
        got = rows(self.get(category="fee", q="Aarav", actor=self.admin.pk))
        self.assertEqual({r["action"] for r in got}, {"fee.waive"})
        self.assertEqual(rows(self.get(category="fee", q="Aarav", actor=self.principal.pk)), [])

    def test_row_has_category(self):
        got = rows(self.get(action="fee.waive"))
        self.assertEqual(got[0]["category"], "fee")

    def test_facets(self):
        resp = self.as_(self.admin).get(self.url("facets/"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["total"], 5)
        cats = {c["category"]: c["count"] for c in resp.data["categories"]}
        self.assertEqual(cats["fee"], 1)
        self.assertEqual(cats["fee_waive"], 1)
        self.assertEqual({a["username"] for a in resp.data["actors"]}, {"admin", "principal"})

    def test_export_csv_is_filtered_audit_logged_and_formula_safe(self):
        resp = self.as_(self.admin).get(self.url("export/"), {"category": "attendance"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        self.assertEqual(resp["X-Row-Count"], "1")
        self.assertEqual(resp["X-Truncated"], "0")
        body = resp.content.decode("utf-8")
        self.assertTrue(body.startswith("\ufeff"))
        self.assertIn("'=HYPERLINK", body)           # the apostrophe guard
        self.assertNotIn(",=HYPERLINK", body)
        self.assertIn("audit.export", self.audit_actions())

    def test_teacher_and_student_cannot_read_audit_log(self):
        for user in (self.teacher, self.sa1, self.parent):
            for suffix in ("", "facets/", "export/"):
                self.assertIn(self.as_(user).get(self.url(suffix)).status_code, (403, 404), f"{user.username} {suffix}")

    def test_principal_can_read_audit_log(self):
        self.assertEqual(self.as_(self.principal).get(self.url()).status_code, 200)


# ===========================================================================
# 2. BACKUP / RESTORE
# ===========================================================================
class BackupTests(SafetyBase):
    def make(self, groups=None, user=None):
        resp = self.as_(user or self.admin).post(self.u("backups"), {"groups": groups or ["setup"], "note": "t"}, format="json")
        return resp

    # ---- create / list / detail ----
    def test_create_setup_backup(self):
        resp = self.make()
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "ready")
        self.assertEqual(resp.data["kind"], "manual")
        self.assertEqual(resp.data["groups"], ["setup"])
        self.assertEqual(len(resp.data["sha256"]), 64)
        self.assertGreater(resp.data["size_bytes"], 0)
        self.assertEqual(resp.data["row_counts"]["campus.section"], 2)
        self.assertNotIn("campus.attendance", resp.data["row_counts"])   # records group not asked for
        self.assertIn("backup.create", self.audit_actions())

    def test_default_group_is_setup(self):
        resp = self.as_(self.admin).post(self.u("backups"), {}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["groups"], ["setup"])

    def test_records_group_counts_attendance(self):
        from .models import Attendance
        Attendance.objects.create(enrollment=self.e_a1, date=timezone.localdate(), status="present", marked_by=self.teacher)
        resp = self.make(["setup", "records"])
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["row_counts"]["campus.attendance"], 1)

    def test_file_is_valid_gzip_json_with_header(self):
        resp = self.make()
        backup = CampusBackup.objects.get(pk=resp.data["id"])
        with gzip.GzipFile(fileobj=backup.file.open("rb")) as gz:
            data = json.loads(gz.read().decode("utf-8"))
        self.assertEqual(data["header"]["format"], rules.BACKUP_FORMAT)
        self.assertEqual(data["header"]["campus_id"], str(self.campus.id))
        self.assertEqual(len(data["models"]["campus.section"]), 2)

    def test_invalid_group_is_400_with_code(self):
        resp = self.make(["setup", "everything"])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "invalid_group")

    def test_groups_must_be_a_list(self):
        resp = self.as_(self.admin).post(self.u("backups"), {"groups": "setup"}, format="json")
        self.assertEqual(resp.status_code, 400)

    @override_settings(CAMPUS_BACKUP_MAX_ROWS=3)
    def test_too_many_rows_is_413_and_leaves_no_row(self):
        resp = self.make()
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(resp.data.get("code"), "too_many_rows")
        self.assertEqual(CampusBackup.objects.filter(campus=self.campus).count(), 0)

    def test_list_filters_and_is_campus_scoped(self):
        self.make()
        backup_svc.create_backup(self.campus, groups=["setup"], kind=rules.KIND_SCHEDULED)
        other = Campus.objects.create(name="Other", created_by=self.admin)
        backup_svc.create_backup(other, groups=["setup"])
        self.assertEqual(len(rows(self.as_(self.admin).get(self.u("backups")))), 2)
        self.assertEqual(len(rows(self.as_(self.admin).get(self.u("backups"), {"kind": "scheduled"}))), 1)

    def test_detail(self):
        bid = self.make().data["id"]
        resp = self.as_(self.admin).get(self.u("backups", bid))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], bid)

    def test_other_campus_backup_is_404(self):
        other = Campus.objects.create(name="Other", created_by=self.admin)
        foreign = backup_svc.create_backup(other, groups=["setup"])
        self.assertEqual(self.as_(self.admin).get(self.u("backups", foreign.id)).status_code, 404)
        self.assertEqual(self.as_(self.admin).delete(self.u("backups", foreign.id)).status_code, 404)

    def test_bad_backup_id_is_404(self):
        self.assertEqual(self.as_(self.admin).get(self.u("backups", "not-a-uuid")).status_code, 404)

    def test_delete_removes_file_and_row_and_is_audited(self):
        backup = CampusBackup.objects.get(pk=self.make().data["id"])
        path = backup.file.path
        resp = self.as_(self.admin).delete(self.u("backups", backup.id))
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(CampusBackup.objects.filter(pk=backup.pk).exists())
        import os
        self.assertFalse(os.path.exists(path))
        self.assertIn("backup.delete", self.audit_actions())

    # ---- download ----
    def test_download_streams_the_file_with_checksum_header(self):
        data = self.make().data
        resp = self.as_(self.admin).get(self.u("backups", data["id"], "download"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/gzip")
        self.assertIn(".json.gz", resp["Content-Disposition"])
        self.assertEqual(resp["X-Backup-SHA256"], data["sha256"])
        self.assertEqual(resp["Cache-Control"], "no-store")
        raw = b"".join(resp.streaming_content)
        self.assertEqual(json.loads(gzip.decompress(raw).decode("utf-8"))["header"]["format"], rules.BACKUP_FORMAT)
        self.assertIn("backup.download", self.audit_actions())

    def test_download_of_failed_backup_is_409(self):
        failed = CampusBackup.objects.create(campus=self.campus, status=CampusBackup.Status.FAILED, groups=["setup"])
        resp = self.as_(self.admin).get(self.u("backups", failed.id, "download"))
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "backup_not_ready")

    # ---- permissions ----
    def test_only_admin_or_principal_may_touch_backups(self):
        bid = self.make().data["id"]
        for user in (self.teacher, self.sa1, self.parent, self.outsider):
            c = self.as_(user)
            self.assertEqual(c.get(self.u("backups")).status_code, 403, user.username)
            self.assertEqual(c.post(self.u("backups"), {}, format="json").status_code, 403, user.username)
            self.assertEqual(c.get(self.u("backups", bid, "download")).status_code, 403, user.username)
            self.assertEqual(c.delete(self.u("backups", bid)).status_code, 403, user.username)

    def test_principal_can_back_up_and_download(self):
        resp = self.make(user=self.principal)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(self.as_(self.principal).get(self.u("backups", resp.data["id"], "download")).status_code, 200)

    # ---- prune / schedule ----
    @override_settings(CAMPUS_BACKUP_MAX_PER_CAMPUS=2)
    def test_manual_create_prunes_beyond_the_limit(self):
        for _ in range(4):
            self.assertEqual(self.make().status_code, 201)
        self.assertEqual(CampusBackup.objects.filter(campus=self.campus).count(), 2)

    def test_prune_drops_expired(self):
        old = backup_svc.create_backup(self.campus, groups=["setup"])
        CampusBackup.objects.filter(pk=old.pk).update(expires_at=timezone.now() - timedelta(days=1))
        keep = backup_svc.create_backup(self.campus, groups=["setup"])
        self.assertEqual(backup_svc.prune_backups(self.campus), 1)
        self.assertEqual(list(CampusBackup.objects.filter(campus=self.campus).values_list("pk", flat=True)), [keep.pk])

    def test_scheduled_task_backs_up_campuses_without_a_recent_one_and_is_idempotent(self):
        first = campus_tasks.run_scheduled_campus_backups()
        self.assertGreaterEqual(first["made"], 1)
        self.assertEqual(first["failed"], 0)
        self.assertTrue(CampusBackup.objects.filter(campus=self.campus, kind="scheduled", status="ready").exists())
        again = campus_tasks.run_scheduled_campus_backups()
        self.assertEqual(again["made"], 0)


class RestoreTests(SafetyBase):
    def setUp(self):
        super().setUp()
        self.backup = backup_svc.create_backup(self.campus, groups=["setup"], user=self.admin)

    def restore(self, user=None, **body):
        return self.as_(user or self.admin).post(self.u("backups", self.backup.id, "restore"), body, format="json")

    def test_dry_run_is_the_default_and_changes_nothing(self):
        Section.objects.filter(pk=self.secB.pk).delete()
        resp = self.restore()
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["dry_run"])
        self.assertEqual(resp.data["models"]["campus.section"]["created"], 1)
        self.assertFalse(Section.objects.filter(pk=self.secB.pk).exists())
        self.assertEqual(CampusBackup.objects.filter(kind="pre_restore").count(), 0)
        self.assertNotIn("backup.restore", self.audit_actions())

    def test_real_restore_needs_the_typed_word(self):
        Section.objects.filter(pk=self.secB.pk).delete()
        resp = self.restore(dry_run=False)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "confirm_required")
        self.assertFalse(Section.objects.filter(pk=self.secB.pk).exists())
        resp = self.restore(dry_run=False, confirm="restore please")
        self.assertEqual(resp.data.get("code"), "confirm_required")

    def test_real_restore_recreates_a_deleted_row_and_takes_a_pre_restore_backup(self):
        Section.objects.filter(pk=self.secB.pk).delete()
        resp = self.restore(dry_run=False, confirm="RESTORE")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data["dry_run"])
        self.assertEqual(resp.data["models"]["campus.section"]["created"], 1)
        self.assertEqual(resp.data["models"]["campus.section"]["failed"], 0, resp.data["errors"])
        self.assertTrue(Section.objects.filter(pk=self.secB.pk).exists())
        pre = CampusBackup.objects.get(kind="pre_restore")
        self.assertEqual(str(pre.pk), resp.data["pre_restore_backup"])
        self.assertIn("backup.restore", self.audit_actions())

    def test_confirm_word_is_case_and_space_tolerant(self):
        resp = self.restore(dry_run=False, confirm="  restore ")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_missing_only_never_touches_an_existing_row(self):
        Section.objects.filter(pk=self.secA.pk).update(name="CHANGED")
        resp = self.restore(dry_run=False, confirm="RESTORE", mode="missing_only")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Section.objects.get(pk=self.secA.pk).name, "CHANGED")
        self.assertEqual(resp.data["totals"]["updated"], 0)

    def test_overwrite_puts_old_values_back(self):
        original = Section.objects.get(pk=self.secA.pk).name
        Section.objects.filter(pk=self.secA.pk).update(name="CHANGED")
        resp = self.restore(dry_run=False, confirm="RESTORE", mode="overwrite")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Section.objects.get(pk=self.secA.pk).name, original)
        self.assertGreaterEqual(resp.data["models"]["campus.section"]["updated"], 1)

    def test_rows_added_after_the_backup_are_never_deleted(self):
        extra = Section.objects.create(school_class=self.cls, name="Z")
        self.restore(dry_run=False, confirm="RESTORE", mode="overwrite")
        self.assertTrue(Section.objects.filter(pk=extra.pk).exists())

    def test_parents_are_restored_before_children(self):
        # delete a class (cascades to its sections): the class is in the backup too (parents first), so it is
        # re-created and the section follows.
        from .models import SchoolClass
        SchoolClass.objects.filter(pk=self.cls2.pk).delete()
        resp = self.restore(dry_run=False, confirm="RESTORE")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(SchoolClass.objects.filter(pk=self.cls2.pk).exists())
        self.assertTrue(Section.objects.filter(pk=self.secB.pk).exists())

    def test_invalid_mode_and_group_not_in_backup(self):
        self.assertEqual(self.restore(mode="nuke").data.get("code"), "invalid_mode")
        self.assertEqual(self.restore(groups=["records"]).data.get("code"), "group_not_in_backup")
        self.assertEqual(self.restore(groups=["bogus"]).data.get("code"), "invalid_group")

    def test_only_the_campus_admin_may_restore_not_the_principal(self):
        resp = self.restore(user=self.principal, dry_run=False, confirm="RESTORE")
        self.assertEqual(resp.status_code, 403)
        for user in (self.teacher, self.sa1, self.parent, self.outsider):
            self.assertEqual(self.restore(user=user).status_code, 403, user.username)

    def test_tampered_file_is_refused_with_backup_corrupt(self):
        with open(self.backup.file.path, "ab") as fh:
            fh.write(b"tamper")
        resp = self.restore()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "backup_corrupt")

    def test_missing_file_is_410(self):
        import os
        os.remove(self.backup.file.path)
        resp = self.restore()
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data.get("code"), "backup_file_missing")

    def test_backup_of_another_campus_cannot_be_loaded_here(self):
        """A backup row moved to another campus must not restore (header campus id is checked)."""
        other = Campus.objects.create(name="Other", created_by=self.admin)
        CampusBackup.objects.filter(pk=self.backup.pk).update(campus=other)
        self.backup.refresh_from_db()
        with self.assertRaises(backup_svc.BackupError) as cm:
            backup_svc.load_backup_file(self.backup)
        self.assertEqual(cm.exception.code, "backup_wrong_campus")


# ===========================================================================
# 3. DATA DELETE REQUEST
# ===========================================================================
class DeletionBase(SafetyBase):
    def file(self, user, **body):
        return self.as_(user).post(self.u("deletion-requests"), body, format="json")

    def file_my(self, user, reason="please delete my data", **extra):
        return self.file(user, kind="my_data", reason=reason, **extra)

    def act(self, user, req_id, action, **body):
        return self.as_(user).post(self.u("deletion-requests", req_id, action), body, format="json")


class DeletionFilingTests(DeletionBase):
    def test_student_files_own_request(self):
        resp = self.file_my(self.sa1)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "pending")
        self.assertEqual(resp.data["kind"], "my_data")
        self.assertEqual(resp.data["subject"]["id"], self.sa1.pk)
        self.assertTrue(resp.data["due_at"])
        self.assertFalse(resp.data["is_overdue"])
        self.assertIn("data_request.create", self.audit_actions())
        self.notify.assert_called()   # approvers were told

    def test_due_date_follows_the_sla_setting(self):
        with override_settings(CAMPUS_DELETION_SLA_DAYS=10):
            resp = self.file_my(self.sa1)
        req = CampusDataDeletionRequest.objects.get(pk=resp.data["id"])
        self.assertAlmostEqual((req.due_at - req.created_at).total_seconds(), 10 * 86400, delta=5)

    def test_parent_files_for_linked_child(self):
        resp = self.file(self.parent, kind="child_data", reason="child is leaving", student=self.sa1.pk)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["subject"]["id"], self.sa1.pk)
        self.assertEqual(resp.data["requester"]["id"], self.parent.pk)

    def test_parent_cannot_file_for_someone_elses_child(self):
        resp = self.file(self.parent, kind="child_data", reason="not mine", student=self.sb.pk)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get("code"), "not_a_linked_parent")

    def test_child_data_needs_a_student(self):
        resp = self.file(self.parent, kind="child_data", reason="something")
        self.assertEqual(resp.data.get("code"), "student_required")

    def test_account_deletion_only_for_own_data(self):
        resp = self.file(self.parent, kind="child_data", reason="child leaving", student=self.sa1.pk,
                         wants_account_deletion=True)
        self.assertEqual(resp.data.get("code"), "account_deletion_own_data_only")

    def test_my_data_cannot_name_another_student(self):
        resp = self.file(self.sa1, kind="my_data", reason="sneaky", student=self.sa2.pk)
        self.assertEqual(resp.data.get("code"), "student_not_allowed")

    def test_reason_and_kind_validated(self):
        self.assertEqual(self.file(self.sa1, kind="my_data", reason=" ").data.get("code"), "reason_too_short")
        self.assertEqual(self.file(self.sa1, kind="everything", reason="abc def").data.get("code"), "invalid_kind")

    def test_unknown_student_is_400(self):
        resp = self.file(self.parent, kind="child_data", reason="child leaving", student=999999)
        self.assertEqual(resp.status_code, 400)

    def test_non_member_cannot_file(self):
        resp = self.file_my(self.outsider)
        self.assertEqual(resp.status_code, 403)

    def test_only_one_open_request_per_person(self):
        self.assertEqual(self.file_my(self.sa1).status_code, 201)
        again = self.file_my(self.sa1)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.data.get("code"), "request_already_open")

    def test_parent_request_blocks_the_child_own_second_request(self):
        """Open request is per SUBJECT: the child's data already has one pending."""
        self.assertEqual(self.file(self.parent, kind="child_data", reason="child leaving", student=self.sa1.pk).status_code, 201)
        self.assertEqual(self.file_my(self.sa1).status_code, 409)

    def test_can_file_again_after_it_was_rejected_or_cancelled(self):
        rid = self.file_my(self.sa1).data["id"]
        self.assertEqual(self.act(self.sa1, rid, "cancel").status_code, 200)
        self.assertEqual(self.file_my(self.sa1).status_code, 201)


class DeletionVisibilityTests(DeletionBase):
    def test_member_sees_only_own_requests_admin_sees_all(self):
        mine = self.file_my(self.sa1).data["id"]
        theirs = self.file_my(self.sa2).data["id"]
        got = {r["id"] for r in rows(self.as_(self.sa1).get(self.u("deletion-requests")))}
        self.assertEqual(got, {mine})
        got = {r["id"] for r in rows(self.as_(self.admin).get(self.u("deletion-requests")))}
        self.assertEqual(got, {mine, theirs})

    def test_parent_sees_request_filed_for_the_child_and_so_does_the_child(self):
        rid = self.file(self.parent, kind="child_data", reason="child leaving", student=self.sa1.pk).data["id"]
        for user in (self.parent, self.sa1):
            got = {r["id"] for r in rows(self.as_(user).get(self.u("deletion-requests")))}
            self.assertEqual(got, {rid}, user.username)

    def test_detail_is_hidden_from_others(self):
        rid = self.file_my(self.sa1).data["id"]
        self.assertEqual(self.as_(self.sa2).get(self.u("deletion-requests", rid)).status_code, 404)
        self.assertEqual(self.as_(self.teacher).get(self.u("deletion-requests", rid)).status_code, 404)
        self.assertEqual(self.as_(self.sa1).get(self.u("deletion-requests", rid)).status_code, 200)
        self.assertEqual(self.as_(self.admin).get(self.u("deletion-requests", rid)).status_code, 200)

    def test_non_member_cannot_list(self):
        self.assertEqual(self.as_(self.outsider).get(self.u("deletion-requests")).status_code, 403)

    def test_status_filter_overdue_filter_and_search(self):
        a = self.file_my(self.sa1).data["id"]
        b = self.file_my(self.sa2).data["id"]
        self.act(self.admin, b, "approve")
        CampusDataDeletionRequest.objects.filter(pk=a).update(due_at=timezone.now() - timedelta(days=1))
        c = self.as_(self.admin)
        self.assertEqual({r["id"] for r in rows(c.get(self.u("deletion-requests"), {"status": "approved"}))}, {b})
        self.assertEqual({r["id"] for r in rows(c.get(self.u("deletion-requests"), {"status": "pending,approved"}))}, {a, b})
        over = rows(c.get(self.u("deletion-requests"), {"overdue": "1"}))
        self.assertEqual({r["id"] for r in over}, {a})
        self.assertTrue(over[0]["is_overdue"])
        self.assertEqual({r["id"] for r in rows(c.get(self.u("deletion-requests"), {"q": "sa2"}))}, {b})

    def test_search_is_admin_only_a_member_cannot_search_other_people(self):
        self.file_my(self.sa1)
        self.file_my(self.sa2)
        got = rows(self.as_(self.sa1).get(self.u("deletion-requests"), {"q": "sa2"}))
        self.assertEqual(len(got), 1)   # q ignored for non-admins: still only their own row

    def test_requester_view_has_no_admin_only_extras(self):
        rid = self.file_my(self.sa1).data["id"]
        resp = self.as_(self.sa1).get(self.u("deletion-requests", rid))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([r["key"] for r in resp.data["will_keep"]], [k for k, _ in rules.RETAINED_RECORDS])


class DeletionDecisionTests(DeletionBase):
    def setUp(self):
        super().setUp()
        self.rid = self.file_my(self.sa1).data["id"]

    def test_approve(self):
        resp = self.act(self.admin, self.rid, "approve", note="ok")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "approved")
        self.assertEqual(resp.data["reviewed_by"]["id"], self.admin.pk)
        self.assertEqual(resp.data["review_note"], "ok")
        self.assertIn("data_request.approve", self.audit_actions())

    def test_reject_needs_a_note(self):
        resp = self.act(self.admin, self.rid, "reject")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "note_too_short")
        resp = self.act(self.admin, self.rid, "reject", note="Fees still due")
        self.assertEqual(resp.data["status"], "rejected")
        self.assertEqual(resp.data["review_note"], "Fees still due")

    def test_audit_line_never_contains_the_reason_text(self):
        CampusDataDeletionRequest.objects.filter(pk=self.rid).update(reason="very private reason xyz")
        self.act(self.admin, self.rid, "approve")
        texts = " ".join(CampusAuditLog.objects.filter(campus=self.campus, action__startswith="data_request.")
                         .values_list("summary", flat=True))
        self.assertNotIn("very private reason xyz", texts)

    def test_cannot_approve_twice_or_reject_after_approve(self):
        self.act(self.admin, self.rid, "approve")
        self.assertEqual(self.act(self.admin, self.rid, "approve").data.get("code"), "invalid_transition")
        self.assertEqual(self.act(self.admin, self.rid, "reject", note="too late").status_code, 409)

    def test_cannot_execute_before_approval(self):
        resp = self.act(self.admin, self.rid, "execute")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "invalid_transition")

    def test_only_admin_or_principal_decide(self):
        for user in (self.teacher, self.sa1, self.sa2, self.parent, self.outsider):
            for action in ("approve", "reject", "execute"):
                resp = self.act(user, self.rid, action, note="abc def")
                self.assertEqual(resp.status_code, 403, f"{user.username} {action}")
        self.assertEqual(CampusDataDeletionRequest.objects.get(pk=self.rid).status, "pending")

    def test_principal_can_decide(self):
        self.assertEqual(self.act(self.principal, self.rid, "approve").status_code, 200)

    def test_preview_is_admin_only(self):
        self.assertEqual(self.as_(self.admin).get(self.u("deletion-requests", self.rid, "preview")).status_code, 200)
        self.assertEqual(self.as_(self.sa1).get(self.u("deletion-requests", self.rid, "preview")).status_code, 403)

    def test_admin_cannot_decide_a_request_about_themselves_when_someone_else_can(self):
        own = self.file_my(self.principal).data["id"]
        # the principal filed it; the admin (another approver) may decide, the principal may not
        self.assertEqual(self.act(self.principal, own, "approve").data.get("code"), "cannot_decide_own_request")
        self.assertEqual(self.act(self.admin, own, "approve").status_code, 200)

    def test_sole_approver_may_decide_their_own_request(self):
        StaffProfile.objects.filter(user=self.principal).update(is_active=False)
        own = self.file_my(self.admin).data["id"]
        self.assertEqual(self.act(self.admin, own, "approve").status_code, 200)

    def test_requester_cancels_only_while_pending(self):
        self.assertEqual(self.act(self.sa2, self.rid, "cancel").status_code, 404)   # not their request
        self.assertEqual(self.act(self.admin, self.rid, "cancel").data.get("code"), "only_requester_can_cancel")
        resp = self.act(self.sa1, self.rid, "cancel")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "cancelled")
        self.assertEqual(self.act(self.sa1, self.rid, "cancel").data.get("code"), "invalid_transition")

    def test_cannot_cancel_after_approval(self):
        self.act(self.admin, self.rid, "approve")
        self.assertEqual(self.act(self.sa1, self.rid, "cancel").status_code, 409)

    def test_requester_is_notified_of_each_decision(self):
        self.notify.reset_mock()
        self.act(self.admin, self.rid, "approve")
        self.assertTrue(self.notify.called)
        kwargs = self.notify.call_args.kwargs
        self.assertEqual([u.pk for u in kwargs["users"]], [self.sa1.pk])
        self.assertEqual(kwargs["data"]["event"], "approved")
        self.assertNotIn("please delete my data", str(kwargs["body"]))   # no reason text on the lock screen


class DeletionExecuteTests(DeletionBase):
    def setUp(self):
        super().setUp()
        self.note = StudentNote.objects.create(
            author=self.sa1, campus=self.campus, section=self.secA, subject=self.math, text="my private note")
        self.other_note = StudentNote.objects.create(
            author=self.sa2, campus=self.campus, section=self.secA, subject=self.math, text="someone else's note")
        self.card = DigitalIDCard.objects.create(user=self.sa1, campus=self.campus, qr_token="tok-sa1")
        self.other_card = DigitalIDCard.objects.create(user=self.sa2, campus=self.campus, qr_token="tok-sa2")
        self.doubt = CampusDoubt.objects.create(
            campus=self.campus, session=self.session, section=self.secA, subject=self.math, author=self.sa1,
            text="what is a derivative?")
        from .models import Attendance
        self.att = Attendance.objects.create(
            enrollment=self.e_a1, date=timezone.localdate(), status="present", marked_by=self.teacher)
        self.rid = self.file_my(self.sa1).data["id"]
        self.act(self.admin, self.rid, "approve")

    def execute(self, user=None):
        return self.act(user or self.admin, self.rid, "execute")

    def test_preview_counts_match_what_exists(self):
        resp = self.as_(self.admin).get(self.u("deletion-requests", self.rid, "preview"))
        self.assertEqual(resp.status_code, 200)
        erase = resp.data["will_erase"]
        self.assertEqual(erase["student_notes"], 1)
        self.assertEqual(erase["digital_id_cards"], 1)
        self.assertEqual(erase["doubts"], 1)
        self.assertEqual({k["key"] for k in resp.data["will_keep"]}, {k for k, _ in rules.RETAINED_RECORDS})
        self.assertFalse(resp.data["account_deletion"])

    def test_preview_changes_nothing(self):
        self.as_(self.admin).get(self.u("deletion-requests", self.rid, "preview"))
        self.assertTrue(StudentNote.objects.filter(pk=self.note.pk).exists())

    def test_execute_erases_personal_content_and_keeps_school_records(self):
        resp = self.execute()
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "completed")
        self.assertTrue(resp.data["completed_at"])
        # erased
        self.assertFalse(StudentNote.objects.filter(pk=self.note.pk).exists())
        self.assertFalse(DigitalIDCard.objects.filter(pk=self.card.pk).exists())
        self.doubt.refresh_from_db()
        self.assertEqual(self.doubt.text, deletion_svc.REMOVED_TEXT)
        self.assertFalse(self.doubt.is_active)
        # untouched: someone else's data + the school's records
        self.assertTrue(StudentNote.objects.filter(pk=self.other_note.pk).exists())
        self.assertTrue(DigitalIDCard.objects.filter(pk=self.other_card.pk).exists())
        from .models import Attendance
        self.assertTrue(Attendance.objects.filter(pk=self.att.pk).exists())
        # the person is told what happened
        result = resp.data["result"]
        self.assertEqual(result["erased"]["student_notes"], 1)
        self.assertEqual(result["erased"]["digital_id_cards"], 1)
        self.assertFalse(result["account_deleted"])
        self.assertEqual({k["key"] for k in result["retained"]}, {k for k, _ in rules.RETAINED_RECORDS})
        self.assertIn("data_request.execute", self.audit_actions())

    def test_user_account_stays_when_account_deletion_was_not_asked(self):
        self.execute()
        self.sa1.refresh_from_db()
        self.assertTrue(self.sa1.is_active)
        self.assertEqual(self.sa1.username, "sa1")

    def test_execute_is_one_shot(self):
        self.assertEqual(self.execute().status_code, 200)
        again = self.execute()
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.data.get("code"), "invalid_transition")

    def test_result_is_only_shown_once_completed(self):
        rid2 = self.file_my(self.sa2).data["id"]
        self.assertEqual(self.as_(self.admin).get(self.u("deletion-requests", rid2)).data["result"], {})

    def test_child_data_request_erases_the_childs_and_the_parents_texts(self):
        # parent files for sa1 (the earlier own request is cancelled first: one open request per subject)
        CampusDataDeletionRequest.objects.filter(pk=self.rid).update(status="cancelled")
        resp = self.file(self.parent, kind="child_data", reason="child leaving", student=self.sa1.pk)
        self.assertEqual(resp.status_code, 201, resp.data)
        rid = resp.data["id"]
        self.act(self.admin, rid, "approve")
        out = self.act(self.admin, rid, "execute")
        self.assertEqual(out.status_code, 200, out.data)
        self.assertFalse(StudentNote.objects.filter(pk=self.note.pk).exists())   # the child's note
        self.assertTrue(StudentNote.objects.filter(pk=self.other_note.pk).exists())

    def test_account_deletion_is_refused_while_a_withdrawal_is_in_flight(self):
        CampusDataDeletionRequest.objects.filter(pk=self.rid).update(wants_account_deletion=True)
        with mock.patch("login.account_deletion.blocking_reasons", return_value=["coin withdrawal in progress"]):
            resp = self.execute()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "account_deletion_blocked")
        # nothing was erased and the request is still approved (retry later)
        self.assertTrue(StudentNote.objects.filter(pk=self.note.pk).exists())
        self.assertEqual(CampusDataDeletionRequest.objects.get(pk=self.rid).status, "approved")

    def test_account_deletion_runs_when_asked(self):
        CampusDataDeletionRequest.objects.filter(pk=self.rid).update(wants_account_deletion=True)
        with mock.patch("login.account_deletion.blocking_reasons", return_value=[]), \
                mock.patch("login.account_deletion.delete_account", return_value={"already_deleted": False}) as gone:
            resp = self.execute()
        self.assertEqual(resp.status_code, 200, resp.data)
        gone.assert_called_once()
        self.assertTrue(resp.data["result"]["account_deleted"])

    def test_only_admin_may_execute(self):
        self.assertEqual(self.execute(self.sa1).status_code, 403)
        self.assertTrue(StudentNote.objects.filter(pk=self.note.pk).exists())


class DeletionOverdueTaskTests(DeletionBase):
    def test_overdue_warning_goes_to_admins_on_day_0_then_weekly(self):
        rid = self.file_my(self.sa1).data["id"]
        for days_late, expected in ((0, 1), (3, 0), (7, 1), (8, 0)):
            self.notify.reset_mock()
            due = timezone.now() - timedelta(days=days_late, hours=1)
            CampusDataDeletionRequest.objects.filter(pk=rid).update(due_at=due)
            result = campus_tasks.warn_overdue_deletion_requests()
            self.assertEqual(result["warned"], expected, f"days_late={days_late}")
            self.assertEqual(self.notify.called, bool(expected))

    def test_finished_requests_are_never_warned_about(self):
        rid = self.file_my(self.sa1).data["id"]
        self.act(self.admin, rid, "reject", note="not possible")
        CampusDataDeletionRequest.objects.filter(pk=rid).update(due_at=timezone.now() - timedelta(days=1))
        self.assertEqual(campus_tasks.warn_overdue_deletion_requests()["warned"], 0)


# ===========================================================================
# 4. CONSENT LOG
# ===========================================================================
class ConsentBase(SafetyBase):
    def me_post(self, user, **body):
        return self.as_(user).post(reverse("campus-safety-consent-me", args=[str(self.campus.id)]), body, format="json")

    def me_get(self, user):
        return self.as_(user).get(reverse("campus-safety-consent-me", args=[str(self.campus.id)]))

    def item(self, resp, purpose, student=None):
        for it in resp.data["items"]:
            if it["purpose"] == purpose and ((it["student"] or {}).get("id") == student):
                return it
        return None


class ConsentSelfTests(ConsentBase):
    def test_state_lists_what_a_student_can_answer(self):
        resp = self.me_get(self.sa1)
        self.assertEqual(resp.status_code, 200)
        purposes = {it["purpose"] for it in resp.data["items"]}
        self.assertEqual(purposes, {"terms_privacy", "photo_media", "communications", "ai_features"})  # no child_data
        self.assertEqual(resp.data["policy_version"], consent_svc.policy_version())
        self.assertIsNone(self.item(resp, "terms_privacy")["status"])
        self.assertTrue(self.item(resp, "terms_privacy")["required"])

    def test_grant_then_withdraw_appends_two_rows(self):
        r1 = self.me_post(self.sa1, purpose="communications", granted=True)
        self.assertEqual(r1.status_code, 201, r1.data)
        self.assertEqual(r1.data["status"], "granted")
        self.assertEqual(r1.data["source"], "app")
        r2 = self.me_post(self.sa1, purpose="communications", granted=False)
        self.assertEqual(r2.data["status"], "withdrawn")
        self.assertEqual(CampusConsentRecord.objects.filter(subject=self.sa1, purpose="communications").count(), 2)
        self.assertEqual(self.item(self.me_get(self.sa1), "communications")["status"], "withdrawn")

    def test_latest_answer_wins_even_when_rows_share_a_timestamp(self):
        self.me_post(self.sa1, purpose="ai_features", granted=True)
        self.me_post(self.sa1, purpose="ai_features", granted=False)
        self.me_post(self.sa1, purpose="ai_features", granted=True)
        self.assertEqual(self.item(self.me_get(self.sa1), "ai_features")["status"], "granted")

    def test_rows_record_policy_version(self):
        resp = self.me_post(self.sa1, purpose="terms_privacy", granted=True)
        self.assertEqual(resp.data["policy_version"], consent_svc.policy_version())
        resp = self.me_post(self.sa1, purpose="terms_privacy", granted=True, policy_version="2025-01")
        self.assertEqual(resp.data["policy_version"], "2025-01")

    def test_old_policy_version_is_flagged_for_re_asking_but_a_withdrawal_is_not(self):
        self.me_post(self.sa1, purpose="terms_privacy", granted=True, policy_version="2020-01")
        self.me_post(self.sa1, purpose="ai_features", granted=False, policy_version="2020-01")
        state = self.me_get(self.sa1)
        self.assertTrue(self.item(state, "terms_privacy")["needs_update"])
        self.assertFalse(self.item(state, "ai_features")["needs_update"])
        self.me_post(self.sa1, purpose="terms_privacy", granted=True)
        self.assertFalse(self.item(self.me_get(self.sa1), "terms_privacy")["needs_update"])

    @override_settings(CAMPUS_CONSENT_POLICY_VERSION="2099-01")
    def test_policy_version_comes_from_settings(self):
        self.assertEqual(self.me_get(self.sa1).data["policy_version"], "2099-01")
        self.assertEqual(self.me_post(self.sa1, purpose="terms_privacy", granted=True).data["policy_version"], "2099-01")

    def test_validation_errors(self):
        self.assertEqual(self.me_post(self.sa1, purpose="nope", granted=True).data.get("code"), "invalid_purpose")
        self.assertEqual(self.me_post(self.sa1, purpose="communications", granted="yes").data.get("code"),
                         "granted_must_be_boolean")
        self.assertEqual(self.me_post(self.sa1, purpose="communications").data.get("code"), "granted_must_be_boolean")
        self.assertEqual(self.me_post(self.sa1, purpose="child_data", granted=True).data.get("code"), "student_required")
        self.assertEqual(
            self.me_post(self.sa1, purpose="communications", granted=True, student=self.sa2.pk).data.get("code"),
            "student_not_allowed")

    def test_non_member_cannot_answer_or_read(self):
        self.assertEqual(self.me_post(self.outsider, purpose="communications", granted=True).status_code, 403)
        self.assertEqual(self.me_get(self.outsider).status_code, 403)

    def test_one_person_cannot_answer_for_another(self):
        """The app endpoint always records the caller as the subject."""
        self.me_post(self.sa1, purpose="communications", granted=True, subject=self.sa2.pk)
        row = CampusConsentRecord.objects.get(purpose="communications")
        self.assertEqual(row.subject_id, self.sa1.pk)

    def test_rows_are_append_only(self):
        self.me_post(self.sa1, purpose="communications", granted=True)
        row = CampusConsentRecord.objects.get()
        row.note = "edited"
        with self.assertRaises(ValueError):
            row.save()
        with self.assertRaises(ValueError):
            row.delete()


class ConsentParentTests(ConsentBase):
    def test_parent_state_has_child_items(self):
        state = self.me_get(self.parent)
        self.assertIsNotNone(self.item(state, "child_data", self.sa1.pk))
        self.assertIsNotNone(self.item(state, "photo_media", self.sa1.pk))      # "either" -> per child when linked
        self.assertIsNone(self.item(state, "photo_media", None))
        self.assertIsNotNone(self.item(state, "communications", None))          # "self" purpose

    def test_parent_answers_for_linked_child(self):
        resp = self.me_post(self.parent, purpose="child_data", granted=True, student=self.sa1.pk)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["student"]["id"], self.sa1.pk)
        self.assertEqual(resp.data["subject"]["id"], self.parent.pk)
        self.assertEqual(self.item(self.me_get(self.parent), "child_data", self.sa1.pk)["status"], "granted")

    def test_parent_cannot_answer_for_an_unlinked_child(self):
        resp = self.me_post(self.parent, purpose="child_data", granted=True, student=self.sb.pk)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get("code"), "not_a_linked_parent")

    def test_two_children_two_independent_answers(self):
        CampusParentLink.objects.create(campus=self.campus, parent=self.parent, student=self.sb)
        self.me_post(self.parent, purpose="child_data", granted=True, student=self.sa1.pk)
        self.me_post(self.parent, purpose="child_data", granted=False, student=self.sb.pk)
        state = self.me_get(self.parent)
        self.assertEqual(self.item(state, "child_data", self.sa1.pk)["status"], "granted")
        self.assertEqual(self.item(state, "child_data", self.sb.pk)["status"], "withdrawn")


class ConsentAdminTests(ConsentBase):
    def url(self, name):
        return reverse(f"campus-safety-consent-{name}", args=[str(self.campus.id)])

    def record(self, user, **body):
        return self.as_(user).post(self.url("record"), body, format="json")

    def test_staff_enters_a_paper_consent(self):
        resp = self.record(self.admin, subject=self.sa1.pk, purpose="terms_privacy", granted=True, note="paper form #12")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["source"], "admin_entry")
        self.assertEqual(resp.data["recorded_by"]["id"], self.admin.pk)
        self.assertEqual(resp.data["subject"]["id"], self.sa1.pk)
        self.assertEqual(resp.data["note"], "paper form #12")
        self.assertIn("consent.record", self.audit_actions())
        # and the student sees it as answered
        self.assertEqual(self.item(self.me_get(self.sa1), "terms_privacy")["status"], "granted")

    def test_admin_entry_for_a_childs_consent_uses_the_parent_as_subject(self):
        resp = self.record(self.admin, subject=self.parent.pk, purpose="child_data", granted=True, student=self.sa1.pk)
        self.assertEqual(resp.status_code, 201, resp.data)
        resp = self.record(self.admin, subject=self.parent.pk, purpose="child_data", granted=True, student=self.sb.pk)
        self.assertEqual(resp.data.get("code"), "not_a_linked_parent")

    def test_subject_required_and_must_be_a_member(self):
        self.assertEqual(self.record(self.admin, purpose="terms_privacy", granted=True).status_code, 400)
        self.assertEqual(self.record(self.admin, subject=999999, purpose="terms_privacy", granted=True).status_code, 400)
        resp = self.record(self.admin, subject=self.outsider.pk, purpose="terms_privacy", granted=True)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get("code"), "not_a_member")

    def test_only_admin_or_principal_may_enter(self):
        for user in (self.teacher, self.sa1, self.parent, self.outsider):
            resp = self.record(user, subject=self.sa1.pk, purpose="terms_privacy", granted=True)
            self.assertEqual(resp.status_code, 403, user.username)
        self.assertEqual(self.record(self.principal, subject=self.sa1.pk, purpose="terms_privacy", granted=True).status_code, 201)

    def test_note_is_capped(self):
        resp = self.record(self.admin, subject=self.sa1.pk, purpose="terms_privacy", granted=True, note="x" * 1000)
        self.assertEqual(len(resp.data["note"]), rules.NOTE_MAX_CONSENT)


class ConsentLogTests(ConsentBase):
    def setUp(self):
        super().setUp()
        self.me_post(self.sa1, purpose="terms_privacy", granted=True)
        self.me_post(self.sa1, purpose="communications", granted=False)
        self.me_post(self.sa2, purpose="terms_privacy", granted=True)
        self.me_post(self.parent, purpose="child_data", granted=True, student=self.sa1.pk)
        self.as_(self.admin).post(
            reverse("campus-safety-consent-record", args=[str(self.campus.id)]),
            {"subject": self.sa2.pk, "purpose": "photo_media", "granted": True, "note": "signed at PTM"}, format="json")

    def url(self, name):
        return reverse(f"campus-safety-consent-{name}", args=[str(self.campus.id)])

    def log(self, **params):
        return self.as_(self.admin).get(self.url("log"), params)

    def test_log_lists_everything_newest_first(self):
        got = rows(self.log())
        self.assertEqual(len(got), 5)
        self.assertEqual(got[0]["purpose"], "photo_media")

    def test_filters(self):
        self.assertEqual(len(rows(self.log(purpose="terms_privacy"))), 2)
        self.assertEqual(len(rows(self.log(purpose="terms_privacy,communications"))), 3)
        self.assertEqual(len(rows(self.log(status="withdrawn"))), 1)
        self.assertEqual(len(rows(self.log(source="admin_entry"))), 1)
        self.assertEqual(len(rows(self.log(subject=self.sa2.pk))), 2)
        self.assertEqual(len(rows(self.log(student=self.sa1.pk))), 1)

    def test_search_matches_person_child_and_note(self):
        self.assertEqual(len(rows(self.log(q="sa2"))), 2)
        self.assertEqual(len(rows(self.log(q="PTM"))), 1)
        self.assertEqual(len(rows(self.log(q="parent"))), 1)
        self.assertEqual(len(rows(self.log(q="nobody-has-this"))), 0)

    def test_log_is_campus_scoped(self):
        other = Campus.objects.create(name="Other", created_by=self.admin)
        CampusConsentRecord.objects.create(campus=other, subject=self.sa1, purpose="terms_privacy", status="granted")
        self.assertEqual(len(rows(self.log())), 5)

    def test_log_is_admin_only(self):
        for user in (self.teacher, self.sa1, self.parent, self.outsider):
            self.assertEqual(self.as_(user).get(self.url("log")).status_code, 403, user.username)
            self.assertEqual(self.as_(user).get(self.url("summary")).status_code, 403, user.username)
            self.assertEqual(self.as_(user).get(self.url("export")).status_code, 403, user.username)

    def test_summary_counts(self):
        resp = self.as_(self.admin).get(self.url("summary"))
        self.assertEqual(resp.status_code, 200)
        by = {p["purpose"]: p for p in resp.data["purposes"]}
        self.assertEqual(by["terms_privacy"]["granted"], 2)
        self.assertEqual(by["communications"]["withdrawn"], 1)
        self.assertEqual(by["child_data"]["granted"], 1)
        # 'terms_privacy' is asked of every member: admin, principal, teachers, students, parent ... minus the 2 who answered
        members = consent_svc.member_ids(self.campus)
        self.assertEqual(by["terms_privacy"]["asked"], len(members))
        self.assertEqual(by["terms_privacy"]["missing"], len(members) - 2)
        # child_data is asked once per (parent, child) link
        self.assertEqual(by["child_data"]["asked"], 1)
        self.assertEqual(by["child_data"]["missing"], 0)
        self.assertEqual(resp.data["required_missing"], by["terms_privacy"]["missing"] + by["child_data"]["missing"])

    def test_summary_withdrawal_counts_as_answered_not_missing(self):
        resp = self.as_(self.admin).get(self.url("summary"))
        by = {p["purpose"]: p for p in resp.data["purposes"]}
        members = consent_svc.member_ids(self.campus)
        self.assertEqual(by["communications"]["missing"], len(members) - 1)

    def test_summary_flags_stale_grants(self):
        with override_settings(CAMPUS_CONSENT_POLICY_VERSION="2099-01"):
            resp = self.as_(self.admin).get(self.url("summary"))
        by = {p["purpose"]: p for p in resp.data["purposes"]}
        self.assertEqual(by["terms_privacy"]["needs_update"], 2)
        self.assertEqual(by["communications"]["needs_update"], 0)   # a withdrawal is never stale

    def test_csv_export(self):
        resp = self.as_(self.admin).get(self.url("export"), {"purpose": "terms_privacy"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        self.assertEqual(resp["X-Row-Count"], "2")
        body = resp.content.decode("utf-8")
        self.assertTrue(body.startswith("\ufeff"))
        self.assertEqual(len(body.strip().splitlines()), 3)   # header + 2 rows
        self.assertIn("consent.export", self.audit_actions())

    def test_csv_guards_formula_cells(self):
        CampusConsentRecord.objects.create(
            campus=self.campus, subject=self.sa1, purpose="communications", status="granted", note="=1+1")
        body = self.as_(self.admin).get(self.url("export")).content.decode("utf-8")
        self.assertIn("'=1+1", body)

    def test_overview_card(self):
        backup_svc.create_backup(self.campus, groups=["setup"])
        rid = self.as_(self.sa1).post(
            reverse("campus-safety-deletion-requests", args=[str(self.campus.id)]),
            {"kind": "my_data", "reason": "please delete"}, format="json").data["id"]
        CampusDataDeletionRequest.objects.filter(pk=rid).update(due_at=timezone.now() - timedelta(days=2))
        resp = self.as_(self.admin).get(reverse("campus-safety-overview", args=[str(self.campus.id)]))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["can_manage"])
        self.assertTrue(resp.data["can_restore"])
        self.assertEqual(resp.data["backups"]["count"], 1)
        self.assertIsNotNone(resp.data["backups"]["last"])
        self.assertEqual(resp.data["deletion_requests"], {"pending": 1, "approved_not_executed": 0, "overdue": 1})
        self.assertGreater(resp.data["consent"]["required_missing"], 0)
        # principal: may manage, but not restore
        resp = self.as_(self.principal).get(reverse("campus-safety-overview", args=[str(self.campus.id)]))
        self.assertTrue(resp.data["can_manage"])
        self.assertFalse(resp.data["can_restore"])
        # everybody else: no overview
        self.assertEqual(self.as_(self.teacher).get(reverse("campus-safety-overview", args=[str(self.campus.id)])).status_code, 403)
