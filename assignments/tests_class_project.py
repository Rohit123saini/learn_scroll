# assignments/tests_class_project.py
"""
A CLASS (source=tuitionclass) project end to end through the unified API - the path the Flutter class project screen
uses: the hand-in row already exists (status=missing) from posting, the student PATCHes submit_freeform with a link,
the poster grades it per rubric (grade-rubric) or with plain marks (grade).

Run:  python manage.py test assignments.tests_class_project
Written WITHOUT a Django runtime - the first run is the real review (same convention as tests_advanced.py).
"""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .bridge import create_context_assigments
from .models import assigmentsSource, assigmentsSubmission

User = get_user_model()
RUBRIC = [{"criterion": "Design", "max_marks": 10}, {"criterion": "Code", "max_marks": 20}]


def _user(prefix):
    unique = uuid.uuid4().hex[:8]
    try:
        return User.objects.create_user(username=f"{prefix}_{unique}", password="testpass123")
    except TypeError:
        return User.objects.create_user(email=f"{prefix}_{unique}@example.com", password="testpass123")


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


class ClassProjectFlowTests(TestCase):
    def setUp(self):
        self.teacher, self.student, self.other = _user("teacher"), _user("student"), _user("other")

    def _post(self, **kw):
        return create_context_assigments(
            source=assigmentsSource.TUITIONCLASS, context_type="classroom", context_id=1,
            posted_by=self.teacher, title="Weather app", roster=[{"user_id": self.student.id}], **kw,
        )

    def _row(self, assignment):
        return assigmentsSubmission.objects.get(assigments=assignment, student=self.student)

    def test_row_exists_missing_and_only_the_student_and_poster_can_see_it(self):
        a = self._post(kind="project", rubric=RUBRIC, submission_types=["link"])
        row = self._row(a)
        self.assertEqual(row.status, "missing")
        url = reverse("assigments-submission-detail", kwargs={"pk": row.pk})
        self.assertEqual(_client(self.student).get(url).status_code, 200)
        self.assertEqual(_client(self.teacher).get(url).status_code, 200)
        self.assertEqual(_client(self.other).get(url).status_code, 404)

    def test_link_handin_then_rubric_grade(self):
        a = self._post(kind="project", rubric=RUBRIC, submission_types=["link"])
        row = self._row(a)
        hand_in = reverse("assigments-submission-submit-freeform", kwargs={"pk": row.pk})

        # type rules of the class project: only a link is accepted
        self.assertEqual(_client(self.student).patch(hand_in, {"written_content": "text"}, format="json").status_code, 400)
        self.assertEqual(_client(self.student).patch(hand_in, {"link_url": "javascript:alert(1)"}, format="json").status_code, 400)
        self.assertEqual(_client(self.other).patch(hand_in, {"link_url": "https://example.com"}, format="json").status_code, 404)
        ok = _client(self.student).patch(hand_in, {"link_url": "https://github.com/me/weather"}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual((ok.json()["status"], ok.json()["link_url"]), ("submitted", "https://github.com/me/weather"))

        grade = reverse("assigments-submission-grade-rubric", kwargs={"pk": row.pk})
        self.assertEqual(_client(self.student).patch(grade, {"scores": {"Design": 9, "Code": 18}}, format="json").status_code, 403)
        self.assertEqual(_client(self.teacher).patch(grade, {"scores": {"Design": 99, "Code": 1}}, format="json").status_code, 400)
        self.assertEqual(_client(self.teacher).patch(grade, {"scores": {"Design": 9}}, format="json").status_code, 400)
        res = _client(self.teacher).patch(grade, {"scores": {"Design": 9, "Code": 18}, "feedback": "Nice"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual((body["status"], body["total_marks_awarded"], body["grade"]), ("checked", 27, "27/30"))
        self.assertEqual(body["rubric_scores"], {"Design": 9, "Code": 18})

    def test_plain_class_assignment_graded_with_marks(self):
        a = self._post()  # no rubric, no kind
        row = self._row(a)
        _client(self.student).patch(
            reverse("assigments-submission-submit-freeform", kwargs={"pk": row.pk}), {"written_content": "my answer"}, format="json"
        )
        grade = reverse("assigments-submission-grade", kwargs={"pk": row.pk})
        self.assertEqual(_client(self.student).patch(grade, {"grade": "9/10"}, format="json").status_code, 403)
        res = _client(self.teacher).patch(grade, {"grade": "9/10", "feedback": "Good"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status"], "checked")
        # a rubric grade on an assignment without a rubric is refused
        self.assertEqual(
            _client(self.teacher).patch(reverse("assigments-submission-grade-rubric", kwargs={"pk": row.pk}), {"scores": {"x": 1}}, format="json").status_code,
            400,
        )
