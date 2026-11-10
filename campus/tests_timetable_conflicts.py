# campus/tests_timetable_conflicts.py
"""Timetable conflict detection — teacher / room / section double booking.
NOT RUN when written (see STATUS_TIMETABLE_CONFLICTS.md): run
    python manage.py test campus.tests_timetable_conflicts
"""
from datetime import time
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from rest_framework import status

from . import timetable_conflicts as tc
from .models import Room, StaffProfile, Subject, TimeSlot, TimetableEntry
from .tests_t4_efg import T4Base


class ConflictBase(T4Base):
    def setUp(self):
        super().setUp()
        self.phy = Subject.objects.create(campus=self.campus, name="Physics")
        self.room1 = Room.objects.create(campus=self.campus, name="R1")
        self.room2 = Room.objects.create(campus=self.campus, name="R2")
        self.s0900 = self.slot(1, 9, 0, 9, 45)
        self.s0930 = self.slot(1, 9, 30, 10, 15)   # overlaps s0900
        self.s0945 = self.slot(1, 9, 45, 10, 30)   # back-to-back with s0900
        self.s_tue = self.slot(2, 9, 0, 9, 45)     # other weekday

    def slot(self, day, h1, m1, h2, m2):
        return TimeSlot.objects.create(campus=self.campus, day_of_week=day,
                                       start_time=time(h1, m1), end_time=time(h2, m2))

    def entry(self, section=None, subject=None, staff=None, slot=None, room=None, **kw):
        return TimetableEntry.objects.create(
            section=section or self.secA, subject=subject or self.maths, staff=staff or self.p_ct,
            time_slot=slot or self.s0900, room=room, session=self.session, **kw)

    def body(self, **over):
        data = {"section": str(self.secB.id), "subject": str(self.phy.id), "staff": str(self.p_sub.id),
                "time_slot": str(self.s0900.id), "session": str(self.session.id)}
        data.update({k: (str(v.id) if hasattr(v, "id") else v) for k, v in over.items()})
        return data


class ModelOverlapTests(ConflictBase):
    def test_same_teacher_overlapping_different_slot_rejected(self):
        self.entry(staff=self.p_ct, slot=self.s0900)
        with self.assertRaises(ValidationError):
            self.entry(section=self.secB, staff=self.p_ct, slot=self.s0930)

    def test_same_section_overlapping_rejected(self):
        self.entry(section=self.secA, slot=self.s0900)
        with self.assertRaises(ValidationError):
            self.entry(section=self.secA, subject=self.phy, staff=self.p_sub, slot=self.s0930)

    def test_same_room_overlapping_rejected(self):
        self.entry(room=self.room1, slot=self.s0900)
        with self.assertRaises(ValidationError):
            self.entry(section=self.secB, staff=self.p_sub, room=self.room1, slot=self.s0930)

    def test_exact_same_slot_still_rejected(self):
        self.entry(staff=self.p_ct, slot=self.s0900)
        with self.assertRaises(ValidationError):
            self.entry(section=self.secB, staff=self.p_ct, slot=self.s0900)

    def test_back_to_back_and_other_day_are_fine(self):
        self.entry(staff=self.p_ct, room=self.room1, slot=self.s0900)
        self.entry(section=self.secB, staff=self.p_ct, room=self.room1, slot=self.s0945)
        self.entry(section=self.secB, subject=self.phy, staff=self.p_ct, room=self.room1, slot=self.s_tue)

    def test_different_teacher_room_section_overlapping_is_fine(self):
        self.entry(staff=self.p_ct, room=self.room1, slot=self.s0900)
        self.entry(section=self.secB, staff=self.p_sub, room=self.room2, slot=self.s0930)

    def test_saving_an_entry_again_does_not_clash_with_itself(self):
        e = self.entry(slot=self.s0900)
        e.mode = "online"
        e.save()


class EntryApiTests(ConflictBase):
    def post(self, **over):
        self.as_(self.admin)
        return self.client.post(self.url("timetable-entry-list"), self.body(**over), format="json")

    def test_conflict_returns_structured_400(self):
        self.entry(staff=self.p_sub, section=self.secA, slot=self.s0900)
        r = self.post(time_slot=self.s0930)  # p_sub again, overlapping
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, r.data)
        self.assertEqual(r.data["code"], "timetable_conflict")
        self.assertEqual(r.data["conflicts"][0]["type"], "staff")
        self.assertIn("overlaps", r.data["detail"])
        self.assertFalse(TimetableEntry.objects.filter(section=self.secB).exists())

    def test_clean_entry_is_created(self):
        self.assertEqual(self.post().status_code, status.HTTP_201_CREATED)

    def test_edit_to_a_clashing_slot_is_rejected_but_self_edit_ok(self):
        self.entry(staff=self.p_ct, slot=self.s0900)
        mine = self.entry(section=self.secB, staff=self.p_sub, slot=self.s0945)
        self.as_(self.admin)
        url = self.url("timetable-entry-detail", pk=mine.id)
        self.assertEqual(self.client.patch(url, {"mode": "online"}, format="json").status_code, 200)
        r = self.client.patch(url, {"staff": str(self.p_ct.id), "time_slot": str(self.s0930.id)}, format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data["code"], "timetable_conflict")

    def test_cross_campus_ids_are_refused_without_leaking(self):
        from .models import AcademicSession, Campus
        other = Campus.objects.create(name="Other", created_by=self.admin)
        other_slot = TimeSlot.objects.create(campus=other, day_of_week=1, start_time=time(9), end_time=time(10))
        r = self.post(time_slot=other_slot)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data["code"], "campus_mismatch")

    def test_check_conflicts_endpoint(self):
        self.entry(staff=self.p_sub, slot=self.s0900)
        self.as_(self.admin)
        url = self.url("timetable-entry-check-conflicts")
        r = self.client.post(url, self.body(time_slot=self.s0930), format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["has_conflicts"])
        r = self.client.post(url, self.body(time_slot=self.s0945), format="json")
        self.assertFalse(r.data["has_conflicts"])
        self.assertEqual(TimetableEntry.objects.count(), 1)  # nothing saved

    def test_check_conflicts_excludes_the_entry_being_edited(self):
        e = self.entry(section=self.secB, staff=self.p_sub, slot=self.s0900)
        self.as_(self.admin)
        r = self.client.post(self.url("timetable-entry-check-conflicts"),
                             self.body(entry=str(e.id)), format="json")
        self.assertFalse(r.data["has_conflicts"], r.data)

    def test_student_cannot_use_check_conflicts(self):
        self.enroll(self.students[0], self.secA)
        self.as_(self.students[0])
        r = self.client.post(self.url("timetable-entry-check-conflicts"), self.body(), format="json")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)


class AuditEndpointTests(ConflictBase):
    def test_audit_lists_existing_clashes_admin_only(self):
        self.entry(staff=self.p_ct, slot=self.s0900)
        # bypass save() validation to simulate legacy bad data
        TimetableEntry.objects.bulk_create([TimetableEntry(
            section=self.secB, subject=self.phy, staff=self.p_ct, time_slot=self.s0930, session=self.session)])
        self.as_(self.admin)
        r = self.client.get(self.url("timetable-entry-conflicts"), {"campus": str(self.campus.id)})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["conflicts"][0]["type"], "staff")
        self.as_(self.ct)
        self.assertEqual(self.client.get(self.url("timetable-entry-conflicts"),
                                         {"campus": str(self.campus.id)}).status_code, 403)

    def test_audit_needs_campus(self):
        self.as_(self.admin)
        self.assertEqual(self.client.get(self.url("timetable-entry-conflicts")).status_code, 400)


class TimeSlotEditTests(ConflictBase):
    def test_moving_a_slot_into_a_clash_is_refused(self):
        self.entry(staff=self.p_ct, slot=self.s0900)
        late = self.slot(1, 11, 0, 11, 45)
        self.entry(section=self.secB, staff=self.p_ct, slot=late)
        self.as_(self.admin)
        url = self.url("time-slot-detail", pk=late.id)
        r = self.client.patch(url, {"start_time": "09:30:00", "end_time": "10:15:00"}, format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data["code"], "timetable_conflict")
        late.refresh_from_db()
        self.assertEqual(late.start_time, time(11, 0))
        ok = self.client.patch(url, {"start_time": "10:00:00", "end_time": "10:45:00"}, format="json")
        self.assertEqual(ok.status_code, 200, ok.data)


class ScanEntriesTests(SimpleTestCase):
    """Pure-python sweep used by the audit endpoint."""

    def e(self, staff, section, room, slot_id, day, start, end):
        return SimpleNamespace(staff_id=staff, section_id=section, room_id=room, time_slot_id=slot_id,
                               time_slot=SimpleNamespace(day_of_week=day, start_time=start, end_time=end))

    def test_finds_overlap_per_kind_once(self):
        a = self.e("t1", "s1", "r1", "A", 1, time(9), time(10))
        b = self.e("t1", "s2", "r1", "B", 1, time(9, 30), time(10, 30))
        c = self.e("t2", "s3", "r2", "C", 1, time(10), time(11))  # touches a: fine
        kinds = sorted(k for k, _, _ in tc.scan_entries([a, b, c]))
        self.assertEqual(kinds, ["room", "staff"])

    def test_other_day_not_a_clash(self):
        a = self.e("t1", "s1", None, "A", 1, time(9), time(10))
        b = self.e("t1", "s2", None, "B", 2, time(9), time(10))
        self.assertEqual(tc.scan_entries([a, b]), [])
