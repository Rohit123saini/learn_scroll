"""P-F11 tests for GET /tuitionclass/classrooms/editable/ (written, NOT RUN)."""
from django.urls import reverse
from rest_framework.test import APIClient

from login.models import User

from . import tests as base_tests  # module import: its test classes are NOT re-collected here
from .bridge import editable_classroom_roles, user_editable_testseries_context_ids
from .models import Classroom, ClassroomStaff


class EditableClassesTests(base_tests.TuitionClassTestBase):
    def setUp(self):
        super().setUp()
        self.url = reverse("classroom-editable")
        self.co = User.objects.create_user(username="co1", password="pass12345", email="co1@example.com")
        self.mod = User.objects.create_user(username="mod1", password="pass12345", email="mod1@example.com")
        self.ta = User.objects.create_user(username="ta1", password="pass12345", email="ta1@example.com")
        ClassroomStaff.objects.create(classroom=self.classroom, user=self.co, role=ClassroomStaff.Role.CO_TEACHER)
        ClassroomStaff.objects.create(classroom=self.classroom, user=self.mod, role=ClassroomStaff.Role.MODERATOR)
        ClassroomStaff.objects.create(classroom=self.classroom, user=self.ta, role=ClassroomStaff.Role.TA)

    def _get(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c.get(self.url)

    def _rows(self, r):
        self.assertEqual(r.status_code, 200)
        return r.data["results"] if isinstance(r.data, dict) and "results" in r.data else r.data

    def test_requires_login(self):
        self.assertIn(APIClient().get(self.url).status_code, (401, 403))

    def test_owner_sees_own_class_with_teacher_role(self):
        rows = self._rows(self._get(self.teacher))
        self.assertEqual([x["id"] for x in rows], [self.classroom.id])
        self.assertEqual(rows[0]["my_role"], "teacher")

    def test_co_teacher_and_moderator_see_the_class(self):
        for user, role in ((self.co, "co_teacher"), (self.mod, "moderator")):
            rows = self._rows(self._get(user))
            self.assertEqual([x["id"] for x in rows], [self.classroom.id])
            self.assertEqual(rows[0]["my_role"], role)

    def test_ta_student_and_stranger_get_nothing(self):
        for user in (self.ta, self.student, self.other_teacher):
            ids = [x["id"] for x in self._rows(self._get(user))]
            self.assertNotIn(self.classroom.id, ids)

    def test_other_teachers_own_class_is_listed_only_for_them(self):
        rows = self._rows(self._get(self.other_teacher))
        self.assertEqual([x["id"] for x in rows], [self.other_classroom.id])

    def test_soft_deleted_class_is_hidden(self):
        Classroom.objects.filter(pk=self.classroom.pk).update(is_deleted=True)
        self.assertEqual(self._rows(self._get(self.teacher)), [])
        self.assertEqual(self._rows(self._get(self.co)), [])

    def test_owner_role_wins_over_staff_row(self):
        ClassroomStaff.objects.create(classroom=self.other_classroom, user=self.other_teacher,
                                      role=ClassroomStaff.Role.MODERATOR)
        self.assertEqual(editable_classroom_roles(self.other_teacher)[self.other_classroom.id], "teacher")

    def test_resolver_and_list_agree(self):
        import uuid
        ids = {uuid.UUID(int=pk) for pk in editable_classroom_roles(self.co)}
        self.assertEqual(ids, user_editable_testseries_context_ids(user=self.co, context_type="classroom"))
