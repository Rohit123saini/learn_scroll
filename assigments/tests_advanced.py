# assigments/tests_advanced.py
"""
API tests for: regression of the two `assigments = assigments.objects…`
crash bugs (personal create via API, campus/liveclass create via bridge), the
missing public-page throttle rate, publishing / share link / Explore / join,
project hand-ins (link + rubric grading) and CSV question import.

Run:  python manage.py test assigments.tests_advanced

⚠️ Authored WITHOUT a Django runtime available — the first run is the real
review. `_make_user()` is the one seam to adapt if your `User` needs other
required fields (same convention as assigments/tests.py).
"""
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .bridge import create_context_assigments
from .models import (
    assigments, assigmentsQuestion, assigmentsSource, assigmentsStatus, assigmentsSubmission,
    assigmentsVisibility,
)

User = get_user_model()


def _make_user(prefix="user"):
    unique = uuid.uuid4().hex[:8]
    try:
        return User.objects.create_user(username=f"{prefix}_{unique}", password="testpass123")
    except TypeError:
        return User.objects.create_user(email=f"{prefix}_{unique}@example.com", password="testpass123")


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def _rows(response):
    body = response.json()
    return body["results"] if isinstance(body, dict) and "results" in body else body


class _Base(TestCase):
    def setUp(self):
        self.poster = _make_user("poster")
        self.student = _make_user("student")
        self.other = _make_user("other")

    def create(self, **payload):
        payload.setdefault("title", "Build a weather app")
        payload.setdefault("description", "Use any public weather API.")
        res = _client(self.poster).post(reverse("assigments-list"), payload, format="json")
        assert res.status_code == 201, res.content
        return assigments.objects.get(pk=res.json()["id"])

    def publish(self, assignment, visibility="public", user=None):
        return _client(user or self.poster).post(
            reverse("assigments-publish", kwargs={"pk": assignment.pk}), {"visibility": visibility}, format="json"
        )


class RegressionTests(_Base):
    def test_personal_assignment_can_be_created_through_the_api(self):
        """Was UnboundLocalError on every POST (local var shadowed the model class)."""
        assignment = self.create()
        self.assertEqual(assignment.source, assigmentsSource.PERSONAL)
        self.assertEqual(assignment.posted_by, self.poster)
        self.assertEqual(assignment.status, assigmentsStatus.DRAFT)          # starts private
        self.assertEqual(assignment.visibility, assigmentsVisibility.PRIVATE)

    def test_structured_assignment_with_questions_is_created(self):
        res = _client(self.poster).post(
            reverse("assigments-list"),
            {
                "title": "Quiz", "has_structured_questions": True,
                "questions": [{
                    "order": 1, "question_type": "mcq", "text": "2+2?", "marks": 2,
                    "options": [{"id": "a", "text": "3"}, {"id": "b", "text": "4"}],
                    "correct_answer": {"option_id": "b"},
                }],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(assigments.objects.get(pk=res.json()["id"]).total_marks, 2)

    def test_campus_style_assignment_is_created_through_the_bridge(self):
        """Was UnboundLocalError in create_context_assigments()."""
        assignment = create_context_assigments(
            source=assigmentsSource.CAMPUS, context_type="section", context_id=uuid.uuid4(),
            posted_by=self.poster, title="Chapter 3 worksheet",
            roster=[
                {"user_id": self.student.id, "roll_number": "12", "enrollment_no": "E12"},
                {"user_id": self.other.id, "roll_number": "13", "enrollment_no": "E13"},
            ],
        )
        self.assertEqual(assignment.submissions.count(), 2)
        self.assertTrue(assignment.submissions.filter(student=self.student, roll_number="12").exists())

    def test_public_submission_share_page_does_not_crash_on_a_missing_throttle_rate(self):
        """`assigments_public_page` had no DEFAULT_THROTTLE_RATES entry -> 500 on first hit."""
        assignment = self.create()
        submission = assigmentsSubmission.objects.create(assigments=assignment, student=self.poster)
        slug = submission.publish()
        res = _client().get(reverse("assigments-public-submission", kwargs={"slug": slug}))
        self.assertEqual(res.status_code, 200, res.content)


class PublishingTests(_Base):
    def test_nothing_to_do_yet_blocks_publish(self):
        assignment = assigments.objects.create(
            source=assigmentsSource.PERSONAL, posted_by=self.poster, title="Empty",
            status=assigmentsStatus.DRAFT,
        )
        res = self.publish(assignment)
        self.assertEqual(res.status_code, 400)
        self.assertTrue(res.json()["problems"])

    def test_publish_makes_it_public_and_mints_a_stable_slug(self):
        assignment = self.create()
        res = self.publish(assignment)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual((body["status"], body["visibility"]), ("published", "public"))
        self.assertTrue(body["public_slug"])
        self.assertIn(body["public_slug"], body["share_url"])

        again = self.publish(assignment, visibility="link").json()
        self.assertEqual(again["public_slug"], body["public_slug"])   # link you posted stays valid
        self.assertEqual(again["visibility"], "link")

    def test_only_the_poster_can_publish(self):
        assignment = self.create()
        self.publish(assignment)  # public now, so `other` can open it
        res = self.publish(assignment, user=self.other)
        self.assertEqual(res.status_code, 403)

    def test_structured_assignment_needs_its_answer_key(self):
        assignment = self.create(has_structured_questions=True, questions=[{
            "order": 1, "question_type": "mcq", "text": "Q", "marks": 1,
            "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], "correct_answer": {"option_id": "a"},
        }])
        assigmentsQuestion.objects.filter(assigments=assignment).update(correct_answer={})
        res = self.publish(assignment)
        self.assertEqual(res.status_code, 400)
        self.assertTrue(any("answer key" in p for p in res.json()["problems"]))

    def test_a_past_due_date_blocks_publish(self):
        assignment = self.create(due_date=(timezone.localdate() - timedelta(days=2)).isoformat())
        self.assertEqual(self.publish(assignment).status_code, 400)

    def test_unpublish_hides_it_but_keeps_participants_work(self):
        assignment = self.create()
        self.publish(assignment)
        joined = _client(self.student).post(reverse("assigments-join", kwargs={"pk": assignment.pk}))
        self.assertEqual(joined.status_code, 201)

        res = _client(self.poster).post(reverse("assigments-unpublish", kwargs={"pk": assignment.pk}))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["visibility"], "private")
        self.assertEqual(_client().get(
            reverse("assigments-public-assignment", kwargs={"slug": assignment.public_slug})).status_code, 404)
        self.assertTrue(assigmentsSubmission.objects.filter(assigments=assignment, student=self.student).exists())
        # The participant can still open what they joined.
        self.assertEqual(_client(self.student).get(reverse("assigments-detail", kwargs={"pk": assignment.pk})).status_code, 200)


class ExploreAndJoinTests(_Base):
    def test_explore_lists_only_published_public_assignments(self):
        public = self.create(title="Public one")
        self.publish(public)
        unlisted = self.create(title="Unlisted")
        self.publish(unlisted, visibility="link")
        self.create(title="Still a draft")

        res = _client(self.student).get(reverse("assigments-explore"))
        self.assertEqual(res.status_code, 200, res.content)
        titles = [r["title"] for r in _rows(res)]
        self.assertEqual(titles, ["Public one"])
        row = _rows(res)[0]
        self.assertNotIn("questions", row)
        self.assertIn("poster_name", row)

    def test_explore_search_and_kind_filters(self):
        self.publish(self.create(title="Weather app", kind="project"))
        self.publish(self.create(title="Grammar worksheet", description="Nouns and verbs"))
        rows = _rows(_client(self.student).get(reverse("assigments-explore"), {"search": "grammar"}))
        self.assertEqual([r["title"] for r in rows], ["Grammar worksheet"])
        rows = _rows(_client(self.student).get(reverse("assigments-explore"), {"kind": "project"}))
        self.assertEqual([r["title"] for r in rows], ["Weather app"])

    @skipUnlessDBFeature("supports_json_field_contains")
    def test_explore_tag_filter(self):
        self.publish(self.create(title="Tagged", tags=["Python", "APIs"]))
        self.publish(self.create(title="Other", tags=["design"]))
        rows = _rows(_client(self.student).get(reverse("assigments-explore"), {"tag": "python"}))
        self.assertEqual([r["title"] for r in rows], ["Tagged"])

    def test_explore_popular_ordering(self):
        quiet = self.create(title="Quiet")
        busy = self.create(title="Busy")
        self.publish(quiet)
        self.publish(busy)
        _client(self.student).post(reverse("assigments-join", kwargs={"pk": busy.pk}))
        _client(self.other).post(reverse("assigments-join", kwargs={"pk": busy.pk}))
        rows = _rows(_client(self.student).get(reverse("assigments-explore"), {"ordering": "popular"}))
        self.assertEqual([r["title"] for r in rows], ["Busy", "Quiet"])
        self.assertEqual(rows[0]["participants_count"], 2)

    def test_join_creates_a_submission_and_is_idempotent(self):
        assignment = self.create()
        self.publish(assignment)
        url = reverse("assigments-join", kwargs={"pk": assignment.pk})
        first = _client(self.student).post(url)
        second = _client(self.student).post(url)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(first.json()["status"], "missing")
        self.assertEqual(assigmentsSubmission.objects.filter(assigments=assignment, student=self.student).count(), 1)

    def test_private_and_draft_assignments_cannot_be_joined(self):
        assignment = self.create()  # draft + private
        res = _client(self.student).post(reverse("assigments-join", kwargs={"pk": assignment.pk}))
        self.assertEqual(res.status_code, 404)
        # ...nor sneaked in through the raw submissions endpoint (IDOR).
        res = _client(self.student).post(
            reverse("assigments-submission-list"), {"assigments": str(assignment.pk)}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(assigmentsSubmission.objects.filter(assigments=assignment, student=self.student).exists())

    def test_a_participant_cannot_edit_or_delete_the_posters_assignment(self):
        """`get_queryset()` includes assignments you merely hold a submission for —
        writes were never restricted to the poster."""
        assignment = self.create()
        self.publish(assignment)
        _client(self.student).post(reverse("assigments-join", kwargs={"pk": assignment.pk}))
        url = reverse("assigments-detail", kwargs={"pk": assignment.pk})
        self.assertEqual(_client(self.student).patch(url, {"title": "Hacked"}, format="json").status_code, 403)
        self.assertEqual(_client(self.student).delete(url).status_code, 403)
        self.assertEqual(_client(self.poster).patch(url, {"title": "Mine"}, format="json").status_code, 200)

    def test_the_plain_list_stays_mine_plus_joined(self):
        assignment = self.create(title="Public one")
        self.publish(assignment)
        self.assertEqual(_rows(_client(self.student).get(reverse("assigments-list"))), [])  # not joined yet
        _client(self.student).post(reverse("assigments-join", kwargs={"pk": assignment.pk}))
        self.assertEqual(len(_rows(_client(self.student).get(reverse("assigments-list")))), 1)

    def test_answer_keys_are_hidden_from_participants(self):
        assignment = self.create(has_structured_questions=True, questions=[{
            "order": 1, "question_type": "mcq", "text": "Q", "marks": 1,
            "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], "correct_answer": {"option_id": "a"},
        }])
        self.publish(assignment)
        res = _client(self.student).get(reverse("assigments-detail", kwargs={"pk": assignment.pk}))
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("correct_answer", res.json()["questions"][0])
        mine = _client(self.poster).get(reverse("assigments-detail", kwargs={"pk": assignment.pk})).json()
        self.assertIn("correct_answer", mine["questions"][0])


class PublicShareLinkTests(_Base):
    def test_anonymous_preview_by_slug_has_no_questions(self):
        assignment = self.create(has_structured_questions=True, questions=[{
            "order": 1, "question_type": "mcq", "text": "Secret question", "marks": 1,
            "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], "correct_answer": {"option_id": "a"},
        }])
        slug = self.publish(assignment, visibility="link").json()["public_slug"]
        res = _client().get(reverse("assigments-public-assignment", kwargs={"slug": slug}))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["question_count"], 1)
        self.assertNotIn("Secret question", res.content.decode())

    def test_private_assignment_has_no_public_page(self):
        assignment = self.create()
        assignment.public_slug = "privateslug"
        assignment.save(update_fields=["public_slug"])
        res = _client().get(reverse("assigments-public-assignment", kwargs={"slug": "privateslug"}))
        self.assertEqual(res.status_code, 404)


class ProjectTests(_Base):
    RUBRIC = [{"criterion": "Design", "max_marks": 10}, {"criterion": "Code", "max_marks": 20}]

    def project(self, **kw):
        return self.create(kind="project", rubric=self.RUBRIC, **kw)

    def test_rubric_sets_total_marks_and_projects_default_to_all_hand_in_types(self):
        project = self.project()
        self.assertEqual(project.total_marks, 30)
        self.assertEqual(set(project.submission_types), {"text", "file", "link"})

    def test_bad_rubrics_are_rejected(self):
        for rubric in ([{"criterion": "A", "max_marks": 0}], [{"criterion": "A", "max_marks": 5}] * 2, "nope"):
            res = _client(self.poster).post(
                reverse("assigments-list"), {"title": "P", "kind": "project", "rubric": rubric}, format="json"
            )
            self.assertEqual(res.status_code, 400, rubric)

    def test_link_handin_and_type_restrictions(self):
        project = self.project(submission_types=["link"])
        self.publish(project)
        submission_id = _client(self.student).post(reverse("assigments-join", kwargs={"pk": project.pk})).json()["id"]
        url = reverse("assigments-submission-submit-freeform", kwargs={"pk": submission_id})

        ok = _client(self.student).patch(url, {"link_url": "https://github.com/me/weather"}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()["link_url"], "https://github.com/me/weather")
        self.assertEqual(ok.json()["status"], "submitted")

        self.assertEqual(_client(self.student).patch(url, {"written_content": "text"}, format="json").status_code, 400)
        self.assertEqual(_client(self.student).patch(url, {"link_url": "javascript:alert(1)"}, format="json").status_code, 400)

    def test_rubric_grading(self):
        project = self.project()
        self.publish(project)
        submission_id = _client(self.student).post(reverse("assigments-join", kwargs={"pk": project.pk})).json()["id"]
        _client(self.student).patch(
            reverse("assigments-submission-submit-freeform", kwargs={"pk": submission_id}),
            {"link_url": "https://example.com/demo"}, format="json",
        )
        grade_url = reverse("assigments-submission-grade-rubric", kwargs={"pk": submission_id})

        self.assertEqual(_client(self.student).patch(grade_url, {"scores": {"Design": 9, "Code": 18}}, format="json").status_code, 403)
        self.assertEqual(_client(self.poster).patch(grade_url, {"scores": {"Design": 99, "Code": 1}}, format="json").status_code, 400)
        self.assertEqual(_client(self.poster).patch(grade_url, {"scores": {"Design": 9}}, format="json").status_code, 400)  # missing Code
        self.assertEqual(_client(self.poster).patch(grade_url, {"scores": {"Design": 9, "Nope": 1}}, format="json").status_code, 400)

        res = _client(self.poster).patch(
            grade_url, {"scores": {"Design": 9, "Code": 18}, "feedback": "Nice work"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual((body["status"], body["total_marks_awarded"], body["grade"]), ("checked", 27, "27/30"))
        self.assertEqual(body["rubric_scores"], {"Design": 9, "Code": 18})

    def test_grading_needs_a_rubric(self):
        plain = self.create()
        self.publish(plain)
        submission_id = _client(self.student).post(reverse("assigments-join", kwargs={"pk": plain.pk})).json()["id"]
        res = _client(self.poster).patch(
            reverse("assigments-submission-grade-rubric", kwargs={"pk": submission_id}),
            {"scores": {"x": 1}}, format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_poster_can_filter_submissions_by_assignment(self):
        project = self.project()
        self.publish(project)
        _client(self.student).post(reverse("assigments-join", kwargs={"pk": project.pk}))
        _client(self.other).post(reverse("assigments-join", kwargs={"pk": project.pk}))
        res = _client(self.poster).get(reverse("assigments-submission-list"), {"assigments": str(project.pk)})
        self.assertEqual(len(_rows(res)), 2)


class QuestionImportTests(_Base):
    CSV = "question,option_a,option_b,correct,marks\nTwo plus two?,3,4,B,2\nCapital of India?,Delhi,Pune,A,3\n"

    def structured(self):
        return self.create(has_structured_questions=True, questions=[{
            "order": 1, "question_type": "mcq", "text": "Seed", "marks": 1,
            "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], "correct_answer": {"option_id": "a"},
        }])

    def test_csv_import_appends_questions_with_the_answer_key(self):
        assignment = self.structured()
        upload = SimpleUploadedFile("q.csv", self.CSV.encode(), content_type="text/csv")
        res = _client(self.poster).post(
            reverse("assigments-questions-import", kwargs={"pk": assignment.pk}), {"file": upload}, format="multipart"
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["created"], 2)
        self.assertEqual(res.json()["total_marks"], 6)  # 1 + 2 + 3
        self.assertEqual(assignment.questions.get(order=2).correct_answer, {"option_id": "b"})

    def test_import_is_refused_once_someone_has_started(self):
        assignment = self.structured()
        self.publish(assignment)
        _client(self.student).post(reverse("assigments-join", kwargs={"pk": assignment.pk}))
        assigmentsSubmission.objects.filter(assigments=assignment).update(status="submitted")
        upload = SimpleUploadedFile("q.csv", self.CSV.encode(), content_type="text/csv")
        res = _client(self.poster).post(
            reverse("assigments-questions-import", kwargs={"pk": assignment.pk}), {"file": upload}, format="multipart"
        )
        self.assertEqual(res.status_code, 400)

    def test_bad_csv_imports_nothing(self):
        assignment = self.structured()
        upload = SimpleUploadedFile("q.csv", b"question,option_a,option_b,correct\nQ?,x,y,\n", content_type="text/csv")
        res = _client(self.poster).post(
            reverse("assigments-questions-import", kwargs={"pk": assignment.pk}), {"file": upload}, format="multipart"
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(assignment.questions.count(), 1)
