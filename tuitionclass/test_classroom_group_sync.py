# tuitionclass/test_classroom_group_sync.py
"""T3 — a classroom's chat group is an exact mirror of its participants.

Real models end to end (no mocks of message/tuitionclass): create classroom ->
group exists; 3 students join -> 3 + teacher; 1 banned/refunded -> 2 + teacher;
reconcile repairs drift; backfill is idempotent.

`on_commit` callbacks only run inside `captureOnCommitCallbacks(execute=True)`
under TestCase, and Celery is eager under test (settings.TESTING)."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core import classroom_chat_bridge as bridge
from message.models import Group, GroupMember

from .models import ClassPass, Classroom, ClassroomBan, ClassroomStaff, PassPurchase

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, password="pw-12345")


class GroupMirrorBase(TestCase):
    def setUp(self):
        self.teacher = _user("teacher")
        self.students = [_user(f"s{i}") for i in range(3)]

    def _classroom(self, **kw):
        with self.captureOnCommitCallbacks(execute=True):
            c = Classroom.objects.create(teacher=self.teacher, title="Physics", **kw)
        c.refresh_from_db()
        return c

    def _pass(self, classroom):
        return ClassPass.objects.create(classroom=classroom, pass_type=ClassPass.PassType.MONTHLY, price=0, validity_days=30)

    def _join(self, classroom, student, days=30, status=PassPurchase.Status.SUCCESS):
        with self.captureOnCommitCallbacks(execute=True):
            return PassPurchase.objects.create(
                student=student, class_pass=self._pass(classroom), amount_paid=0, status=status,
                expires_at=timezone.now() + timedelta(days=days),
            )

    def _members(self, classroom):
        group = Group.objects.get(conversation_id=classroom.linked_conversation_id)
        return {m.user_id: m.role for m in GroupMember.objects.filter(group=group)}


class AutoCreateTests(GroupMirrorBase):
    def test_new_classroom_gets_group_with_teacher_admin(self):
        c = self._classroom()
        self.assertTrue(c.chat_group_enabled)
        self.assertIsNotNone(c.linked_conversation_id)
        self.assertEqual(self._members(c), {self.teacher.id: GroupMember.Role.ADMIN})

    def test_group_failure_never_fails_classroom_create(self):
        with mock.patch.object(bridge, "ensure_classroom_group", side_effect=RuntimeError("boom")):
            with self.captureOnCommitCallbacks(execute=True):
                c = Classroom.objects.create(teacher=self.teacher, title="X")
        self.assertTrue(Classroom.objects.filter(pk=c.pk).exists())
        c.refresh_from_db()
        self.assertIsNone(c.linked_conversation_id)  # reconcile/backfill will fix it later
        bridge.reconcile_classroom_group(c)
        c.refresh_from_db()
        self.assertIsNotNone(c.linked_conversation_id)

    def test_create_is_idempotent(self):
        c = self._classroom()
        first = c.linked_conversation_id
        bridge.ensure_classroom_group(c)
        bridge.ensure_classroom_group(c)
        c.refresh_from_db()
        self.assertEqual(c.linked_conversation_id, first)
        self.assertEqual(Group.objects.filter(conversation_id=first).count(), 1)


class MembershipEventTests(GroupMirrorBase):
    def test_three_join_then_one_banned(self):
        c = self._classroom()
        for s in self.students:
            self._join(c, s)
        m = self._members(c)
        self.assertEqual(len(m), 4)  # 3 students + teacher
        self.assertEqual(m[self.teacher.id], GroupMember.Role.ADMIN)
        self.assertTrue(all(m[s.id] == GroupMember.Role.MEMBER for s in self.students))

        with self.captureOnCommitCallbacks(execute=True):
            ClassroomBan.objects.create(classroom=c, student=self.students[0], banned_by=self.teacher)
        m = self._members(c)
        self.assertEqual(len(m), 3)
        self.assertNotIn(self.students[0].id, m)

    def test_refund_removes_but_second_live_pass_keeps_member(self):
        c = self._classroom()
        p1 = self._join(c, self.students[0])
        self._join(c, self.students[0])  # second live pass (e.g. renewal)
        with self.captureOnCommitCallbacks(execute=True):
            p1.status = PassPurchase.Status.REFUNDED
            p1.save()
        self.assertIn(self.students[0].id, self._members(c))

        solo = self._join(c, self.students[1])
        with self.captureOnCommitCallbacks(execute=True):
            solo.status = PassPurchase.Status.REFUNDED
            solo.save()
        self.assertNotIn(self.students[1].id, self._members(c))

    def test_lapse_is_active_false_removes(self):
        c = self._classroom()
        p = self._join(c, self.students[0])
        with self.captureOnCommitCallbacks(execute=True):
            p.is_active = False
            p.save()
        self.assertNotIn(self.students[0].id, self._members(c))

    def test_pending_purchase_is_not_a_member_until_success(self):
        c = self._classroom()
        p = self._join(c, self.students[0], status=PassPurchase.Status.PENDING)
        self.assertNotIn(self.students[0].id, self._members(c))
        with self.captureOnCommitCallbacks(execute=True):
            p.status = PassPurchase.Status.SUCCESS
            p.save()
        self.assertIn(self.students[0].id, self._members(c))

    def test_staff_add_promotes_and_remove_demotes_or_removes(self):
        c = self._classroom()
        ta = _user("ta")
        with self.captureOnCommitCallbacks(execute=True):
            staff = ClassroomStaff.objects.create(classroom=c, user=ta, role=ClassroomStaff.Role.MODERATOR)
        self.assertEqual(self._members(c)[ta.id], GroupMember.Role.MODERATOR)
        with self.captureOnCommitCallbacks(execute=True):
            staff.delete()
        self.assertNotIn(ta.id, self._members(c))  # no pass -> out

        # staff who ALSO holds a pass -> demoted, stays
        self._join(c, self.students[0])
        with self.captureOnCommitCallbacks(execute=True):
            st = ClassroomStaff.objects.create(classroom=c, user=self.students[0], role=ClassroomStaff.Role.TA)
        self.assertEqual(self._members(c)[self.students[0].id], GroupMember.Role.MODERATOR)
        with self.captureOnCommitCallbacks(execute=True):
            st.delete()
        self.assertEqual(self._members(c)[self.students[0].id], GroupMember.Role.MEMBER)

    def test_classroom_close_archives_group(self):
        c = self._classroom()
        with self.captureOnCommitCallbacks(execute=True):
            c.is_active = False
            c.save()
        self.assertFalse(Group.objects.filter(conversation_id=c.linked_conversation_id).exists())
        self.assertTrue(Group.all_objects.filter(conversation_id=c.linked_conversation_id, is_deleted=True).exists())


class ReconcileTests(GroupMirrorBase):
    def test_reconcile_repairs_drift_and_dry_run_writes_nothing(self):
        c = self._classroom()
        for s in self.students:
            self._join(c, s)
        group = Group.objects.get(conversation_id=c.linked_conversation_id)
        stranger = _user("stranger")
        # drift: one real participant missing, one stranger present, one wrong role
        GroupMember.objects.filter(group=group, user=self.students[0]).delete()
        GroupMember.objects.create(group=group, user=stranger, role=GroupMember.Role.MEMBER)
        GroupMember.objects.filter(group=group, user=self.students[1]).update(role=GroupMember.Role.MODERATOR)

        before = self._members(c)
        report = bridge.reconcile_classroom_group(c, dry_run=True)
        self.assertEqual(self._members(c), before)  # dry run = no change
        self.assertEqual(report["added"], [str(self.students[0].id)])
        self.assertEqual(report["removed"], [str(stranger.id)])
        self.assertEqual(report["role_fixed"], [str(self.students[1].id)])

        bridge.reconcile_classroom_group(c)
        m = self._members(c)
        self.assertEqual(set(m), {self.teacher.id, *[s.id for s in self.students]})
        self.assertEqual(m[self.students[1].id], GroupMember.Role.MEMBER)
        again = bridge.reconcile_classroom_group(c)
        self.assertEqual((again["added"], again["removed"], again["role_fixed"]), ([], [], []))

    def test_expired_pass_removed_by_reconcile(self):
        c = self._classroom()
        p = self._join(c, self.students[0])
        PassPurchase.objects.filter(pk=p.pk).update(expires_at=timezone.now() - timedelta(minutes=5))
        self.assertIn(self.students[0].id, self._members(c))  # nothing fired yet — time just passed
        bridge.reconcile_classroom_group(c)
        self.assertNotIn(self.students[0].id, self._members(c))

    def test_missing_group_is_recreated(self):
        c = self._classroom()
        self._join(c, self.students[0])
        Group.objects.filter(conversation_id=c.linked_conversation_id).delete()  # hard-gone
        report = bridge.reconcile_classroom_group(c)
        self.assertTrue(report["group_missing"])
        c.refresh_from_db()
        self.assertEqual(set(self._members(c)), {self.teacher.id, self.students[0].id})

    def test_management_command_and_task(self):
        c = self._classroom()
        self._join(c, self.students[0])
        out = StringIO()
        call_command("reconcile_classroom_groups", "--dry-run", stdout=out)
        self.assertIn("DRY-RUN", out.getvalue())
        from core.tasks import reconcile_classroom_groups

        result = reconcile_classroom_groups.apply(kwargs={"classroom_id": c.pk}).get()
        self.assertEqual(result["classrooms"], 1)
        self.assertEqual(result["errors"], 0)

    def test_capacity_mismatch_reported(self):
        c = self._classroom(max_participants=1)
        for s in self.students[:2]:
            self._join(c, s)
        self.assertTrue(bridge.reconcile_classroom_group(c)["capacity_mismatch"])


class ToggleAndBackfillTests(GroupMirrorBase):
    def test_toggle_off_archives_and_on_restores_same_conversation(self):
        c = self._classroom()
        self._join(c, self.students[0])
        conv = c.linked_conversation_id
        bridge.set_group_enabled(c, False)
        c.refresh_from_db()
        self.assertFalse(c.chat_group_enabled)
        self.assertFalse(Group.objects.filter(conversation_id=conv).exists())  # archived, not deleted
        self.assertTrue(Group.all_objects.filter(conversation_id=conv).exists())
        # while disabled, joins don't resurrect it
        self._join(c, self.students[1])
        self.assertFalse(Group.objects.filter(conversation_id=conv).exists())

        bridge.set_group_enabled(c, True)
        c.refresh_from_db()
        self.assertEqual(c.linked_conversation_id, conv)
        self.assertEqual(set(self._members(c)), {self.teacher.id, self.students[0].id, self.students[1].id})

    def test_backfill_creates_missing_and_is_idempotent(self):
        with self.captureOnCommitCallbacks(execute=False):  # old classroom: signal never ran
            old = Classroom.objects.create(teacher=self.teacher, title="Old", chat_group_enabled=False)
        self._join(old, self.students[0])
        out = StringIO()
        call_command("backfill_classroom_groups", "--dry-run", stdout=out)
        old.refresh_from_db()
        self.assertIsNone(old.linked_conversation_id)  # dry-run wrote nothing
        call_command("backfill_classroom_groups", stdout=out)
        old.refresh_from_db()
        self.assertTrue(old.chat_group_enabled)
        self.assertEqual(set(self._members(old)), {self.teacher.id, self.students[0].id})
        conv = old.linked_conversation_id
        call_command("backfill_classroom_groups", stdout=out)
        old.refresh_from_db()
        self.assertEqual(old.linked_conversation_id, conv)
        self.assertEqual(Group.all_objects.filter(conversation_id=conv).count(), 1)


class GroupApiTests(GroupMirrorBase):
    def setUp(self):
        super().setUp()
        self.c = self._classroom()
        self._join(self.c, self.students[0])
        self.client = APIClient()

    def test_open_view_only_for_participants(self):
        self.client.force_authenticate(self.students[0])
        r = self.client.get(f"/tuitionclass/classrooms/{self.c.id}/group/open/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["linked_conversation_id"], str(self.c.linked_conversation_id))
        self.client.force_authenticate(self.students[2])  # outsider
        r = self.client.get(f"/tuitionclass/classrooms/{self.c.id}/group/open/")
        self.assertIsNone(r.data["linked_conversation_id"])

    def test_create_group_view_400_only_when_group_really_exists(self):
        self.client.force_authenticate(self.teacher)
        r = self.client.post(f"/tuitionclass/classrooms/{self.c.id}/create_group/")
        self.assertEqual(r.status_code, 400)  # already auto-created
        Group.objects.filter(conversation_id=self.c.linked_conversation_id).delete()
        r = self.client.post(f"/tuitionclass/classrooms/{self.c.id}/create_group/")
        self.assertEqual(r.status_code, 201)

    def test_retry_and_toggle_permissions(self):
        self.client.force_authenticate(self.students[0])
        self.assertEqual(self.client.post(f"/tuitionclass/classrooms/{self.c.id}/group/retry/").status_code, 403)
        self.assertEqual(
            self.client.post(f"/tuitionclass/classrooms/{self.c.id}/group/toggle/", {"enabled": False}, format="json").status_code, 403,
        )
        self.client.force_authenticate(self.teacher)
        r = self.client.post(f"/tuitionclass/classrooms/{self.c.id}/group/toggle/", {"enabled": False}, format="json")
        self.assertEqual((r.status_code, r.data["chat_group_enabled"], r.data["group_ready"]), (200, False, False))
        r = self.client.post(f"/tuitionclass/classrooms/{self.c.id}/group/toggle/", {"enabled": True}, format="json")
        self.assertEqual((r.data["group_ready"], r.data["in_sync"]), (True, True))
        r = self.client.post(f"/tuitionclass/classrooms/{self.c.id}/group/retry/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["member_count"], r.data["expected_count"])
