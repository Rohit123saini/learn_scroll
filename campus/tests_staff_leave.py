# campus/tests_staff_leave.py
"""
Staff leave: apply -> approve / reject / cancel -> TeacherAbsence (substitute
flow) -> notifications -> visibility.

Run: `python manage.py test campus.tests_staff_leave`

Celery / FCM are never touched: `dispatch_after_commit` is mocked on the API
paths and `campus.bridge` on the push paths. Dates are relative to today so the
suite doesn't rot.
"""
from datetime import time, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from . import staff_leave_push
from . import substitutes as sub_svc
from . import tasks as campus_tasks
from .bridge import NotifTypes
from .models import CampusAuditLog, CampusHoliday, StaffLeave, StaffProfile, SubstituteAssignment, TeacherAbsence
from .tests_substitutes import SubstituteBase

User = get_user_model()
D = timedelta


class LeaveBase(SubstituteBase):
    """SubstituteBase gives: campus, admin (ADMIN), t_staff / t2_staff / t3_staff
    (teachers), an entry taught by t_staff on `target` (= tomorrow)."""

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.principal = User.objects.create_user(username="lv_principal", password="x")
        self.principal_staff = StaffProfile.objects.create(
            campus=self.campus, user=self.principal, role=StaffProfile.Role.PRINCIPAL_HOD
        )
        self.admin_staff = StaffProfile.objects.get(campus=self.campus, user=self.admin)
        self.teacher = self.t_staff.user
        self.other_teacher = self.t2_staff.user
        self.outsider = User.objects.create_user(username="lv_outsider", password="x")

    def apply(self, user, **over):
        body = {
            "leave_type": "casual",
            "date_from": (self.today + D(days=1)).isoformat(),
            "date_to": (self.today + D(days=2)).isoformat(),
            "reason": "Family function",
        }
        body.update(over)
        self.client.force_authenticate(user)
        with mock.patch("campus.staff_leave_views.dispatch_after_commit") as dispatch:
            resp = self.client.post(reverse("campus-staff-leave-list"), body, format="json")
        return resp, dispatch

    def make_leave(self, staff=None, **over):
        staff = staff or self.t_staff
        data = dict(
            campus=self.campus, staff=staff, leave_type="casual",
            date_from=self.today + D(days=1), date_to=self.today + D(days=2),
        )
        data.update(over)
        return StaffLeave.objects.create(**data)

    def act(self, user, leave, verb, body=None):
        self.client.force_authenticate(user)
        with mock.patch("campus.staff_leave_views.dispatch_after_commit") as dispatch:
            resp = self.client.post(
                reverse(f"campus-staff-leave-{verb}", args=[leave.id]), body or {}, format="json"
            )
        return resp, dispatch


class ApplyTests(LeaveBase):
    def test_staff_can_apply_and_approvers_are_notified_after_commit(self):
        resp, dispatch = self.apply(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["status"], "pending")
        self.assertEqual(resp.data["days"], 2.0)
        self.assertTrue(resp.data["is_mine"])
        self.assertTrue(resp.data["can_cancel"])
        self.assertFalse(resp.data["can_decide"])
        leave = StaffLeave.objects.get()
        self.assertEqual((leave.staff_id, leave.campus_id), (self.t_staff.id, self.campus.id))
        dispatch.assert_called_once_with(campus_tasks.send_staff_leave_notifications, str(leave.id), "requested")
        self.assertTrue(CampusAuditLog.objects.filter(action="staff_leave.apply").exists())

    def test_non_staff_cannot_apply(self):
        resp, _ = self.apply(self.outsider, campus=str(self.campus.id))
        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertEqual(resp.data.get("code"), "not_staff")

    def test_half_day(self):
        d = (self.today + D(days=3)).isoformat()
        resp, _ = self.apply(self.teacher, date_from=d, date_to=d, is_half_day=True)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["days"], 0.5)
        bad, _ = self.apply(self.other_teacher, is_half_day=True)  # 2-day range
        self.assertEqual(bad.status_code, 400, bad.data)
        self.assertEqual(bad.data.get("code"), "half_day_single_date")

    def test_single_date_defaults_date_to(self):
        d = (self.today + D(days=5)).isoformat()
        resp, _ = self.apply(self.teacher, date_from=d, date_to=None)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["date_to"], d)

    def test_validation_codes(self):
        cases = [
            ({"date_to": (self.today).isoformat()}, "invalid_range"),  # to < from (from = tomorrow)
            ({"date_from": (self.today - D(days=8)).isoformat(), "date_to": self.today.isoformat()}, "too_far_back"),
            ({"date_to": (self.today + D(days=100)).isoformat()}, "range_too_long"),
            ({"leave_type": "other", "reason": ""}, "reason_required"),
            ({"reason": "x" * 301}, None),  # serializer max_length => plain 400
        ]
        for over, code in cases:
            resp, _ = self.apply(self.teacher, **over)
            self.assertEqual(resp.status_code, 400, (over, resp.data))
            if code:
                self.assertEqual(resp.data.get("code"), code, over)
        self.assertEqual(StaffLeave.objects.count(), 0)

    def test_unknown_leave_type_rejected(self):
        resp, _ = self.apply(self.teacher, leave_type="holiday")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_backdated_sick_leave_within_a_week_is_ok(self):
        resp, _ = self.apply(
            self.teacher, leave_type="sick",
            date_from=(self.today - D(days=3)).isoformat(), date_to=(self.today - D(days=2)).isoformat(),
        )
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_overlap_with_pending_or_approved_blocked_but_not_with_rejected_or_cancelled(self):
        first = self.make_leave()
        resp, _ = self.apply(self.teacher)
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertEqual(resp.data.get("code"), "leave_overlap")
        for state in ("rejected", "cancelled"):
            StaffLeave.objects.filter(pk=first.pk).update(status=state)
            ok, _ = self.apply(self.teacher)
            self.assertEqual(ok.status_code, 201, (state, ok.data))
            StaffLeave.objects.exclude(pk=first.pk).delete()

    def test_touching_ranges_that_do_not_share_a_day_are_fine(self):
        self.make_leave(date_from=self.today + D(days=1), date_to=self.today + D(days=2))
        resp, _ = self.apply(
            self.teacher,
            date_from=(self.today + D(days=3)).isoformat(), date_to=(self.today + D(days=4)).isoformat(),
        )
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_user_with_two_campuses_must_name_one(self):
        from .models import Campus

        other = Campus.objects.create(
            created_by=self.admin, name="Other", type="school",
            verification_status=Campus.VerificationStatus.APPROVED,
        )
        StaffProfile.objects.create(campus=other, user=self.teacher, role=StaffProfile.Role.SUBJECT_TEACHER)
        resp, _ = self.apply(self.teacher)
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data.get("code"), "campus_required")
        ok, _ = self.apply(self.teacher, campus=str(other.id))
        self.assertEqual(ok.status_code, 201, ok.data)
        self.assertEqual(str(StaffLeave.objects.get().campus_id), str(other.id))

    def test_inactive_staff_cannot_apply(self):
        StaffProfile.objects.filter(pk=self.t_staff.pk).update(is_active=False)
        resp, _ = self.apply(self.teacher, campus=str(self.campus.id))
        self.assertEqual(resp.status_code, 403, resp.data)


class DecideTests(LeaveBase):
    def test_admin_approves_and_absence_is_recorded(self):
        leave = self.make_leave(leave_type="sick", reason="Fever, doctor advised rest")
        resp, dispatch = self.act(self.admin, leave, "approve", {"note": "Get well soon"})
        self.assertEqual(resp.status_code, 200, resp.data)
        leave.refresh_from_db()
        self.assertEqual(leave.status, "approved")
        self.assertEqual((leave.decided_by_id, leave.decision_note), (self.admin.id, "Get well soon"))
        self.assertIsNotNone(leave.decided_at)
        absence = leave.absence
        self.assertIsNotNone(absence)
        self.assertEqual((absence.staff_id, absence.date_from, absence.date_to), (self.t_staff.id, leave.date_from, leave.date_to))
        self.assertTrue(absence.is_active)
        # the absence reason is just the type — never the applicant's own words
        self.assertEqual(absence.reason, "Leave: Sick")
        self.assertNotIn("Fever", absence.reason)
        dispatch.assert_called_once_with(campus_tasks.send_staff_leave_notifications, str(leave.id), "approved")
        self.assertTrue(CampusAuditLog.objects.filter(action="staff_leave.approve").exists())

    def test_principal_can_decide_too(self):
        leave = self.make_leave()
        resp, _ = self.act(self.principal, leave, "approve")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_reject_needs_a_note_and_creates_no_absence(self):
        leave = self.make_leave()
        no_note, _ = self.act(self.admin, leave, "reject")
        self.assertEqual(no_note.status_code, 400, no_note.data)
        self.assertEqual(no_note.data.get("code"), "note_required")
        resp, dispatch = self.act(self.admin, leave, "reject", {"note": "Exams that week"})
        self.assertEqual(resp.status_code, 200, resp.data)
        leave.refresh_from_db()
        self.assertEqual((leave.status, leave.decision_note), ("rejected", "Exams that week"))
        self.assertIsNone(leave.absence)
        self.assertEqual(TeacherAbsence.objects.count(), 0)
        dispatch.assert_called_once_with(campus_tasks.send_staff_leave_notifications, str(leave.id), "rejected")

    def test_regular_teacher_cannot_decide(self):
        leave = self.make_leave(staff=self.t2_staff)
        resp, _ = self.act(self.teacher, leave, "approve")
        # a teacher can't even SEE a colleague's leave
        self.assertEqual(resp.status_code, 404, resp.data)
        own = self.make_leave()
        resp2, _ = self.act(self.teacher, own, "approve")
        self.assertEqual(resp2.status_code, 403, resp2.data)
        self.assertEqual(resp2.data.get("code"), "not_allowed")
        own.refresh_from_db()
        self.assertEqual(own.status, "pending")

    def test_admin_of_another_campus_cannot_see_or_decide(self):
        from .models import Campus

        other = Campus.objects.create(
            created_by=self.outsider, name="Other", type="school",
            verification_status=Campus.VerificationStatus.APPROVED,
        )
        StaffProfile.objects.create(campus=other, user=self.outsider, role=StaffProfile.Role.ADMIN)
        leave = self.make_leave()
        resp, _ = self.act(self.outsider, leave, "approve")
        self.assertEqual(resp.status_code, 404, resp.data)

    def test_cannot_decide_own_leave(self):
        leave = self.make_leave(staff=self.admin_staff)
        resp, _ = self.act(self.admin, leave, "approve")
        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertEqual(resp.data.get("code"), "cannot_decide_own_leave")
        # ...but the principal can
        ok, _ = self.act(self.principal, leave, "approve")
        self.assertEqual(ok.status_code, 200, ok.data)

    def test_cannot_decide_twice(self):
        leave = self.make_leave()
        self.act(self.admin, leave, "approve")
        again, _ = self.act(self.principal, leave, "reject", {"note": "changed my mind"})
        self.assertEqual(again.status_code, 409, again.data)
        self.assertEqual(again.data.get("code"), "leave_not_pending")
        leave.refresh_from_db()
        self.assertEqual(leave.status, "approved")

    def test_existing_covering_absence_is_reused_not_duplicated(self):
        existing = TeacherAbsence.objects.create(
            campus=self.campus, staff=self.t_staff, date_from=self.today, date_to=self.today + D(days=5),
            marked_by=self.admin,
        )
        leave = self.make_leave()
        self.act(self.admin, leave, "approve")
        leave.refresh_from_db()
        self.assertIsNone(leave.absence)
        self.assertEqual(TeacherAbsence.objects.count(), 1)
        # cancelling the leave must not touch an absence it didn't create
        self.act(self.admin, leave, "cancel")
        existing.refresh_from_db()
        self.assertTrue(existing.is_active)

    def test_approving_a_leave_already_in_the_past_records_no_absence(self):
        leave = self.make_leave(date_from=self.today - D(days=3), date_to=self.today - D(days=2))
        resp, _ = self.act(self.admin, leave, "approve")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(TeacherAbsence.objects.count(), 0)

    def test_can_decide_flag_only_for_approvers(self):
        leave = self.make_leave()
        self.client.force_authenticate(self.admin)
        self.assertTrue(self.client.get(reverse("campus-staff-leave-detail", args=[leave.id])).data["can_decide"])
        self.client.force_authenticate(self.teacher)
        self.assertFalse(self.client.get(reverse("campus-staff-leave-detail", args=[leave.id])).data["can_decide"])


class CancelTests(LeaveBase):
    def test_owner_cancels_pending_without_notifying_anyone(self):
        leave = self.make_leave()
        resp, dispatch = self.act(self.teacher, leave, "cancel")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "cancelled")
        dispatch.assert_not_called()
        # idempotent
        again, d2 = self.act(self.teacher, leave, "cancel")
        self.assertEqual(again.status_code, 200, again.data)
        d2.assert_not_called()

    def test_owner_cancels_approved_not_started_releases_absence_and_substitutes(self):
        self.mark_absent  # (SubstituteBase helper exists; we go through the leave instead)
        leave = self.make_leave(date_from=self.target, date_to=self.target)
        self.act(self.admin, leave, "approve")
        leave.refresh_from_db()
        sub = SubstituteAssignment.objects.create(
            campus=self.campus, entry=self.entry, date=self.target, substitute=self.t2_staff,
            original_staff=self.t_staff, absence=leave.absence, assigned_by=self.admin,
        )
        resp, dispatch = self.act(self.teacher, leave, "cancel")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["released_substitutes"], 1)
        leave.refresh_from_db()
        sub.refresh_from_db()
        leave.absence.refresh_from_db()
        self.assertEqual(leave.status, "cancelled")
        self.assertFalse(leave.absence.is_active)
        self.assertEqual(sub.status, "cancelled")
        calls = {c.args[1:] for c in dispatch.call_args_list}
        self.assertIn((str(sub.id), "cancelled"), calls)
        self.assertIn((str(leave.id), "cancelled_by_staff"), calls)

    def test_owner_cannot_cancel_once_started_but_admin_can(self):
        leave = self.make_leave(date_from=self.today, date_to=self.today + D(days=2), status="approved")
        resp, _ = self.act(self.teacher, leave, "cancel")
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertEqual(resp.data.get("code"), "leave_already_started")
        ok, dispatch = self.act(self.admin, leave, "cancel")
        self.assertEqual(ok.status_code, 200, ok.data)
        dispatch.assert_called_once_with(campus_tasks.send_staff_leave_notifications, str(leave.id), "cancelled_by_admin")

    def test_over_leave_cannot_be_cancelled(self):
        leave = self.make_leave(date_from=self.today - D(days=5), date_to=self.today - D(days=4), status="approved")
        resp, _ = self.act(self.admin, leave, "cancel")
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertEqual(resp.data.get("code"), "leave_already_over")

    def test_rejected_leave_cannot_be_cancelled(self):
        leave = self.make_leave(status="rejected")
        resp, _ = self.act(self.teacher, leave, "cancel")
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertEqual(resp.data.get("code"), "leave_not_cancellable")

    def test_colleague_cannot_cancel(self):
        leave = self.make_leave()
        resp, _ = self.act(self.other_teacher, leave, "cancel")
        self.assertEqual(resp.status_code, 404, resp.data)
        leave.refresh_from_db()
        self.assertEqual(leave.status, "pending")


class VisibilityAndListTests(LeaveBase):
    def setUp(self):
        super().setUp()
        self.mine = self.make_leave()
        self.theirs = self.make_leave(
            staff=self.t2_staff, date_from=self.today + D(days=10), date_to=self.today + D(days=11),
        )

    def ids(self, user, **params):
        self.client.force_authenticate(user)
        resp = self.client.get(reverse("campus-staff-leave-list"), params)
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data["results"] if isinstance(resp.data, dict) and "results" in resp.data else resp.data
        return {r["id"] for r in rows}

    def test_staff_see_only_their_own(self):
        self.assertEqual(self.ids(self.teacher), {str(self.mine.id)})
        self.assertEqual(self.ids(self.other_teacher), {str(self.theirs.id)})

    def test_admin_and_principal_see_whole_campus(self):
        both = {str(self.mine.id), str(self.theirs.id)}
        self.assertEqual(self.ids(self.admin), both)
        self.assertEqual(self.ids(self.principal), both)

    def test_outsider_and_students_see_nothing(self):
        self.assertEqual(self.ids(self.outsider), set())
        student = self.enrollments_user() if hasattr(self, "enrollments_user") else None
        if student:
            self.assertEqual(self.ids(student), set())

    def test_detail_hidden_from_colleague(self):
        self.client.force_authenticate(self.other_teacher)
        resp = self.client.get(reverse("campus-staff-leave-detail", args=[self.mine.id]))
        self.assertEqual(resp.status_code, 404)

    def test_filters(self):
        StaffLeave.objects.filter(pk=self.theirs.pk).update(status="approved")
        self.assertEqual(self.ids(self.admin, status="approved"), {str(self.theirs.id)})
        self.assertEqual(self.ids(self.admin, status="pending"), {str(self.mine.id)})
        self.assertEqual(self.ids(self.admin, staff=str(self.t2_staff.id)), {str(self.theirs.id)})
        self.assertEqual(self.ids(self.admin, mine="1"), set())  # admin has no leave of their own
        self.assertEqual(
            self.ids(self.admin, date_from=(self.today + D(days=9)).isoformat()), {str(self.theirs.id)}
        )
        self.assertEqual(
            self.ids(self.admin, date_to=(self.today + D(days=5)).isoformat()), {str(self.mine.id)}
        )
        self.client.force_authenticate(self.admin)
        bad = self.client.get(reverse("campus-staff-leave-list"), {"status": "nope"})
        self.assertEqual(bad.status_code, 400)
        bad = self.client.get(reverse("campus-staff-leave-list"), {"date_from": "31-12-2026"})
        self.assertEqual(bad.status_code, 400)

    def test_reason_not_visible_to_colleagues(self):
        StaffLeave.objects.filter(pk=self.mine.pk).update(reason="private")
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(reverse("campus-staff-leave-detail", args=[self.mine.id])).data["reason"], "private")
        self.client.force_authenticate(self.other_teacher)
        self.assertEqual(self.client.get(reverse("campus-staff-leave-detail", args=[self.mine.id])).status_code, 404)

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(reverse("campus-staff-leave-list")).status_code, (401, 403))


class SummaryTests(LeaveBase):
    def test_own_summary_counts_approved_and_pending_per_type(self):
        y = self.today.year
        self.make_leave(leave_type="sick", date_from=self.today + D(days=1), date_to=self.today + D(days=2), status="approved")
        self.make_leave(
            leave_type="casual", date_from=self.today + D(days=20), date_to=self.today + D(days=20),
            is_half_day=True, status="approved",
        )
        self.make_leave(leave_type="casual", date_from=self.today + D(days=30), date_to=self.today + D(days=31))
        self.make_leave(leave_type="sick", date_from=self.today + D(days=50), date_to=self.today + D(days=51), status="rejected")
        self.client.force_authenticate(self.teacher)
        resp = self.client.get(reverse("campus-staff-leave-summary"), {"year": y})
        self.assertEqual(resp.status_code, 200, resp.data)
        # leave near New Year could straddle `year`; only assert when all inside it
        if (self.today + D(days=51)).year == y:
            self.assertEqual(resp.data["approved"], {"sick": 2.0, "casual": 0.5})
            self.assertEqual(resp.data["pending"], {"casual": 2.0})
            self.assertEqual(resp.data["approved_total"], 2.5)
        self.assertNotIn("pending_for_approval", resp.data)

    def test_year_boundary_counts_only_days_inside_the_year(self):
        StaffLeave.objects.create(
            campus=self.campus, staff=self.t_staff, leave_type="earned",
            date_from=timezone.localdate().replace(year=2030, month=12, day=30),
            date_to=timezone.localdate().replace(year=2031, month=1, day=2), status="approved",
        )
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self.client.get(reverse("campus-staff-leave-summary"), {"year": 2030}).data["approved"], {"earned": 2.0})
        self.assertEqual(self.client.get(reverse("campus-staff-leave-summary"), {"year": 2031}).data["approved"], {"earned": 2.0})

    def test_admin_sees_pending_count_and_can_view_a_teachers_summary(self):
        self.make_leave()
        self.make_leave(staff=self.t2_staff, date_from=self.today + D(days=10), date_to=self.today + D(days=10))
        self.make_leave(staff=self.admin_staff, date_from=self.today + D(days=15), date_to=self.today + D(days=15))
        self.client.force_authenticate(self.admin)
        resp = self.client.get(reverse("campus-staff-leave-summary"), {"staff": str(self.t_staff.id)})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["staff"], str(self.t_staff.id))
        self.assertEqual(resp.data["pending_for_approval"], 2)  # the admin's own leave isn't theirs to approve

    def test_teacher_cannot_view_a_colleagues_summary(self):
        self.client.force_authenticate(self.teacher)
        resp = self.client.get(reverse("campus-staff-leave-summary"), {"staff": str(self.t2_staff.id)})
        self.assertEqual(resp.status_code, 403, resp.data)

    def test_bad_year(self):
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self.client.get(reverse("campus-staff-leave-summary"), {"year": "abc"}).status_code, 400)


class NotificationTests(LeaveBase):
    def test_requested_goes_to_admins_and_principals_but_not_the_applicant(self):
        leave = self.make_leave()
        with mock.patch("campus.staff_leave_push.bridge") as bridge:
            n = staff_leave_push.notify_leave_requested(leave)
        self.assertEqual(n, 2)
        kw = bridge.notify.call_args.kwargs
        self.assertEqual(kw["notif_type"], NotifTypes.CAMPUS_LEAVE_REQUESTED)
        self.assertTrue(kw["push"])
        self.assertEqual({u.pk for u in kw["users"]}, {self.admin.pk, self.principal.pk})
        self.assertNotIn("Family", kw["body"] + kw["title"])  # no reason on a lock screen

    def test_approver_who_applies_does_not_notify_themself(self):
        leave = self.make_leave(staff=self.admin_staff)
        with mock.patch("campus.staff_leave_push.bridge") as bridge:
            staff_leave_push.notify_leave_requested(leave)
        self.assertEqual({u.pk for u in bridge.notify.call_args.kwargs["users"]}, {self.principal.pk})

    def test_decision_goes_to_the_applicant_only(self):
        leave = self.make_leave(status="approved")
        with mock.patch("campus.staff_leave_push.bridge") as bridge:
            staff_leave_push.notify_leave_decided(leave, "approved")
        kw = bridge.notify.call_args.kwargs
        self.assertEqual([u.pk for u in kw["users"]], [self.teacher.pk])
        self.assertEqual(kw["notif_type"], NotifTypes.CAMPUS_LEAVE_DECIDED)
        self.assertIn("approved", kw["title"])

    def test_task_skips_when_state_no_longer_matches(self):
        leave = self.make_leave(status="cancelled")
        with mock.patch("campus.staff_leave_push.bridge") as bridge:
            res = campus_tasks.send_staff_leave_notifications(str(leave.id), "requested")
        self.assertEqual(res, {"sent": 0})
        bridge.notify.assert_not_called()

    def test_task_sends_for_matching_state(self):
        leave = self.make_leave()
        with mock.patch("campus.staff_leave_push.bridge") as bridge:
            res = campus_tasks.send_staff_leave_notifications(str(leave.id), "requested")
        self.assertEqual(res, {"sent": 2})
        self.assertTrue(bridge.notify.called)

    def test_notif_types_exist_in_core(self):
        from core.models import Notification

        values = {v for v, _ in Notification.NotifType.choices}
        self.assertIn(NotifTypes.CAMPUS_LEAVE_REQUESTED, values)
        self.assertIn(NotifTypes.CAMPUS_LEAVE_DECIDED, values)


# ---------------------------------------------------------------------------
# calendar (weekly off + holidays) and quota
# ---------------------------------------------------------------------------
class CalendarQuotaTests(LeaveBase):
    def _week(self):
        """7 consecutive days = exactly one of every weekday."""
        start = self.today + D(days=10)
        return start, start + D(days=6)

    def test_default_counts_every_day(self):
        a, b = self._week()
        resp, _ = self.apply(self.teacher, date_from=a.isoformat(), date_to=b.isoformat())
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["days"], 7.0)

    def test_weekly_off_days_do_not_count(self):
        self.campus.weekly_off_days = [7]
        self.campus.save(update_fields=["weekly_off_days"])
        a, b = self._week()
        resp, _ = self.apply(self.teacher, date_from=a.isoformat(), date_to=b.isoformat())
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["days"], 6.0)

    def test_half_day_weekday_counts_half(self):
        self.campus.weekly_off_days = [7]
        self.campus.half_day_weekdays = [6]
        self.campus.save(update_fields=["weekly_off_days", "half_day_weekdays"])
        a, b = self._week()  # one of every weekday: 5 full + Saturday 0.5 + Sunday off
        resp, _ = self.apply(self.teacher, date_from=a.isoformat(), date_to=b.isoformat())
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["days"], 5.5)

    def test_leave_only_on_a_half_day_weekday_is_half(self):
        self.campus.half_day_weekdays = [6]
        self.campus.save(update_fields=["half_day_weekdays"])
        a, _ = self._week()
        sat = a + D(days=(6 - a.isoweekday()) % 7)
        resp, _ = self.apply(self.teacher, date_from=sat.isoformat(), date_to=sat.isoformat())
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["days"], 0.5)

    def test_half_day_leave_on_a_half_day_weekday_stays_half(self):
        self.campus.half_day_weekdays = [6]
        self.campus.save(update_fields=["half_day_weekdays"])
        a, _ = self._week()
        sat = a + D(days=(6 - a.isoweekday()) % 7)
        resp, _ = self.apply(self.teacher, date_from=sat.isoformat(), date_to=sat.isoformat(), is_half_day=True)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["days"], 0.5)

    def test_weekly_off_wins_over_half_day(self):
        self.campus.weekly_off_days = [6]
        self.campus.half_day_weekdays = [6]
        self.campus.save(update_fields=["weekly_off_days", "half_day_weekdays"])
        a, b = self._week()
        resp, _ = self.apply(self.teacher, date_from=a.isoformat(), date_to=b.isoformat())
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["days"], 6.0)

    def test_holiday_does_not_count(self):
        a = self.today + D(days=10)
        CampusHoliday.objects.create(campus=self.campus, date=a + D(days=1), name="Festival")
        resp, _ = self.apply(
            self.teacher, date_from=a.isoformat(), date_to=(a + D(days=2)).isoformat()
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["days"], 2.0)

    def test_leave_only_on_off_days_is_refused(self):
        a = self.today + D(days=10)
        CampusHoliday.objects.create(campus=self.campus, date=a, name="Festival")
        resp, _ = self.apply(self.teacher, date_from=a.isoformat(), date_to=a.isoformat())
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data.get("code"), "no_working_days")

    def test_quota_blocks_the_leave_that_goes_over(self):
        self.campus.leave_quota = {"casual": 2}
        self.campus.save(update_fields=["leave_quota"])
        a = self.today + D(days=10)
        ok, _ = self.apply(self.teacher, date_from=a.isoformat(), date_to=(a + D(days=1)).isoformat())
        self.assertEqual(ok.status_code, 201, ok.data)
        later = self.today + D(days=20)
        over, _ = self.apply(self.teacher, date_from=later.isoformat(), date_to=later.isoformat())
        if later.year == a.year:  # quota is per calendar year
            self.assertEqual(over.status_code, 409, over.data)
            self.assertEqual(over.data.get("code"), "leave_quota_exceeded")
        # a type without a quota is unlimited
        sick, _ = self.apply(
            self.teacher, leave_type="sick", date_from=later.isoformat(), date_to=later.isoformat()
        )
        self.assertEqual(sick.status_code, 201, sick.data)

    def test_rejected_leave_frees_the_quota(self):
        self.campus.leave_quota = {"casual": 1}
        self.campus.save(update_fields=["leave_quota"])
        a = self.today + D(days=10)
        first, _ = self.apply(self.teacher, date_from=a.isoformat(), date_to=a.isoformat())
        self.assertEqual(first.status_code, 201, first.data)
        leave = StaffLeave.objects.get(pk=first.data["id"])
        self.act(self.principal, leave, "reject", {"note": "Not now"})
        again, _ = self.apply(self.teacher, date_from=a.isoformat(), date_to=a.isoformat())
        self.assertEqual(again.status_code, 201, again.data)

    def test_summary_shows_quota_and_remaining(self):
        self.campus.leave_quota = {"casual": 12}
        self.campus.save(update_fields=["leave_quota"])
        a = self.today + D(days=10)
        self.make_leave(date_from=a, date_to=a + D(days=1))
        self.client.force_authenticate(self.teacher)
        resp = self.client.get(reverse("campus-staff-leave-summary"), {"year": a.year})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["quota"], {"casual": 12.0})
        self.assertEqual(resp.data["remaining"], {"casual": 10.0})

    def test_admin_can_set_calendar_through_campus_api(self):
        self.client.force_authenticate(self.admin)
        url = reverse("campus-detail", args=[self.campus.id])
        ok = self.client.patch(url, {"weekly_off_days": [7, 6, 7], "leave_quota": {"casual": 12}}, format="json")
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(ok.data["weekly_off_days"], [6, 7])
        bad = self.client.patch(url, {"weekly_off_days": [0, 8]}, format="json")
        self.assertEqual(bad.status_code, 400, bad.data)
        bad2 = self.client.patch(url, {"leave_quota": {"holiday": 3}}, format="json")
        self.assertEqual(bad2.status_code, 400, bad2.data)

    def test_holiday_api_admin_writes_staff_reads(self):
        d = (self.today + D(days=15)).isoformat()
        self.client.force_authenticate(self.teacher)
        denied = self.client.post(
            reverse("campus-holiday-list"), {"campus": str(self.campus.id), "date": d, "name": "Diwali"}, format="json"
        )
        self.assertEqual(denied.status_code, 403, denied.data)
        self.client.force_authenticate(self.admin)
        made = self.client.post(
            reverse("campus-holiday-list"), {"campus": str(self.campus.id), "date": d, "name": "Diwali"}, format="json"
        )
        self.assertEqual(made.status_code, 201, made.data)
        dup = self.client.post(
            reverse("campus-holiday-list"), {"campus": str(self.campus.id), "date": d, "name": "Again"}, format="json"
        )
        self.assertEqual(dup.status_code, 400, dup.data)
        self.client.force_authenticate(self.teacher)
        listed = self.client.get(reverse("campus-holiday-list"), {"campus": str(self.campus.id)})
        self.assertEqual(listed.status_code, 200, listed.data)


# ---------------------------------------------------------------------------
# half day -> absence for that half only
# ---------------------------------------------------------------------------
class HalfDayAbsenceTests(LeaveBase):
    def _approved_half(self, part):
        d = self.target.isoformat()
        resp, _ = self.apply(self.teacher, date_from=d, date_to=d, is_half_day=True, half_day_part=part)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["half_day_part"], part)
        leave = StaffLeave.objects.get(pk=resp.data["id"])
        ok, _ = self.act(self.principal, leave, "approve")
        self.assertEqual(ok.status_code, 200, ok.data)
        leave.refresh_from_db()
        return leave

    def test_half_day_defaults_to_first_half(self):
        d = self.target.isoformat()
        resp, _ = self.apply(self.teacher, date_from=d, date_to=d, is_half_day=True)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["half_day_part"], "first_half")

    def test_first_half_covers_the_morning_period_only(self):
        leave = self._approved_half("first_half")
        self.assertEqual(leave.absence.part, "first_half")
        self.assertEqual(len(sub_svc.periods_for_absence(leave.absence)), 1)  # 10:00 period
        self.assertTrue(sub_svc.is_staff_absent(self.t_staff.pk, self.campus.id, self.target, time(10, 0)))
        self.assertFalse(sub_svc.is_staff_absent(self.t_staff.pk, self.campus.id, self.target, time(14, 0)))

    def test_second_half_leaves_the_morning_period_alone(self):
        leave = self._approved_half("second_half")
        self.assertEqual(leave.absence.part, "second_half")
        self.assertEqual(sub_svc.periods_for_absence(leave.absence), [])
        self.assertFalse(sub_svc.is_staff_absent(self.t_staff.pk, self.campus.id, self.target, time(10, 0)))
        self.assertTrue(sub_svc.is_staff_absent(self.t_staff.pk, self.campus.id, self.target, time(14, 0)))

    def test_full_day_absence_is_still_full(self):
        leave = self.make_leave(date_from=self.target, date_to=self.target)
        ok, _ = self.act(self.principal, leave, "approve")
        self.assertEqual(ok.status_code, 200, ok.data)
        leave.refresh_from_db()
        self.assertEqual(leave.absence.part, "full")


# ---------------------------------------------------------------------------
# only admin / principal at the campus
# ---------------------------------------------------------------------------
class SoleApproverTests(LeaveBase):
    def setUp(self):
        super().setUp()
        # the principal leaves -> the admin is the only person who could ever decide
        self.principal_staff.is_active = False
        self.principal_staff.save(update_fields=["is_active"])

    def test_sole_admin_leave_is_approved_on_apply(self):
        resp, dispatch = self.apply(self.admin)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "approved")
        self.assertEqual(resp.data["decided_by"], self.admin.pk)
        self.assertTrue(CampusAuditLog.objects.filter(campus=self.campus, action="staff_leave.auto_approve").exists())
        dispatch.assert_not_called()  # nobody else to tell

    def test_pending_own_leave_can_be_decided_by_the_sole_admin(self):
        leave = self.make_leave(staff=self.admin_staff)
        self.client.force_authenticate(self.admin)
        detail = self.client.get(reverse("campus-staff-leave-detail", args=[leave.id]))
        self.assertTrue(detail.data["can_decide"])
        ok, _ = self.act(self.admin, leave, "approve")
        self.assertEqual(ok.status_code, 200, ok.data)

    def test_with_another_approver_own_leave_still_needs_them(self):
        self.principal_staff.is_active = True
        self.principal_staff.save(update_fields=["is_active"])
        resp, _ = self.apply(self.admin)
        self.assertEqual(resp.data["status"], "pending")
        leave = StaffLeave.objects.get(pk=resp.data["id"])
        denied, _ = self.act(self.admin, leave, "approve")
        self.assertEqual(denied.data.get("code"), "cannot_decide_own_leave")


# ---------------------------------------------------------------------------
# edit a pending leave
# ---------------------------------------------------------------------------
class EditTests(LeaveBase):
    def _edit(self, user, leave, body):
        return self.act(user, leave, "edit", body)

    def test_owner_edits_pending_and_approvers_are_told(self):
        leave = self.make_leave()
        new_from = self.today + D(days=10)
        resp, dispatch = self._edit(
            self.teacher, leave,
            {"date_from": new_from.isoformat(), "date_to": (new_from + D(days=3)).isoformat(), "reason": "Wedding"},
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["days"], 4.0)
        self.assertEqual(resp.data["reason"], "Wedding")
        self.assertIsNotNone(resp.data["edited_at"])
        leave.refresh_from_db()
        self.assertEqual(leave.date_from, new_from)
        self.assertIn("edited", [c.args[-1] for c in dispatch.call_args_list])

    def test_single_day_leave_follows_a_new_start_date(self):
        d = self.today + D(days=5)
        leave = self.make_leave(date_from=d, date_to=d)
        new = self.today + D(days=8)
        resp, _ = self._edit(self.teacher, leave, {"date_from": new.isoformat()})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["date_to"], new.isoformat())

    def test_cannot_edit_after_a_decision(self):
        leave = self.make_leave(status="approved")
        resp, _ = self._edit(self.teacher, leave, {"reason": "x y z"})
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertEqual(resp.data.get("code"), "leave_not_editable")

    def test_only_the_applicant_can_edit(self):
        leave = self.make_leave()
        resp, _ = self._edit(self.admin, leave, {"reason": "Changed by admin"})
        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertEqual(resp.data.get("code"), "not_allowed")

    def test_edit_cannot_overlap_another_leave_but_may_overlap_itself(self):
        a = self.today + D(days=10)
        mine = self.make_leave(date_from=a, date_to=a + D(days=1))
        other = self.make_leave(date_from=a + D(days=5), date_to=a + D(days=6))
        ok, _ = self._edit(self.teacher, mine, {"date_to": (a + D(days=2)).isoformat()})
        self.assertEqual(ok.status_code, 200, ok.data)
        clash, _ = self._edit(self.teacher, mine, {"date_to": (a + D(days=5)).isoformat()})
        self.assertEqual(clash.status_code, 409, clash.data)
        self.assertEqual(clash.data.get("code"), "leave_overlap")
        self.assertTrue(other.pk)

    def test_edit_is_checked_against_the_quota(self):
        self.campus.leave_quota = {"casual": 2}
        self.campus.save(update_fields=["leave_quota"])
        a = self.today + D(days=10)
        leave = self.make_leave(date_from=a, date_to=a + D(days=1))
        resp, _ = self._edit(self.teacher, leave, {"date_to": (a + D(days=3)).isoformat()})
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertEqual(resp.data.get("code"), "leave_quota_exceeded")

    def test_task_sends_edit_push_to_approvers_only(self):
        leave = self.make_leave()
        with mock.patch("campus.staff_leave_push.bridge.notify") as notify:
            campus_tasks.send_staff_leave_notifications(str(leave.pk), "edited")
        users = notify.call_args.kwargs["users"]
        self.assertIn(self.admin, users)
        self.assertNotIn(self.teacher, users)


# ---------------------------------------------------------------------------
# admin applies for someone else
# ---------------------------------------------------------------------------
class AdminAppliesForStaffTests(LeaveBase):
    def test_admin_records_leave_for_a_teacher_as_approved(self):
        d = self.target.isoformat()
        resp, dispatch = self.apply(self.admin, staff=str(self.t_staff.id), date_from=d, date_to=d)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "approved")
        self.assertEqual(resp.data["staff"], self.t_staff.id)
        self.assertEqual(resp.data["applied_by"], self.admin.pk)
        leave = StaffLeave.objects.get(pk=resp.data["id"])
        self.assertIsNotNone(leave.absence_id)  # the substitute flow sees it
        self.assertIn("recorded_by_admin", [c.args[-1] for c in dispatch.call_args_list])
        self.assertTrue(CampusAuditLog.objects.filter(campus=self.campus, action="staff_leave.apply_for").exists())

    def test_teacher_cannot_apply_for_a_colleague(self):
        resp, _ = self.apply(self.teacher, staff=str(self.t2_staff.id))
        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertEqual(resp.data.get("code"), "not_allowed")

    def test_unknown_staff_is_404(self):
        resp, _ = self.apply(self.admin, staff="00000000-0000-0000-0000-000000000000")
        self.assertEqual(resp.status_code, 404, resp.data)

    def test_admin_leave_for_a_teacher_obeys_the_quota(self):
        self.campus.leave_quota = {"casual": 1}
        self.campus.save(update_fields=["leave_quota"])
        resp, _ = self.apply(self.admin, staff=str(self.t_staff.id))  # 2 days default
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertEqual(resp.data.get("code"), "leave_quota_exceeded")

    def test_recorded_for_push_goes_to_the_staff_member(self):
        leave = self.make_leave(status="approved")
        with mock.patch("campus.staff_leave_push.bridge.notify") as notify:
            campus_tasks.send_staff_leave_notifications(str(leave.pk), "recorded_by_admin")
        self.assertEqual(notify.call_args.kwargs["users"], [self.teacher])
        self.assertIn("recorded", notify.call_args.kwargs["title"])

