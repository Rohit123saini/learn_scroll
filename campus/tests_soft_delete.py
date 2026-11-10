# campus/tests_soft_delete.py
"""Soft-delete + trash/restore + purge. NOT RUN when written (see STATUS_SOFT_DELETE_AUDIT.md):
    python manage.py test campus.tests_soft_delete

The fixture is self-contained on purpose (it does not depend on tests_t4_efg.T4Base).
"""
from datetime import date, time, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from . import settings_service
from .models import (
    AcademicSession, Campus, CampusAuditLog, Department, Room, SchoolClass, Section, StaffProfile,
    StudentEnrollment, Subject, TimeSlot, TimetableEntry,
)

User = get_user_model()


def rows(resp):
    data = resp.data
    return data.get("results", data) if isinstance(data, dict) else data


class SoftDeleteBase(APITestCase):
    def setUp(self):
        mk = lambda n: User.objects.create_user(username=n, password="pass12345")
        self.admin, self.teacher, self.student = mk("sd_admin"), mk("sd_teacher"), mk("sd_student")
        self.campus = Campus.objects.create(name="SD School", created_by=self.admin)
        self.session = AcademicSession.objects.create(
            campus=self.campus, name="2026-27", start_date=date(2026, 6, 1), end_date=date(2027, 4, 30), is_current=True)
        StaffProfile.objects.create(campus=self.campus, user=self.admin, role=StaffProfile.Role.ADMIN)
        self.t_staff = StaffProfile.objects.create(
            campus=self.campus, user=self.teacher, role=StaffProfile.Role.SUBJECT_TEACHER)
        self.dept = Department.objects.create(campus=self.campus, name="Sci")
        self.cls = SchoolClass.objects.create(campus=self.campus, session=self.session, name="10", department=self.dept)
        self.cls2 = SchoolClass.objects.create(campus=self.campus, session=self.session, name="9")
        self.secA = Section.objects.create(school_class=self.cls, name="A")     # will hold a student
        self.secB = Section.objects.create(school_class=self.cls2, name="B")    # empty
        self.maths = Subject.objects.create(campus=self.campus, name="Maths")
        StudentEnrollment.objects.create(student=self.student, section=self.secA, session=self.session, roll_number="1")

    def as_(self, user):
        self.client.force_authenticate(user)
        return self.client

    def backdate(self, obj, days):
        """Pretend the row went to the trash `days` ago."""
        type(obj).all_objects.filter(pk=obj.pk).update(deleted_at=timezone.now() - timedelta(days=days))


class ApiDeleteTests(SoftDeleteBase):
    def test_delete_moves_row_to_trash_and_writes_audit(self):
        r = self.as_(self.admin).delete(reverse("section-detail", args=[self.secB.id]))
        self.assertEqual(r.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Section.objects.filter(pk=self.secB.pk).exists())          # hidden from every normal query
        row = Section.all_objects.get(pk=self.secB.pk)                              # ...but not erased
        self.assertTrue(row.is_deleted)
        self.assertEqual(row.deleted_by_id, self.admin.id)
        self.assertIsNotNone(row.deleted_at)
        log = CampusAuditLog.objects.filter(campus=self.campus, action="section.delete", target_id=str(self.secB.pk)).first()
        self.assertIsNotNone(log)
        self.assertTrue(log.metadata.get("soft_delete"))

    def test_section_with_active_students_is_refused(self):
        r = self.as_(self.admin).delete(reverse("section-detail", args=[self.secA.id]))
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT, r.data)
        self.assertEqual(r.data.get("code"), "section_has_students")
        self.assertTrue(Section.objects.filter(pk=self.secA.pk).exists())

    def test_class_with_active_students_is_refused(self):
        r = self.as_(self.admin).delete(reverse("school-class-detail", args=[self.cls.id]))
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT, r.data)
        self.assertEqual(r.data.get("code"), "class_has_students")

    def test_class_delete_hides_children_in_one_batch_and_restore_brings_them_back(self):
        c = self.as_(self.admin)
        self.assertEqual(c.delete(reverse("school-class-detail", args=[self.cls2.id])).status_code, 204)
        self.assertFalse(Section.objects.filter(pk=self.secB.pk).exists())
        klass = SchoolClass.all_objects.get(pk=self.cls2.pk)
        sec = Section.all_objects.get(pk=self.secB.pk)
        self.assertEqual(klass.delete_batch, sec.delete_batch)
        r = c.post(reverse("school-class-restore", args=[self.cls2.id]))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(SchoolClass.objects.filter(pk=self.cls2.pk).exists())
        self.assertTrue(Section.objects.filter(pk=self.secB.pk).exists())
        self.assertTrue(CampusAuditLog.objects.filter(campus=self.campus, action="schoolclass.restore").exists()
                        or CampusAuditLog.objects.filter(campus=self.campus, action__endswith=".restore").exists())

    def test_a_child_deleted_earlier_on_its_own_stays_deleted_when_parent_is_restored(self):
        extra = Section.objects.create(school_class=self.cls2, name="C")
        c = self.as_(self.admin)
        self.assertEqual(c.delete(reverse("section-detail", args=[extra.id])).status_code, 204)   # own batch
        self.assertEqual(c.delete(reverse("school-class-detail", args=[self.cls2.id])).status_code, 204)
        self.assertEqual(c.post(reverse("school-class-restore", args=[self.cls2.id])).status_code, 200)
        self.assertTrue(Section.objects.filter(pk=self.secB.pk).exists())
        self.assertFalse(Section.objects.filter(pk=extra.pk).exists())

    def test_name_can_be_reused_after_the_old_row_went_to_trash(self):
        self.as_(self.admin).delete(reverse("section-detail", args=[self.secB.id]))
        again = Section.objects.create(school_class=self.cls2, name="B")      # alive-only unique constraint
        self.assertNotEqual(again.pk, self.secB.pk)


class TrashAndRestoreTests(SoftDeleteBase):
    def setUp(self):
        super().setUp()
        self.as_(self.admin).delete(reverse("section-detail", args=[self.secB.id]))

    def test_admin_sees_the_trash(self):
        r = self.as_(self.admin).get(reverse("section-trash"), {"campus": str(self.campus.id)})
        self.assertEqual(r.status_code, 200, getattr(r, "data", None))
        got = rows(r)
        self.assertEqual([x["id"] for x in got], [str(self.secB.id)])
        self.assertIn("label", got[0])
        self.assertIsNotNone(got[0]["deleted_at"])

    def test_trash_needs_campus_param(self):
        self.assertEqual(self.as_(self.admin).get(reverse("section-trash")).status_code, 400)

    def test_teacher_cannot_see_trash_or_restore(self):
        c = self.as_(self.teacher)
        self.assertEqual(c.get(reverse("section-trash"), {"campus": str(self.campus.id)}).status_code, 403)
        self.assertEqual(c.post(reverse("section-restore", args=[self.secB.id])).status_code, 403)
        self.assertFalse(Section.objects.filter(pk=self.secB.pk).exists())

    def test_restore_unknown_or_alive_row_is_404(self):
        c = self.as_(self.admin)
        self.assertEqual(c.post(reverse("section-restore", args=[self.secA.id])).status_code, 404)   # alive
        self.assertEqual(c.post(reverse("section-restore", args=["00000000-0000-0000-0000-000000000000"])).status_code, 404)

    def test_restore_brings_row_back_and_is_audited(self):
        r = self.as_(self.admin).post(reverse("section-restore", args=[self.secB.id]))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(Section.objects.filter(pk=self.secB.pk).exists())
        self.assertTrue(CampusAuditLog.objects.filter(campus=self.campus, action="section.restore").exists())

    def test_cannot_restore_a_child_while_its_parent_is_in_the_trash(self):
        c = self.as_(self.admin)
        # secB's class goes to trash too (cascades secB into the class batch)
        self.assertEqual(c.delete(reverse("school-class-detail", args=[self.cls2.id])).status_code, 204)
        r = c.post(reverse("section-restore", args=[self.secB.id]))
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT, r.data)
        self.assertEqual(r.data.get("code"), "parent_deleted")

    def test_restore_conflicting_with_a_newer_row_is_refused(self):
        Section.objects.create(school_class=self.cls2, name="B")          # same name, alive now
        r = self.as_(self.admin).post(reverse("section-restore", args=[self.secB.id]))
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT, r.data)
        self.assertEqual(r.data.get("code"), "restore_conflict")


class TimetableRestoreTests(SoftDeleteBase):
    def test_restore_is_refused_when_someone_booked_the_teacher_meanwhile(self):
        slot = TimeSlot.objects.create(campus=self.campus, day_of_week=1, start_time=time(9, 0), end_time=time(9, 45))
        first = TimetableEntry.objects.create(
            section=self.secA, subject=self.maths, staff=self.t_staff, time_slot=slot, session=self.session)
        c = self.as_(self.admin)
        self.assertEqual(c.delete(reverse("timetable-entry-detail", args=[first.id])).status_code, 204)
        TimetableEntry.objects.create(                          # same teacher, same slot, other section
            section=self.secB, subject=self.maths, staff=self.t_staff, time_slot=slot, session=self.session)
        r = c.post(reverse("timetable-entry-restore", args=[first.id]))
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT, r.data)
        self.assertEqual(r.data.get("code"), "timetable_conflict")
        self.assertFalse(TimetableEntry.objects.filter(pk=first.pk).exists())


class SettingsServiceSlotTests(SoftDeleteBase):
    def test_clear_slots_moves_unused_slots_to_trash_instead_of_erasing(self):
        free = TimeSlot.objects.create(campus=self.campus, day_of_week=2, start_time=time(9, 0), end_time=time(9, 45))
        out = settings_service.clear_slots(self.campus, self.admin, {"days": [2]})
        self.assertEqual(out["removed"], 1)
        self.assertFalse(TimeSlot.objects.filter(pk=free.pk).exists())
        self.assertTrue(TimeSlot.all_objects.filter(pk=free.pk, is_deleted=True).exists())
        log = CampusAuditLog.objects.filter(campus=self.campus, action="settings.time_slots.clear").first()
        self.assertTrue(log.metadata.get("soft_delete"))

    def test_soft_delete_all_helper(self):
        for d in (3, 4):
            TimeSlot.objects.create(campus=self.campus, day_of_week=d, start_time=time(9, 0), end_time=time(9, 45))
        n = TimeSlot.objects.filter(campus=self.campus).soft_delete_all(user=self.admin)
        self.assertEqual(n, 2)
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus).count(), 0)
        self.assertEqual(TimeSlot.all_objects.filter(campus=self.campus, is_deleted=True).count(), 2)


class PurgeCommandTests(SoftDeleteBase):
    def run_cmd(self, *args):
        out, err = StringIO(), StringIO()
        call_command("purge_soft_deleted", *args, stdout=out, stderr=err)
        return out.getvalue()

    def trash_room(self, name="R1", days_old=100):
        room = Room.objects.create(campus=self.campus, name=name)
        room.soft_delete(user=self.admin)
        self.backdate(room, days_old)
        return room

    def test_dry_run_is_the_default_and_changes_nothing(self):
        room = self.trash_room()
        out = self.run_cmd()
        self.assertIn("DRY RUN", out)
        self.assertTrue(Room.all_objects.filter(pk=room.pk).exists())

    def test_execute_erases_only_rows_older_than_retention(self):
        old = self.trash_room("Old", days_old=100)
        recent = self.trash_room("Recent", days_old=5)
        alive = Room.objects.create(campus=self.campus, name="Alive")
        self.run_cmd("--execute", "--days", "30")
        self.assertFalse(Room.all_objects.filter(pk=old.pk).exists())
        self.assertTrue(Room.all_objects.filter(pk=recent.pk).exists())
        self.assertTrue(Room.all_objects.filter(pk=alive.pk).exists())
        self.assertTrue(CampusAuditLog.objects.filter(campus=self.campus, action="room.purge", target_id=str(old.pk)).exists())

    @override_settings(CAMPUS_SOFT_DELETE_RETENTION_DAYS=10)
    def test_retention_comes_from_settings(self):
        room = self.trash_room(days_old=11)
        self.run_cmd("--execute")
        self.assertFalse(Room.all_objects.filter(pk=room.pk).exists())

    def test_campus_filter(self):
        other = Campus.objects.create(name="Other", created_by=self.admin)
        mine = self.trash_room("Mine")
        theirs = Room.objects.create(campus=other, name="Theirs")
        theirs.soft_delete(user=self.admin)
        self.backdate(theirs, 100)
        self.run_cmd("--execute", "--days", "30", "--campus", str(self.campus.id))
        self.assertFalse(Room.all_objects.filter(pk=mine.pk).exists())
        self.assertTrue(Room.all_objects.filter(pk=theirs.pk).exists())

    def test_parent_waits_while_a_child_is_alive_and_goes_with_trashed_old_children(self):
        self.secB.soft_delete(user=self.admin)                       # empty section -> trash
        self.backdate(self.secB, 100)
        self.run_cmd("--execute", "--days", "30", "--model", "Section")
        self.assertFalse(Section.all_objects.filter(pk=self.secB.pk).exists())
        # a class whose section is still alive must NOT be erased
        self.cls2 = SchoolClass.objects.create(campus=self.campus, session=self.session, name="8")
        sec = Section.objects.create(school_class=self.cls2, name="Z")
        SchoolClass.all_objects.filter(pk=self.cls2.pk).update(is_deleted=True, deleted_at=timezone.now() - timedelta(days=100))
        self.run_cmd("--execute", "--days", "30", "--model", "SchoolClass")
        self.assertTrue(SchoolClass.all_objects.filter(pk=self.cls2.pk).exists())
        self.assertTrue(Section.objects.filter(pk=sec.pk).exists())

    def test_campus_rows_need_the_explicit_flag(self):
        other = Campus.objects.create(name="Gone", created_by=self.admin)
        other.soft_delete(user=self.admin)
        self.backdate(other, 100)
        self.run_cmd("--execute", "--days", "30")
        self.assertTrue(Campus.all_objects.filter(pk=other.pk).exists())
        self.run_cmd("--execute", "--days", "30", "--include-campus")
        self.assertFalse(Campus.all_objects.filter(pk=other.pk).exists())

    def test_bad_arguments(self):
        with self.assertRaises(CommandError):
            self.run_cmd("--days", "0")
        with self.assertRaises(CommandError):
            self.run_cmd("--model", "NoSuchModel")
