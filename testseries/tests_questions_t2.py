# testseries/tests_questions_t2.py
"""
TASK T2 tests — adding / editing / deleting / reordering questions.

Run:  python manage.py test testseries.tests_questions_t2

Covers every question type, every series source (individual / campus / class),
who may edit (creator + the context's teaching staff, nobody else), the
draft-only rule, and the shape of the 400 a client gets back.
"""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

User = get_user_model()

VALID = {
    "mcq": {
        "question_type": "mcq", "text": "Pick one", "marks": 4, "negative_marks": 1,
        "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
        "correct_answer": {"option_id": "a"},
    },
    "msq": {
        "question_type": "msq", "text": "Pick many", "marks": 4,
        "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}, {"id": "c", "text": "C"}],
        "correct_answer": {"option_ids": ["a", "c"]},
    },
    "true_false": {
        "question_type": "true_false", "text": "Sky is blue", "marks": 2,
        "correct_answer": {"value": True},
    },
    "fill_blank": {
        "question_type": "fill_blank", "text": "2 + 2 = ___", "marks": 2,
        "correct_answer": {"answers": ["4", "four"], "case_sensitive": False},
    },
    "numeric": {
        "question_type": "numeric", "text": "pi to 2dp", "marks": 3,
        "correct_answer": {"value": 3.14, "tolerance": 0.01},
    },
    "text": {"question_type": "text", "text": "Explain recursion", "marks": 5},
    "list_match": {
        "question_type": "list", "text": "Match", "marks": 4,
        "options": {
            "left": [{"id": "l1", "text": "Dog"}, {"id": "l2", "text": "Cat"}],
            "right": [{"id": "r1", "text": "Bark"}, {"id": "r2", "text": "Meow"}],
        },
        "correct_answer": {"list_mode": "match", "pairs": {"l1": "r1", "l2": "r2"}},
    },
    "list_order": {
        "question_type": "list", "text": "Order", "marks": 4,
        "options": [{"id": "x", "text": "First"}, {"id": "y", "text": "Second"}],
        "correct_answer": {"list_mode": "order", "sequence": ["x", "y"]},
    },
}


def mk_user(prefix):
    unique = uuid.uuid4().hex[:8]
    try:
        return User.objects.create_user(username=f"{prefix}_{unique}", password="testpass123")
    except TypeError:
        return User.objects.create_user(email=f"{prefix}_{unique}@example.com", password="testpass123")


def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def q_url(series):
    return reverse("testseries-question-list", args=[series.id])


def q_detail(series, question):
    return reverse("testseries-question-detail", args=[series.id, question.id])


def bulk_url(series):
    return reverse("testseries-questions-bulk", args=[series.id])


def reorder_url(series):
    return reverse("testseries-questions-reorder", args=[series.id])


def individual_draft(creator, **extra):
    from testseries.models import TestSeries

    return TestSeries.objects.create(
        creator=creator, title="Draft series", is_paid=True, price_coins=10,
        status=TestSeries.Status.DRAFT, **extra,
    )


# ---------------------------------------------------------------------------
class QuestionTypeCrudTests(TestCase):
    """Every question type: add -> read back (key visible to the creator) ->
    edit -> delete, on an individual draft."""

    def setUp(self):
        self.owner = mk_user("owner")
        self.series = individual_draft(self.owner)
        self.client_ = api(self.owner)

    def test_every_type_can_be_added_edited_and_deleted(self):
        for name, payload in VALID.items():
            with self.subTest(type=name):
                res = self.client_.post(q_url(self.series), payload, format="json")
                self.assertEqual(res.status_code, 201, res.content)
                qid = res.json()["id"]
                if name != "text":
                    self.assertIn("correct_answer", res.json())
                from testseries.models import Question

                question = Question.objects.get(pk=qid)
                res = self.client_.patch(q_detail(self.series, question), {"marks": 9}, format="json")
                self.assertEqual(res.status_code, 200, res.content)
                self.assertEqual(res.json()["marks"], 9)
                res = self.client_.delete(q_detail(self.series, question))
                self.assertEqual(res.status_code, 204, res.content)
        self.series.refresh_from_db()
        self.assertEqual(self.series.total_marks, 0)

    def test_order_is_assigned_when_omitted_and_total_marks_follow(self):
        for _ in range(3):
            res = self.client_.post(q_url(self.series), VALID["mcq"], format="json")
            self.assertEqual(res.status_code, 201, res.content)
        orders = sorted(self.series.questions.values_list("order", flat=True))
        self.assertEqual(orders, [1, 2, 3])
        self.series.refresh_from_db()
        self.assertEqual(self.series.total_marks, 12)

    def test_duplicate_explicit_order_is_a_clean_400(self):
        self.client_.post(q_url(self.series), {**VALID["mcq"], "order": 1}, format="json")
        res = self.client_.post(q_url(self.series), {**VALID["mcq"], "order": 1}, format="json")
        self.assertEqual(res.status_code, 400, res.content)

    def test_junk_shapes_are_400_never_500(self):
        junk = [
            {**VALID["mcq"], "correct_answer": ["a"]},
            {**VALID["mcq"], "correct_answer": {"option_id": ["a"]}},
            {**VALID["msq"], "correct_answer": {"option_ids": 5}},
            {**VALID["msq"], "correct_answer": {"option_ids": [["a"]]}},
            {**VALID["list_order"], "correct_answer": {"list_mode": "order", "sequence": [["x"], ["y"]]}},
            {**VALID["true_false"], "correct_answer": "yes please"},
            {**VALID["numeric"], "correct_answer": {"value": "abc"}},
        ]
        for payload in junk:
            with self.subTest(payload=payload):
                res = self.client_.post(q_url(self.series), payload, format="json")
                self.assertEqual(res.status_code, 400, res.content)


class QuestionAuthorizationTests(TestCase):
    def setUp(self):
        self.owner = mk_user("owner")
        self.stranger = mk_user("stranger")
        self.series = individual_draft(self.owner)

    def test_creator_sees_and_reads_their_own_draft(self):
        res = api(self.owner).get(reverse("testseries-detail", args=[self.series.id]))
        self.assertEqual(res.status_code, 200, res.content)

    def test_stranger_cannot_add_edit_or_delete(self):
        client = api(self.stranger)
        res = client.post(q_url(self.series), VALID["mcq"], format="json")
        self.assertEqual(res.status_code, 403, res.content)
        res = api(self.owner).post(q_url(self.series), VALID["mcq"], format="json")
        question = self.series.questions.get()
        self.assertEqual(client.patch(q_detail(self.series, question), {"marks": 1}, format="json").status_code, 403)
        self.assertEqual(client.delete(q_detail(self.series, question)).status_code, 403)
        # someone else's draft is hidden entirely (404) — its existence is not leaked
        self.assertIn(client.post(bulk_url(self.series), {"questions": [VALID["mcq"]]}, format="json").status_code, (403, 404))

    def test_published_series_gives_a_clean_400(self):
        from testseries.models import TestSeries

        api(self.owner).post(q_url(self.series), VALID["mcq"], format="json")
        TestSeries.objects.filter(pk=self.series.pk).update(status=TestSeries.Status.PUBLISHED)
        res = api(self.owner).post(q_url(self.series), VALID["mcq"], format="json")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("draft", res.json()["detail"].lower())
        res = api(self.owner).post(bulk_url(self.series), {"questions": [VALID["mcq"]]}, format="json")
        self.assertEqual(res.status_code, 400, res.content)


class BulkTests(TestCase):
    def setUp(self):
        self.owner = mk_user("owner")
        self.series = individual_draft(self.owner)
        self.client_ = api(self.owner)

    def test_bulk_creates_every_type(self):
        res = self.client_.post(bulk_url(self.series), {"questions": list(VALID.values())}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["created"], len(VALID))

    def test_bulk_error_body_has_per_question_errors_at_the_top_level(self):
        bad_two = {**VALID["mcq"], "correct_answer": {"option_id": "zzz"}}
        bad_three = {"question_type": "numeric", "text": "n", "marks": 1, "correct_answer": {"value": "x"}}
        res = self.client_.post(
            bulk_url(self.series), {"questions": [VALID["mcq"], bad_two, bad_three]}, format="json"
        )
        self.assertEqual(res.status_code, 400, res.content)
        body = res.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIsInstance(body["errors"], list)
        self.assertEqual([e["index"] for e in body["errors"]], [1, 2])
        self.assertTrue(body["errors"][0]["message"].startswith("Q2:"))
        self.assertIn("Q2", body["detail"])
        self.assertEqual(self.series.questions.count(), 0, "bulk must be all-or-nothing")

    def test_bulk_rejects_non_object_items_and_bad_order(self):
        res = self.client_.post(
            bulk_url(self.series),
            {"questions": ["nope", {**VALID["mcq"], "order": "abc"}, {**VALID["mcq"], "order": [1]}]},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(len(res.json()["errors"]), 3)

    def test_bulk_appends_after_existing_questions(self):
        self.client_.post(q_url(self.series), VALID["mcq"], format="json")
        res = self.client_.post(bulk_url(self.series), {"questions": [VALID["mcq"], VALID["msq"]]}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(sorted(self.series.questions.values_list("order", flat=True)), [1, 2, 3])


class ReorderTests(TestCase):
    def setUp(self):
        self.owner = mk_user("owner")
        self.series = individual_draft(self.owner)
        self.client_ = api(self.owner)
        res = self.client_.post(bulk_url(self.series), {"questions": [VALID["mcq"], VALID["msq"], VALID["numeric"]]}, format="json")
        self.ids = [q["id"] for q in res.json()["questions"]]

    def test_reorder_applies_the_new_order(self):
        new = [self.ids[2], self.ids[0], self.ids[1]]
        res = self.client_.post(reorder_url(self.series), {"order": new}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual([q["id"] for q in res.json()], new)
        self.assertEqual([str(i) for i in self.series.questions.order_by("order").values_list("id", flat=True)], new)

    def test_partial_duplicate_or_foreign_lists_are_rejected(self):
        for bad in ([self.ids[0]], [self.ids[0], self.ids[0], self.ids[1]], self.ids + [str(uuid.uuid4())], ["x"], []):
            with self.subTest(bad=bad):
                res = self.client_.post(reorder_url(self.series), {"order": bad}, format="json")
                self.assertEqual(res.status_code, 400, res.content)

    def test_stranger_is_forbidden(self):
        res = api(mk_user("s")).post(reorder_url(self.series), {"order": self.ids}, format="json")
        self.assertIn(res.status_code, (403, 404), res.content)
        self.assertEqual(self.series.questions.order_by("order").first().order, 1)


# ---------------------------------------------------------------------------
class CampusEditorTests(TestCase):
    """Campus series: created as a DRAFT by the section's staff, questions added
    by any of the section's teaching staff, never by students / outsiders."""

    def setUp(self):
        from datetime import date

        from campus.models import (
            AcademicSession, Campus, ClassTeacherassigments, SchoolClass, Section, StaffProfile,
            StudentEnrollment, Subject, SubjectTeacherassigments,
        )

        self.admin = mk_user("admin")
        self.class_teacher = mk_user("ct")
        self.subject_teacher = mk_user("st")
        self.pending_teacher = mk_user("pending")
        self.other_staff = mk_user("otherstaff")
        self.student = mk_user("student")
        self.outsider = mk_user("outsider")

        self.campus = Campus.objects.create(name="Green Valley", created_by=self.admin)
        StaffProfile.objects.create(campus=self.campus, user=self.admin, role=StaffProfile.Role.ADMIN)
        session = AcademicSession.objects.create(
            campus=self.campus, name="2026-27", start_date=date(2026, 6, 1),
            end_date=date(2027, 4, 30), is_current=True,
        )
        school_class = SchoolClass.objects.create(campus=self.campus, session=session, name="Class 10")
        self.section = Section.objects.create(school_class=school_class, name="A")
        other_section = Section.objects.create(school_class=school_class, name="B")
        self.subject = Subject.objects.create(campus=self.campus, name="Maths")

        ct = StaffProfile.objects.create(campus=self.campus, user=self.class_teacher, role=StaffProfile.Role.CLASS_TEACHER)
        ClassTeacherassigments.objects.create(section=self.section, staff=ct)
        st = StaffProfile.objects.create(campus=self.campus, user=self.subject_teacher, role=StaffProfile.Role.SUBJECT_TEACHER)
        SubjectTeacherassigments.objects.create(
            section=self.section, subject=self.subject, staff=st, status=SubjectTeacherassigments.Status.APPROVED,
        )
        pt = StaffProfile.objects.create(campus=self.campus, user=self.pending_teacher, role=StaffProfile.Role.SUBJECT_TEACHER)
        SubjectTeacherassigments.objects.create(
            section=self.section, subject=self.subject, staff=pt, status=SubjectTeacherassigments.Status.PENDING,
        )
        os_ = StaffProfile.objects.create(campus=self.campus, user=self.other_staff, role=StaffProfile.Role.SUBJECT_TEACHER)
        SubjectTeacherassigments.objects.create(
            section=other_section, subject=self.subject, staff=os_, status=SubjectTeacherassigments.Status.APPROVED,
        )
        StudentEnrollment.objects.create(student=self.student, section=self.section, session=session)

    def _create_draft(self, user=None, **extra):
        body = {"section": self.section.id, "subject": self.subject.id, "title": "Unit test", "draft": True, **extra}
        return api(user or self.class_teacher).post(reverse("campus-test-series-list"), body, format="json")

    def test_class_teacher_creates_an_empty_draft_and_it_is_not_announced(self):
        res = self._create_draft()
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["status"], "draft")
        self.assertEqual(res.json()["total_marks"], 0)

    def test_without_draft_questions_are_still_required(self):
        body = {"section": self.section.id, "subject": self.subject.id, "title": "x"}
        res = api(self.class_teacher).post(reverse("campus-test-series-list"), body, format="json")
        self.assertEqual(res.status_code, 400, res.content)

    def test_bad_question_on_campus_create_is_400_with_a_list_not_500(self):
        body = {
            "section": self.section.id, "subject": self.subject.id, "title": "x",
            "questions": [{**VALID["mcq"], "order": 1}, {**VALID["mcq"], "order": 2, "correct_answer": {"option_id": "zzz"}}],
        }
        res = api(self.class_teacher).post(reverse("campus-test-series-list"), body, format="json")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()["errors"][0]["index"], 1)
        from testseries.models import TestSeries

        self.assertFalse(TestSeries.objects.filter(title="x").exists(), "nothing may be saved on a bad payload")

    def test_staff_who_may_edit_can_add_questions_and_see_the_key(self):
        from testseries.models import TestSeries

        series = TestSeries.objects.get(pk=self._create_draft().json()["id"])
        for who in (self.class_teacher, self.subject_teacher, self.admin):
            with self.subTest(who=who.username):
                res = api(who).post(q_url(series), VALID["mcq"], format="json")
                self.assertEqual(res.status_code, 201, res.content)
                self.assertIn("correct_answer", res.json())
                res = api(who).get(q_url(series))
                self.assertEqual(res.status_code, 200, res.content)
                rows = res.json()["results"] if isinstance(res.json(), dict) else res.json()
                self.assertTrue(all("correct_answer" in r for r in rows))

    def test_bulk_by_a_non_creator_editor(self):
        from testseries.models import TestSeries

        series = TestSeries.objects.get(pk=self._create_draft().json()["id"])
        res = api(self.subject_teacher).post(bulk_url(series), {"questions": [VALID["mcq"], VALID["numeric"]]}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(series.questions.count(), 2)

    def test_everyone_else_is_refused(self):
        from testseries.models import TestSeries

        series = TestSeries.objects.get(pk=self._create_draft().json()["id"])
        for who in (self.student, self.outsider, self.pending_teacher, self.other_staff):
            with self.subTest(who=who.username):
                res = api(who).post(q_url(series), VALID["mcq"], format="json")
                self.assertEqual(res.status_code, 403, res.content)
                res = api(who).post(bulk_url(series), {"questions": [VALID["mcq"]]}, format="json")
                self.assertIn(res.status_code, (403, 404), res.content)

    def test_editor_cannot_publish_or_edit_the_series_itself(self):
        from testseries.models import TestSeries

        series = TestSeries.objects.get(pk=self._create_draft().json()["id"])
        res = api(self.subject_teacher).patch(reverse("testseries-detail", args=[series.id]), {"title": "hijack"}, format="json")
        self.assertEqual(res.status_code, 403, res.content)
        res = api(self.subject_teacher).post(reverse("testseries-publish", args=[series.id]))
        self.assertEqual(res.status_code, 403, res.content)

    def test_students_never_see_a_draft_but_do_see_it_once_published(self):
        from testseries.models import TestSeries

        series = TestSeries.objects.get(pk=self._create_draft().json()["id"])
        api(self.class_teacher).post(q_url(series), VALID["mcq"], format="json")

        listed = api(self.student).get(reverse("campus-test-series-list")).json()
        self.assertNotIn(str(series.id), [str(row["id"]) for row in listed])
        res = api(self.student).get(reverse("campus-test-series-detail", args=[series.id]))
        self.assertEqual(res.status_code, 403, res.content)

        listed = api(self.class_teacher).get(reverse("campus-test-series-list")).json()
        self.assertIn(str(series.id), [str(row["id"]) for row in listed])

        res = api(self.class_teacher).post(reverse("testseries-publish", args=[series.id]))
        self.assertEqual(res.status_code, 200, res.content)
        listed = api(self.student).get(reverse("campus-test-series-list")).json()
        self.assertIn(str(series.id), [str(row["id"]) for row in listed])
        res = api(self.student).get(reverse("campus-test-series-detail", args=[series.id]))
        self.assertEqual(res.status_code, 200, res.content)

    def test_after_publish_adding_is_a_clean_400_even_for_editors(self):
        from testseries.models import TestSeries

        series = TestSeries.objects.get(pk=self._create_draft().json()["id"])
        api(self.class_teacher).post(q_url(series), VALID["mcq"], format="json")
        api(self.class_teacher).post(reverse("testseries-publish", args=[series.id]))
        res = api(self.subject_teacher).post(q_url(series), VALID["mcq"], format="json")
        self.assertEqual(res.status_code, 400, res.content)


# ---------------------------------------------------------------------------
class ClassroomEditorTests(TestCase):
    """Tuition-class series: the teacher, co-teachers and moderators edit the
    questions; teaching assistants, students and outsiders do not."""

    def setUp(self):
        from testseries.bridge import create_context_testseries
        from testseries.models import TestSeries
        from tuitionclass.models import Classroom, ClassroomStaff

        self.teacher = mk_user("teacher")
        self.co_teacher = mk_user("co")
        self.moderator = mk_user("mod")
        self.ta = mk_user("ta")
        self.outsider = mk_user("outsider")
        self.classroom = Classroom.objects.create(teacher=self.teacher, title="DSA Batch")
        ClassroomStaff.objects.create(classroom=self.classroom, user=self.co_teacher, role=ClassroomStaff.Role.CO_TEACHER)
        ClassroomStaff.objects.create(classroom=self.classroom, user=self.moderator, role=ClassroomStaff.Role.MODERATOR)
        ClassroomStaff.objects.create(classroom=self.classroom, user=self.ta, role=ClassroomStaff.Role.TA)
        self.series = create_context_testseries(
            source=TestSeries.Source.TUITIONCLASS, context_type="classroom", context_id=self.classroom.id,
            creator=self.teacher, title="Weekly test", draft=True,
        )

    def test_draft_is_created_empty_and_draft(self):
        self.assertEqual(self.series.status, "draft")
        self.assertEqual(self.series.questions.count(), 0)

    def test_teacher_co_teacher_and_moderator_can_add(self):
        for who in (self.teacher, self.co_teacher, self.moderator):
            with self.subTest(who=who.username):
                res = api(who).post(q_url(self.series), VALID["msq"], format="json")
                self.assertEqual(res.status_code, 201, res.content)
                self.assertIn("correct_answer", res.json())
        self.assertEqual(self.series.questions.count(), 3)

    def test_ta_student_and_outsider_cannot(self):
        for who in (self.ta, self.outsider):
            with self.subTest(who=who.username):
                res = api(who).post(q_url(self.series), VALID["mcq"], format="json")
                self.assertEqual(res.status_code, 403, res.content)

    def test_co_teacher_can_edit_delete_and_reorder_but_not_publish(self):
        client = api(self.co_teacher)
        ids = [r["id"] for r in client.post(bulk_url(self.series), {"questions": [VALID["mcq"], VALID["numeric"]]}, format="json").json()["questions"]]
        question = self.series.questions.get(pk=ids[0])
        self.assertEqual(client.patch(q_detail(self.series, question), {"marks": 7}, format="json").status_code, 200)
        self.assertEqual(client.post(reorder_url(self.series), {"order": ids[::-1]}, format="json").status_code, 200)
        self.assertEqual(client.delete(q_detail(self.series, question)).status_code, 204)
        self.assertEqual(client.post(reverse("testseries-publish", args=[self.series.id])).status_code, 403)

    def test_resolver_is_fail_closed_when_misconfigured(self):
        from django.test import override_settings

        with override_settings(TESTSERIES_CONTEXT_EDITORS={"classroom": "does.not.exist"}):
            res = api(self.co_teacher).post(q_url(self.series), VALID["mcq"], format="json")
            self.assertIn(res.status_code, (403, 404), res.content)
            # the creator is never locked out of their own series
            self.assertEqual(api(self.teacher).post(q_url(self.series), VALID["mcq"], format="json").status_code, 201)
