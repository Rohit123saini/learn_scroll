"""Live Q&A / guest helpers that need no DB."""
from types import SimpleNamespace

from django.test import SimpleTestCase

from tuitionclass.serializers import ClassQuerySerializer


class HasUpvotedTests(SimpleTestCase):
    def _ser(self, ctx):
        s = ClassQuerySerializer(context=ctx)
        return s.get_has_upvoted(SimpleNamespace(id=5))

    def test_true_only_when_id_in_context(self):
        self.assertTrue(self._ser({"upvoted_ids": {5, 9}}))
        self.assertFalse(self._ser({"upvoted_ids": {9}}))

    def test_missing_or_empty_context_is_false(self):
        self.assertFalse(self._ser({}))
        self.assertFalse(self._ser({"upvoted_ids": set()}))
