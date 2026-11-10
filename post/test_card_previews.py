"""Pure helpers of post/card_previews.py (no database needed)."""
import uuid

from django.test import SimpleTestCase

from post.card_previews import MAX_IDS, TEXT_MAX, clip, parse_ids


class CardPreviewHelpersTests(SimpleTestCase):
    def test_parse_ids_keeps_valid_distinct_in_order(self):
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        self.assertEqual(parse_ids([a, "nope", a, b, 5, None]), [a, b])

    def test_parse_ids_caps_and_rejects_non_lists(self):
        many = [str(uuid.uuid4()) for _ in range(MAX_IDS + 10)]
        self.assertEqual(len(parse_ids(many)), MAX_IDS)
        self.assertEqual(parse_ids("abc"), [])
        self.assertEqual(parse_ids(None), [])

    def test_clip_collapses_whitespace_and_truncates(self):
        self.assertEqual(clip("  hello \n  world "), "hello world")
        out = clip("x" * 500)
        self.assertEqual(len(out), TEXT_MAX)
        self.assertTrue(out.endswith("…"))
