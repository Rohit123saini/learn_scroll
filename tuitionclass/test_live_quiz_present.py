"""Quiz push + present helpers that need no DB (SimpleTestCase)."""
from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework import serializers

from tuitionclass.serializers import LivePollSerializer


class _Responses:
    def __init__(self, idxs):
        self._idxs = idxs

    def values_list(self, *_a, **_k):
        return list(self._idxs)


def _poll(correct=None, revealed=None, votes=()):
    return SimpleNamespace(
        id=7, options=["a", "b", "c"], correct_option_index=correct, revealed_at=revealed,
        responses=_Responses(votes),
    )


class AnswerVisibilityTests(SimpleTestCase):
    def test_plain_poll_has_nothing_to_hide(self):
        self.assertTrue(LivePollSerializer(context={})._answer_visible(_poll(correct=None)))

    def test_quiz_hidden_from_students_before_reveal(self):
        self.assertFalse(LivePollSerializer(context={})._answer_visible(_poll(correct=1)))
        # a socket broadcast has no context at all - must stay hidden
        self.assertFalse(LivePollSerializer()._answer_visible(_poll(correct=1)))

    def test_quiz_visible_to_moderator_context_and_after_reveal(self):
        self.assertTrue(LivePollSerializer(context={"show_answer": True})._answer_visible(_poll(correct=1)))
        self.assertTrue(LivePollSerializer(context={})._answer_visible(_poll(correct=1, revealed="2026-01-01")))


class CorrectCountTests(SimpleTestCase):
    def test_none_until_revealed(self):
        self.assertIsNone(LivePollSerializer().get_correct_count(_poll(correct=1, votes=[1, 1, 0])))

    def test_counts_only_the_right_option_after_reveal(self):
        p = _poll(correct=1, revealed="x", votes=[1, 1, 0, 2])
        self.assertEqual(LivePollSerializer().get_correct_count(p), 2)

    def test_not_a_quiz_is_none(self):
        self.assertIsNone(LivePollSerializer().get_correct_count(_poll(correct=None, revealed="x", votes=[0])))


class MyOptionTests(SimpleTestCase):
    def test_reads_from_context_map(self):
        self.assertEqual(LivePollSerializer(context={"my_votes": {7: 2}}).get_my_option_index(_poll()), 2)

    def test_missing_is_none(self):
        self.assertIsNone(LivePollSerializer(context={}).get_my_option_index(_poll()))
        self.assertIsNone(LivePollSerializer(context={"my_votes": {9: 1}}).get_my_option_index(_poll()))


class ValidateTests(SimpleTestCase):
    def test_correct_index_must_be_inside_options(self):
        ser = LivePollSerializer()
        with self.assertRaises(serializers.ValidationError):
            ser.validate({"options": ["a", "b"], "correct_option_index": 2})
        self.assertEqual(ser.validate({"options": ["a", "b"], "correct_option_index": 1})["correct_option_index"], 1)

    def test_revealed_quiz_cannot_change_answer(self):
        ser = LivePollSerializer(instance=_poll(correct=0, revealed="x"))
        with self.assertRaises(serializers.ValidationError):
            ser.validate({"correct_option_index": 1})
        # editing only the question text is fine
        self.assertEqual(ser.validate({}), {})
