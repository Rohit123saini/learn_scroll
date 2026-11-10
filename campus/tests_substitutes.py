# campus/tests_substitutes.py
"""
Substitute teacher: absence -> affected periods -> free teachers -> assign /
replace / cancel -> timetable overlay -> notifications.

Run: `python manage.py test campus.tests_substitutes`

Celery / FCM are never touched: `dispatch_after_commit` is mocked on the API
paths and `campus.bridge` on the push paths — we assert WHO is notified and
WITH push, not delivery.

All dates are relative to "tomorrow" (the fixture session is stretched around
today), so the suite doesn't rot as the calendar moves.
"""
from datetime import time, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from . import substitute_push
from . import substitutes as sub_svc
from . import tasks as campus_tasks
from .models import (
    ClassMode,
    ClassTeacherassigments,
    StaffProfile,
    SubstituteAssignment,
    TeacherAbsence,
    TimeSlot,
    TimetableEntry,
)
from .tests_t4_efg import T4Base, rows

User = get_user_model()


class SubstituteBase(T4Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        today = timezone.localdate()
        # keep "tomorrow" inside the fixture's academic session whatever today is
        type(self.session).objects.filter(pk=self.session.pk).update(
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=365)
        )
        self.session.refresh_from_db()
        self.target = today + timedelta(days=1)

        self.t3_user = User.objects.create_user(username="teacher3", password="pass12345")
        self.t3_staff = StaffProfile.objects.create(
            campus=self.campus, user=self.t3_user, role=StaffProfile.Role.SUBJECT_TEACHER
        )
        self.slot = TimeSlot.objects.create(
            campus=self.campus, day_of_week=self.target.isoweekday(),
            start_time=time(10, 0), end_time=time(10, 45), label="Period 3",
        )
        # t_staff teaches Maths to 10-A at that slot (offline by default)
        self.entry = TimetableEntry.objects.create(
            section=self.secA, subject=self.math, staff=self.t_staff,
            time_slot=self.slot, session=self.session,
        )

    # ---- helpers ------------------------------------------------------------
    def mark_absent(self, staff=None, **extra):
        return TeacherAbsence.objects.create(
            campus=self.campus, staff=staff or self.t_staff,
            date_from=self.target, date_to=self.target, marked_by=self.admin, **extra,
        )

    def assign_body(self, substitute=None, **over):
        data = {
            "entry": str(self.entry.id),
            "date": self.target.isoformat(),
            "substitute": str((substitute or self.t2_staff).id),
        }
        data.update(over)
        return data

    def post_assign(self, user, **over):
        self.client.force_authenticate(user)
        with mock.patch("campus.substitute_views.dispatch_after_commit") as dispatch:
            resp = self.client.post(reverse("campus-substitute-list"), self.assign_body(**over), format="json")
        return resp, dispatch


class AbsenceTests(SubstituteBase):
    def post_absence(self, user, **over):
        self.client.force_authenticate(user)
        body = {"staff": str(self.t_staff.id), "date_from": self.target.isoformat(), "reason": "fever"}
        body.update(over)
        return self.client.post(reverse("campus-teacher-absence-list"), body, format="json")

    def test_admin_marks_absent_and_sees_counts(self):
        resp = self.post_absence(self.admin)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        absence = TeacherAbsence.objects.get(pk=resp.data["id"])
        self.assertEqual(absence.campus_id, self.campus.id)  # derived from the staff row
        self.assertEqual(absence.marked_by_id, self.admin.id)
        self.assertEqual(absence.date_to, self.target)  # defaults to date_from
        self.assertEqual(resp.data["periods_total"], 1)
        self.assertEqual(resp.data["periods_uncovered"], 1)
        self.assertEqual(resp.data["reason"], "fever")  # admin sees the reason

    def test_teacher_cannot_mark_absence(self):
        resp = self.post_absence(self.other_teacher)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_cannot_mark_or_list(self):
        self.assertEqual(self.post_absence(self.sa1).status_code, status.HTTP_403_FORBIDDEN)
        self.mark_absent()
        self.client.force_authenticate(self.sa1)
        self.assertEqual(rows(self.client.get(reverse("campus-teacher-absence-list"))), [])

    def test_overlapping_absence_rejected(self):
        self.mark_absent()
        resp = self.post_absence(self.admin)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_range_validation(self):
        past = (timezone.localdate() - timedelta(days=3)).isoformat()
        self.assertEqual(self.post_absence(self.admin, date_from=past).status_code, 400)
        inverted = self.post_absence(
            self.admin, date_from=self.target.isoformat(), date_to=(self.target - timedelta(days=1)).isoformat()
        )
        self.assertEqual(inverted.status_code, 400)
        too_long = self.post_absence(
            self.admin, date_to=(self.target + timedelta(days=sub_svc.MAX_RANGE_DAYS + 5)).isoformat()
        )
        self.assertEqual(too_long.status_code, 400)

    def test_reason_hidden_from_colleague_teacher(self):
        self.mark_absent(reason="surgery")
        self.client.force_authenticate(self.other_teacher)
        got = rows(self.client.get(reverse("campus-teacher-absence-list")))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["reason"], "")
        self.client.force_authenticate(self.teacher)  # the absent teacher sees their own
        self.assertEqual(rows(self.client.get(reverse("campus-teacher-absence-list")))[0]["reason"], "surgery")

    def test_periods_lists_the_affected_class(self):
        absence = self.mark_absent()
        self.client.force_authenticate(self.admin)
        resp = self.client.get(reverse("campus-teacher-absence-periods", args=[absence.id]))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["total"], 1)
        period = resp.data["periods"][0]
        self.assertEqual(period["entry"], str(self.entry.id))
        self.assertEqual(period["date"], self.target.isoformat())
        self.assertEqual(period["subject_name"], "Maths")
        self.assertFalse(period["covered"])

    def test_periods_ignore_other_weekdays(self):
        absence = TeacherAbsence.objects.create(
            campus=self.campus, staff=self.t_staff,
            date_from=self.target + timedelta(days=1), date_to=self.target + timedelta(days=5),
            marked_by=self.admin,
        )  # Mon..Fri window that doesn't repeat the entry's weekday until a week later
        self.assertEqual(len(sub_svc.periods_for_absence(absence)), 0)


class SuggestionTests(SubstituteBase):
    def get(self, user):
        self.client.force_authenticate(user)
        return self.client.get(
            reverse("campus-substitute-suggestions"),
            {"entry": str(self.entry.id), "date": self.target.isoformat()},
        )

    def test_free_teacher_listed_and_original_excluded(self):
        resp = self.get(self.admin)
        self.assertEqual(resp.status_code, 200, resp.data)
        by_id = {c["staff_id"]: c for c in resp.data["candidates"]}
        self.assertNotIn(str(self.t_staff.id), by_id)
        self.assertTrue(by_id[str(self.t2_staff.id)]["free"])
        self.assertTrue(by_id[str(self.t3_staff.id)]["free"])

    def test_teacher_with_overlapping_class_is_busy(self):
        TimetableEntry.objects.create(
            section=self.secB, subject=self.math, staff=self.t2_staff, time_slot=self.slot, session=self.session
        )
        by_id = {c["staff_id"]: c for c in self.get(self.admin).data["candidates"]}
        self.assertFalse(by_id[str(self.t2_staff.id)]["free"])
        # busy ones sort after free ones
        order = [c["free"] for c in self.get(self.admin).data["candidates"]]
        self.assertEqual(order, sorted(order, reverse=True))

    def test_overlap_not_just_equal_slot(self):
        other_slot = TimeSlot.objects.create(
            campus=self.campus, day_of_week=self.slot.day_of_week, start_time=time(10, 30), end_time=time(11, 15)
        )
        TimetableEntry.objects.create(
            section=self.secB, subject=self.phy, staff=self.t3_staff, time_slot=other_slot, session=self.session
        )
        by_id = {c["staff_id"]: c for c in self.get(self.admin).data["candidates"]}
        self.assertFalse(by_id[str(self.t3_staff.id)]["free"])

    def test_absent_teacher_not_free(self):
        self.mark_absent(staff=self.t2_staff)
        by_id = {c["staff_id"]: c for c in self.get(self.admin).data["candidates"]}
        self.assertFalse(by_id[str(self.t2_staff.id)]["free"])
        self.assertTrue(by_id[str(self.t2_staff.id)]["absent"])

    def test_teacher_being_covered_is_free_for_that_time(self):
        busy_entry = TimetableEntry.objects.create(
            section=self.secB, subject=self.math, staff=self.t2_staff, time_slot=self.slot, session=self.session
        )
        SubstituteAssignment.objects.create(
            campus=self.campus, entry=busy_entry, date=self.target,
            substitute=self.t3_staff, original_staff=self.t2_staff,
        )
        by_id = {c["staff_id"]: c for c in self.get(self.admin).data["candidates"]}
        self.assertTrue(by_id[str(self.t2_staff.id)]["free"])  # their class is covered
        self.assertFalse(by_id[str(self.t3_staff.id)]["free"])  # now t3 is the busy one

    def test_non_manager_forbidden(self):
        self.assertEqual(self.get(self.teacher).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.get(self.sa1).status_code, status.HTTP_403_FORBIDDEN)


class AssignTests(SubstituteBase):
    def test_admin_assigns_and_dispatches(self):
        absence = self.mark_absent()
        resp, dispatch = self.post_assign(self.admin)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        sub = SubstituteAssignment.objects.get(pk=resp.data["id"])
        self.assertEqual(sub.substitute_id, self.t2_staff.id)
        self.assertEqual(sub.original_staff_id, self.t_staff.id)  # snapshot
        self.assertEqual(sub.absence_id, absence.id)  # auto-linked
        self.assertEqual(sub.campus_id, self.campus.id)
        self.assertEqual(resp.data["subject_name"], "Maths")
        self.assertEqual(resp.data["section_label"], "10 - A")
        dispatch.assert_called_once_with(campus_tasks.send_substitute_notifications, str(sub.pk), "assigned")

    def test_assign_without_absence_is_allowed(self):
        resp, _ = self.post_assign(self.admin)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(SubstituteAssignment.objects.get(pk=resp.data["id"]).absence_id)

    def test_same_substitute_again_is_idempotent(self):
        self.post_assign(self.admin)
        resp, dispatch = self.post_assign(self.admin)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        dispatch.assert_not_called()
        self.assertEqual(SubstituteAssignment.objects.count(), 1)

    def test_different_substitute_replaces_and_notifies_both(self):
        first, _ = self.post_assign(self.admin)
        resp, dispatch = self.post_assign(self.admin, substitute=self.t3_staff)
        self.assertEqual(resp.status_code, 201, resp.data)
        old = SubstituteAssignment.objects.get(pk=first.data["id"])
        new = SubstituteAssignment.objects.get(pk=resp.data["id"])
        self.assertEqual(old.status, SubstituteAssignment.Status.CANCELLED)
        self.assertEqual(new.status, SubstituteAssignment.Status.ACTIVE)
        dispatch.assert_has_calls(
            [
                mock.call(campus_tasks.send_substitute_notifications, str(old.pk), "cancelled"),
                mock.call(campus_tasks.send_substitute_notifications, str(new.pk), "assigned"),
            ]
        )
        self.assertEqual(
            SubstituteAssignment.objects.filter(entry=self.entry, status="active").count(), 1
        )

    def test_rules(self):
        cases = {
            "same_teacher": dict(substitute=self.t_staff),
            "wrong_weekday": dict(date=(self.target + timedelta(days=1)).isoformat()),
            "past_date": dict(date=(self.target - timedelta(days=7)).isoformat()),  # same weekday, past
        }
        for code, over in cases.items():
            resp, dispatch = self.post_assign(self.admin, **over)
            self.assertEqual(resp.status_code, 400, (code, resp.data))
            self.assertIn(code, resp.content.decode(), code)
            dispatch.assert_not_called()

    def test_busy_substitute_rejected(self):
        TimetableEntry.objects.create(
            section=self.secB, subject=self.math, staff=self.t2_staff, time_slot=self.slot, session=self.session
        )
        resp, dispatch = self.post_assign(self.admin)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("substitute_busy", resp.content.decode())
        dispatch.assert_not_called()
        self.assertFalse(SubstituteAssignment.objects.exists())

    def test_absent_substitute_rejected(self):
        self.mark_absent(staff=self.t2_staff)
        resp, _ = self.post_assign(self.admin)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("substitute_absent", resp.content.decode())

    def test_non_teaching_and_foreign_campus_rejected(self):
        nt = StaffProfile.objects.create(
            campus=self.campus, user=User.objects.create_user(username="clerk", password="x12345678"),
            role=StaffProfile.Role.NON_TEACHING,
        )
        resp, _ = self.post_assign(self.admin, substitute=nt)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("invalid_substitute", resp.content.decode())

    def test_failed_replace_keeps_the_original_cover(self):
        first, _ = self.post_assign(self.admin)
        self.mark_absent(staff=self.t3_staff)
        resp, _ = self.post_assign(self.admin, substitute=self.t3_staff)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(SubstituteAssignment.objects.get(pk=first.data["id"]).status, "active")

    def test_subject_teacher_cannot_assign(self):
        resp, dispatch = self.post_assign(self.other_teacher)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        dispatch.assert_not_called()

    def test_class_teacher_of_section_can_assign(self):
        ClassTeacherassigments.objects.create(section=self.secA, staff=self.t2_staff)
        resp, _ = self.post_assign(self.other_teacher, substitute=self.t3_staff)
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_class_teacher_of_other_section_cannot(self):
        ClassTeacherassigments.objects.create(section=self.secB, staff=self.t2_staff)
        resp, _ = self.post_assign(self.other_teacher, substitute=self.t3_staff)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_cannot_assign_or_list(self):
        resp, _ = self.post_assign(self.sa1)
        self.assertEqual(resp.status_code, 403)
        self.post_assign(self.admin)
        self.client.force_authenticate(self.sa1)
        self.assertEqual(rows(self.client.get(reverse("campus-substitute-list"))), [])

    def test_garbage_ids_are_clean_errors(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            reverse("campus-substitute-list"), self.assign_body(entry="not-a-uuid"), format="json"
        )
        self.assertIn(resp.status_code, (400, 403))

    def test_mine_filter_for_the_substitute(self):
        self.post_assign(self.admin)
        self.client.force_authenticate(self.other_teacher)
        mine = rows(self.client.get(reverse("campus-substitute-list"), {"mine": "1"}))
        self.assertEqual(len(mine), 1)
        self.client.force_authenticate(self.t3_user)
        self.assertEqual(rows(self.client.get(reverse("campus-substitute-list"), {"mine": "1"})), [])


class CancelTests(SubstituteBase):
    def test_cancel_substitution_once(self):
        created, _ = self.post_assign(self.admin)
        url = reverse("campus-substitute-cancel", args=[created.data["id"]])
        self.client.force_authenticate(self.admin)
        with mock.patch("campus.substitute_views.dispatch_after_commit") as dispatch:
            first = self.client.post(url)
            second = self.client.post(url)  # idempotent
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["status"], "cancelled")
        self.assertEqual(second.status_code, 200)
        dispatch.assert_called_once_with(
            campus_tasks.send_substitute_notifications, created.data["id"], "cancelled"
        )

    def test_period_can_be_covered_again_after_cancel(self):
        created, _ = self.post_assign(self.admin)
        self.client.force_authenticate(self.admin)
        self.client.post(reverse("campus-substitute-cancel", args=[created.data["id"]]))
        resp, _ = self.post_assign(self.admin, substitute=self.t3_staff)
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_non_manager_cannot_cancel(self):
        created, _ = self.post_assign(self.admin)
        self.client.force_authenticate(self.other_teacher)
        resp = self.client.post(reverse("campus-substitute-cancel", args=[created.data["id"]]))
        self.assertEqual(resp.status_code, 403)

    def test_cancel_absence_releases_its_substitutes(self):
        absence = self.mark_absent()
        created, _ = self.post_assign(self.admin)
        self.client.force_authenticate(self.admin)
        with mock.patch("campus.substitute_views.dispatch_after_commit") as dispatch:
            resp = self.client.post(reverse("campus-teacher-absence-cancel", args=[absence.id]))
            again = self.client.post(reverse("campus-teacher-absence-cancel", args=[absence.id]))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["released_substitutes"], 1)
        self.assertFalse(resp.data["is_active"])
        self.assertEqual(again.data["released_substitutes"], 0)  # idempotent
        self.assertEqual(SubstituteAssignment.objects.get(pk=created.data["id"]).status, "cancelled")
        dispatch.assert_called_once_with(
            campus_tasks.send_substitute_notifications, created.data["id"], "cancelled"
        )

    def test_teacher_cannot_cancel_absence(self):
        absence = self.mark_absent()
        self.client.force_authenticate(self.other_teacher)
        self.assertEqual(
            self.client.post(reverse("campus-teacher-absence-cancel", args=[absence.id])).status_code, 403
        )


class OverlayTests(SubstituteBase):
    def get(self, user, section=None):
        self.client.force_authenticate(user)
        return self.client.get(
            reverse("campus-substitute-overlay"),
            {
                "section": str((section or self.secA).id),
                "date_from": self.target.isoformat(),
                "date_to": self.target.isoformat(),
            },
        )

    def test_uncovered_then_substituted(self):
        self.mark_absent(reason="private")
        resp = self.get(self.sa1)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual([i["status"] for i in resp.data["items"]], ["uncovered"])
        self.assertIsNone(resp.data["items"][0]["substitute"])

        self.post_assign(self.admin)
        item = self.get(self.sa1).data["items"][0]
        self.assertEqual(item["status"], "substituted")
        self.assertEqual(item["entry"], str(self.entry.id))
        self.assertEqual(item["substitute"]["staff_id"], str(self.t2_staff.id))
        self.assertTrue(item["substitute"]["name"])

    def test_reason_never_leaks(self):
        self.mark_absent(reason="chemotherapy")
        self.assertNotIn("chemotherapy", self.get(self.sa1).content.decode())
        self.assertNotIn("chemotherapy", self.get(self.parent).content.decode())

    def test_nothing_to_show_when_teacher_present(self):
        self.assertEqual(self.get(self.sa1).data["items"], [])

    def test_visibility_matches_section_access(self):
        self.mark_absent()
        self.assertEqual(self.get(self.sa1).status_code, 200)
        self.assertEqual(self.get(self.parent).status_code, 200)  # linked parent of sa1
        self.assertEqual(self.get(self.admin).status_code, 200)
        self.assertEqual(self.get(self.sb).status_code, 404)  # other section's student

    def test_cancelled_cover_falls_back_to_uncovered(self):
        self.mark_absent()
        created, _ = self.post_assign(self.admin)
        self.client.force_authenticate(self.admin)
        self.client.post(reverse("campus-substitute-cancel", args=[created.data["id"]]))
        self.assertEqual(self.get(self.sa1).data["items"][0]["status"], "uncovered")

    def test_defaults_to_current_week_and_requires_section(self):
        self.client.force_authenticate(self.sa1)
        ok = self.client.get(reverse("campus-substitute-overlay"), {"section": str(self.secA.id)})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.client.get(reverse("campus-substitute-overlay")).status_code, 400)


class ServiceTests(SubstituteBase):
    def test_effective_teacher(self):
        self.assertEqual(sub_svc.effective_teacher_user_id(self.entry, self.target), self.teacher.id)
        self.mark_absent()
        self.assertIsNone(sub_svc.effective_teacher_user_id(self.entry, self.target))  # absent, uncovered
        sub_svc.assign_substitute(
            entry=self.entry, on_date=self.target, substitute=self.t2_staff, assigned_by=self.admin
        )
        self.assertEqual(sub_svc.effective_teacher_user_id(self.entry, self.target), self.other_teacher.id)

    def test_unique_active_substitute_per_period(self):
        from django.db import IntegrityError, transaction

        SubstituteAssignment.objects.create(
            campus=self.campus, entry=self.entry, date=self.target,
            substitute=self.t2_staff, original_staff=self.t_staff,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            SubstituteAssignment.objects.create(
                campus=self.campus, entry=self.entry, date=self.target,
                substitute=self.t3_staff, original_staff=self.t_staff,
            )

    def test_weekly_timetable_is_never_modified(self):
        sub_svc.assign_substitute(
            entry=self.entry, on_date=self.target, substitute=self.t2_staff, assigned_by=self.admin
        )
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.staff_id, self.t_staff.id)


class ReminderTests(SubstituteBase):
    """The 5-minute reminder pings the substitute, not the absent teacher."""

    def test_reminder_teacher_selection(self):
        self.entry.mode = ClassMode.ONLINE
        self.entry.save()
        local_day = self.target
        self.assertEqual(sub_svc.effective_teacher_user_id(self.entry, local_day), self.teacher.id)

        sub_svc.assign_substitute(
            entry=self.entry, on_date=self.target, substitute=self.t2_staff, assigned_by=self.admin
        )
        recipients = campus_tasks._class_reminder_recipients(
            self.entry.section_id, self.entry.session_id, sub_svc.effective_teacher_user_id(self.entry, local_day)
        )
        self.assertIn(self.other_teacher.id, recipients)
        self.assertNotIn(self.teacher.id, recipients)
        self.assertIn(self.sa1.id, recipients)


class PushTests(SubstituteBase):
    def make_assignment(self, mode=ClassMode.OFFLINE):
        self.entry.mode = mode
        self.entry.save()
        return sub_svc.assign_substitute(
            entry=self.entry, on_date=self.target, substitute=self.t2_staff, assigned_by=self.admin
        ).assignment

    def run_assigned(self, assignment):
        with mock.patch.object(substitute_push.bridge, "notify") as notify, mock.patch.object(
            substitute_push.bridge, "push_to_parent_devices", return_value=0
        ) as devices:
            substitute_push.notify_substitute_assigned(assignment)
        return notify, devices

    def test_substitute_and_section_are_notified(self):
        notify, devices = self.run_assigned(self.make_assignment())
        self.assertEqual(notify.call_count, 2)
        to_sub = notify.call_args_list[0].kwargs
        self.assertEqual([u.pk for u in to_sub["users"]], [self.other_teacher.pk])
        self.assertEqual(to_sub["notif_type"], "campus_substitute_assigned")
        self.assertTrue(to_sub["push"])
        self.assertIn("10:00 AM", to_sub["body"])  # staff always see the time

        to_class = notify.call_args_list[1].kwargs
        self.assertEqual(to_class["notif_type"], "campus_class_substituted")
        self.assertEqual({u.pk for u in to_class["users"]}, {self.sa1.pk, self.sa2.pk, self.parent.pk})
        self.assertTrue(to_class["push"])  # class is tomorrow
        self.assertEqual(to_class["data"]["section_id"], str(self.secA.id))
        self.assertEqual(devices.call_count, 2)  # loginless parent devices, per student

    def test_offline_class_never_reveals_time_to_students(self):
        notify, _ = self.run_assigned(self.make_assignment(ClassMode.OFFLINE))
        body = notify.call_args_list[1].kwargs["body"]
        self.assertNotIn("AM", body)
        self.assertNotIn("PM", body)

    def test_online_class_shows_time(self):
        notify, _ = self.run_assigned(self.make_assignment(ClassMode.ONLINE))
        self.assertIn("10:00 AM", notify.call_args_list[1].kwargs["body"])

    def test_far_future_class_is_bell_only_for_students(self):
        a = self.make_assignment()
        SubstituteAssignment.objects.filter(pk=a.pk).update(date=self.target + timedelta(days=14))
        a.refresh_from_db()
        notify, devices = self.run_assigned(a)
        self.assertTrue(notify.call_args_list[0].kwargs["push"])  # the substitute is always pushed
        self.assertFalse(notify.call_args_list[1].kwargs["push"])
        devices.assert_not_called()

    def test_absence_reason_never_in_any_message(self):
        self.mark_absent(reason="very-private-reason")
        notify, _ = self.run_assigned(self.make_assignment())
        for call in notify.call_args_list:
            self.assertNotIn("very-private-reason", f"{call.kwargs['title']} {call.kwargs['body']}")

    def test_cancelled_goes_to_former_substitute_only(self):
        a = self.make_assignment()
        with mock.patch.object(substitute_push.bridge, "notify") as notify:
            substitute_push.notify_substitute_cancelled(a)
        kwargs = notify.call_args.kwargs
        self.assertEqual([u.pk for u in kwargs["users"]], [self.other_teacher.pk])
        self.assertEqual(kwargs["notif_type"], "campus_substitute_cancelled")

    def test_task_skips_when_state_changed(self):
        a = self.make_assignment()
        sub_svc.cancel_substitution(a)
        with mock.patch("campus.substitute_push.notify_substitute_assigned") as assigned:
            self.assertEqual(campus_tasks.send_substitute_notifications(str(a.pk), "assigned"), {"sent": 0})
        assigned.assert_not_called()  # assigned-then-cancelled in the queue: don't announce it

    def test_task_dispatches_by_kind(self):
        a = self.make_assignment()
        with mock.patch("campus.substitute_push.notify_substitute_assigned", return_value=3) as assigned:
            self.assertEqual(campus_tasks.send_substitute_notifications(str(a.pk), "assigned"), {"sent": 3})
        assigned.assert_called_once()
        self.assertEqual(campus_tasks.send_substitute_notifications("00000000-0000-0000-0000-000000000000"), {"sent": 0})


class MyClassesTests(SubstituteBase):
    def test_student_card_shows_substitute_for_next_period(self):
        sub_svc.assign_substitute(
            entry=self.entry, on_date=self.target, substitute=self.t2_staff, assigned_by=self.admin
        )
        self.client.force_authenticate(self.sa1)
        resp = self.client.get(reverse("campus-my-classes"))
        self.assertEqual(resp.status_code, 200)
        math_card = next(c for c in resp.data if c["subject"] == str(self.math.id))
        self.assertEqual(math_card["substitute"]["staff_id"], str(self.t2_staff.id))
        self.assertEqual(math_card["substitute"]["date"], self.target.isoformat())

    def test_no_substitute_key_noise_when_none(self):
        self.client.force_authenticate(self.sa1)
        resp = self.client.get(reverse("campus-my-classes"))
        math_card = next(c for c in resp.data if c["subject"] == str(self.math.id))
        self.assertIsNone(math_card["substitute"])
