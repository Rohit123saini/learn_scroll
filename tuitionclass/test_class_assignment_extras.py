# Class assignment extras (Project type / tags / difficulty / rubric) - WRITTEN BY READING THE CODE, not run.
# Pure-function tests only (no DB): `python manage.py test tuitionclass.test_class_assignment_extras`
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from tuitionclass.views.learning import _parse_class_assignment_extras as parse


class ClassAssignmentExtrasTests(SimpleTestCase):
    def test_empty_means_no_extras(self):
        self.assertEqual(parse({}), {})

    def test_multipart_json_strings_are_parsed_and_cleaned(self):
        out = parse({
            "kind": "project", "difficulty": "easy",
            "tags": '["Python", "python", "web"]',
            "rubric": '[{"criterion": "Design", "max_marks": 10}, {"criterion": "Code", "max_marks": 20}]',
            "submission_types": '["link", "file"]',
        })
        self.assertEqual(out["kind"], "project")
        self.assertEqual(out["tags"], ["python", "web"])
        self.assertEqual(out["rubric"][1], {"criterion": "Code", "max_marks": 20})
        self.assertEqual(out["submission_types"], ["link", "file"])

    def test_bad_values_raise(self):
        for bad in ({"kind": "exam"}, {"difficulty": "insane"}, {"tags": "not json"}, {"rubric": '[{"criterion": "", "max_marks": 5}]'},
                    {"submission_types": '["video"]'}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                parse(bad)
