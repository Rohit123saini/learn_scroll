# testseries/tests_pure.py
"""
Pure-Python tests — no Django, no database. Run either way:

    python -m unittest testseries.tests_pure          # from the project root
    python manage.py test testseries.tests_pure       # inside the normal suite

They cover the rules in `policy.py` and `csv_import.py`, which are the
parts of the advanced test-series feature set that can be verified
completely on their own.
"""
import unittest
from datetime import datetime, timedelta, timezone

from testseries import policy
from testseries.csv_import import parse_csv

UTC = timezone.utc
T0 = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


class PricingPolicyTests(unittest.TestCase):
    def test_individual_must_be_paid(self):
        with self.assertRaises(policy.PolicyError) as cm:
            policy.normalize_pricing(source="individual", is_paid=False, price_coins=0, strict=True)
        self.assertEqual(cm.exception.field, "is_paid")

    def test_individual_paid_ok_and_min_price_enforced(self):
        self.assertEqual(
            policy.normalize_pricing(source="individual", is_paid=True, price_coins=50, strict=True), (True, 50)
        )
        with self.assertRaises(policy.PolicyError) as cm:
            policy.normalize_pricing(source="individual", is_paid=True, price_coins=0, strict=True)
        self.assertEqual(cm.exception.field, "price_coins")

    def test_campus_is_always_free(self):
        with self.assertRaises(policy.PolicyError):
            policy.normalize_pricing(source="campus", is_paid=True, price_coins=10, strict=True)
        # non-strict (model.save) silently coerces instead of raising
        self.assertEqual(
            policy.normalize_pricing(source="campus", is_paid=True, price_coins=10, strict=False), (False, 0)
        )
        self.assertEqual(policy.normalize_pricing(source="campus", is_paid=False, price_coins=0, strict=True), (False, 0))

    def test_liveclass_free_or_paid(self):
        self.assertEqual(policy.normalize_pricing(source="liveclass", is_paid=False, price_coins=0, strict=True), (False, 0))
        self.assertEqual(policy.normalize_pricing(source="liveclass", is_paid=True, price_coins=25, strict=True), (True, 25))
        with self.assertRaises(policy.PolicyError):
            policy.normalize_pricing(source="liveclass", is_paid=False, price_coins=5, strict=True)

    def test_non_strict_never_raises_and_keeps_legacy_free_individual(self):
        # Legacy free individual rows must keep saving (e.g. recompute_total_marks()).
        self.assertEqual(
            policy.normalize_pricing(source="individual", is_paid=False, price_coins=0, strict=False), (False, 0)
        )

    def test_policy_is_overridable(self):
        relaxed = {"individual": {"mode": "optional"}}
        merged = {**policy.DEFAULT_PRICING_POLICY, **relaxed}
        self.assertEqual(
            policy.normalize_pricing(source="individual", is_paid=False, price_coins=0, strict=True, policy=merged),
            (False, 0),
        )


class WindowTests(unittest.TestCase):
    def test_self_paced_always_open(self):
        self.assertEqual(
            policy.window_state(mode="self_paced", now=T0, starts_at=None, ends_at=None), policy.OPEN
        )

    def test_scheduled_window(self):
        kw = dict(mode="scheduled", starts_at=T0, ends_at=T0 + timedelta(hours=2))
        self.assertEqual(policy.window_state(now=T0 - timedelta(minutes=1), **kw), policy.NOT_STARTED)
        self.assertEqual(policy.window_state(now=T0 + timedelta(minutes=90), **kw), policy.OPEN)
        self.assertEqual(policy.window_state(now=T0 + timedelta(hours=2), **kw), policy.ENDED)

    def test_live_late_entry(self):
        kw = dict(mode="live", starts_at=T0, ends_at=T0 + timedelta(hours=1), late_entry_minutes=10)
        self.assertEqual(policy.window_state(now=T0 + timedelta(minutes=5), **kw), policy.OPEN)
        self.assertEqual(policy.window_state(now=T0 + timedelta(minutes=11), **kw), policy.LATE_CLOSED)
        self.assertEqual(policy.window_state(now=T0 + timedelta(hours=1), **kw), policy.ENDED)


class DeadlineTests(unittest.TestCase):
    def test_deadline_is_start_plus_duration(self):
        self.assertEqual(
            policy.compute_deadline(started_at=T0, duration_minutes=60), T0 + timedelta(minutes=60)
        )

    def test_window_end_caps_the_deadline(self):
        end = T0 + timedelta(minutes=30)
        self.assertEqual(policy.compute_deadline(started_at=T0, duration_minutes=60, window_end=end), end)

    def test_untimed(self):
        self.assertIsNone(policy.compute_deadline(started_at=T0, duration_minutes=None))
        self.assertEqual(policy.compute_deadline(started_at=T0, duration_minutes=None, window_end=T0), T0)

    def test_grace(self):
        d = T0 + timedelta(minutes=60)
        self.assertFalse(policy.is_past_deadline(now=d + timedelta(seconds=20), deadline=d, grace=30))
        self.assertTrue(policy.is_past_deadline(now=d + timedelta(seconds=31), deadline=d, grace=30))
        self.assertFalse(policy.is_past_deadline(now=d, deadline=None, grace=30))


class GradingTests(unittest.TestCase):
    def test_non_dict_answer_is_no_answer(self):
        self.assertEqual(policy.coerce_answer_data("B"), {})
        self.assertEqual(policy.coerce_answer_data(["a"]), {})
        self.assertEqual(policy.coerce_answer_data({"option_id": "a"}), {"option_id": "a"})

    def test_is_answered(self):
        self.assertFalse(policy.is_answered("mcq", {}))
        self.assertTrue(policy.is_answered("mcq", {"option_id": "a"}))
        self.assertFalse(policy.is_answered("msq", {"option_ids": []}))
        self.assertFalse(policy.is_answered("list", {"list_mode": "order", "sequence": []}))
        self.assertTrue(policy.is_answered("list", {"list_mode": "match", "pairs": {"1": "2"}}))
        self.assertFalse(policy.is_answered("text", {"text": "   "}))

    def test_negative_marking_only_for_answered_wrong(self):
        neg = lambda **k: policy.negative_penalty(negative_marks=1, **k)  # noqa: E731
        self.assertEqual(neg(question_type="mcq", is_correct=False, answer_data={"option_id": "b"}), 1)
        self.assertEqual(neg(question_type="mcq", is_correct=False, answer_data={}), 0)  # blank
        self.assertEqual(neg(question_type="mcq", is_correct=True, answer_data={"option_id": "a"}), 0)
        self.assertEqual(neg(question_type="text", is_correct=None, answer_data={"text": "x"}), 0)
        self.assertEqual(neg(question_type="list", is_correct=False, answer_data={"list_mode": "order", "sequence": []}), 0)

    def test_net_score_never_negative(self):
        self.assertEqual(policy.net_score(4, 10), 0)
        self.assertEqual(policy.net_score(10, 3), 7)


class ReleaseTests(unittest.TestCase):
    def test_instant(self):
        self.assertTrue(policy.results_visible(release_mode="instant", now=T0, ends_at=None, released_at=None))

    def test_after_end(self):
        end = T0 + timedelta(hours=1)
        self.assertFalse(policy.results_visible(release_mode="after_end", now=T0, ends_at=end, released_at=None))
        self.assertTrue(policy.results_visible(release_mode="after_end", now=end, ends_at=end, released_at=None))
        self.assertTrue(policy.results_visible(release_mode="after_end", now=T0, ends_at=None, released_at=None))

    def test_manual(self):
        self.assertFalse(policy.results_visible(release_mode="manual", now=T0, ends_at=None, released_at=None))
        self.assertTrue(policy.results_visible(release_mode="manual", now=T0, ends_at=None, released_at=T0))


class CertificateMathTests(unittest.TestCase):
    def test_percentage_and_pass(self):
        self.assertEqual(policy.percentage(63, 100), 63.0)
        self.assertEqual(policy.percentage(1, 3), 33.33)
        self.assertIsNone(policy.percentage(5, 0))
        self.assertTrue(policy.has_passed(40.0, 40))
        self.assertFalse(policy.has_passed(39.99, 40))
        self.assertIsNone(policy.has_passed(80.0, None))

    def test_code_format_and_uniqueness(self):
        codes = {policy.generate_certificate_code() for _ in range(500)}
        self.assertEqual(len(codes), 500)
        for c in list(codes)[:20]:
            self.assertRegex(c, r"^LS-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")
            self.assertNotRegex(c[3:], r"[01OIL]")  # body only: the "LS-" prefix legitimately has an L

    def test_share_slug_is_url_safe(self):
        for _ in range(100):
            self.assertRegex(policy.generate_share_slug(), r"^[A-Za-z0-9]{8,}$")


GOOD_CSV = (
    "type,question,option_a,option_b,option_c,option_d,correct,marks,negative,topic,difficulty,explanation\n"
    "mcq,Capital of India?,Mumbai,Delhi,Kolkata,Chennai,B,4,1,GK,easy,New Delhi is the capital.\n"
    "msq,Which are prime?,2,4,5,9,\"A,C\",4,1,Maths,medium,\n"
    "order,Sort ascending,3,1,2,,B C A,4,0,Maths,,\n"
    "text,Explain photosynthesis.,,,,,,10,0,Bio,hard,\n"
)


class CsvImportTests(unittest.TestCase):
    def test_good_file_builds_answer_key(self):
        r = parse_csv(GOOD_CSV)
        self.assertEqual(r.errors, [])
        self.assertEqual(len(r.questions), 4)
        mcq, msq, order, text = r.questions
        self.assertEqual(mcq["question_type"], "mcq")
        self.assertEqual(mcq["correct_answer"], {"option_id": "b"})
        self.assertEqual((mcq["marks"], mcq["negative_marks"], mcq["topic"], mcq["difficulty"]), (4, 1, "GK", "easy"))
        self.assertEqual(msq["correct_answer"], {"option_ids": ["a", "c"]})
        self.assertEqual(order["question_type"], "list")
        self.assertEqual(order["correct_answer"], {"list_mode": "order", "sequence": ["b", "c", "a"]})
        self.assertEqual(text["question_type"], "text")
        self.assertEqual(text["correct_answer"], {})
        self.assertEqual([q["order"] for q in r.questions], [1, 2, 3, 4])

    def test_start_order_offsets_new_questions(self):
        r = parse_csv(GOOD_CSV, start_order=11)
        self.assertEqual([q["order"] for q in r.questions], [11, 12, 13, 14])

    def test_bytes_with_excel_bom(self):
        r = parse_csv(("\ufeff" + GOOD_CSV).encode("utf-8"))
        self.assertEqual(r.errors, [])

    def test_missing_answer_key_is_reported_with_row_number(self):
        bad = "question,option_a,option_b,correct\nWhat?,x,y,\n"
        r = parse_csv(bad)
        self.assertEqual(r.questions, [])
        self.assertEqual(r.errors[0][0], 2)
        self.assertIn("correct", r.errors[0][1])

    def test_answer_pointing_to_missing_option(self):
        r = parse_csv("question,option_a,option_b,correct\nWhat?,x,y,D\n")
        self.assertTrue(any("don't exist" in m for _, m in r.errors))

    def test_mcq_with_two_answers_rejected(self):
        r = parse_csv("question,option_a,option_b,option_c,correct\nWhat?,x,y,z,\"A,B\"\n")
        self.assertTrue(any("exactly one" in m for _, m in r.errors))

    def test_one_bad_row_does_not_hide_good_rows(self):
        csv_text = (
            "question,option_a,option_b,correct,marks\n"
            "Good?,a,b,A,1\n"
            "Bad?,a,b,Z,1\n"
            "Also good?,a,b,B,2\n"
        )
        r = parse_csv(csv_text)
        self.assertEqual(len(r.questions), 2)
        self.assertEqual(len(r.errors), 1)
        self.assertEqual(r.errors[0][0], 3)

    def test_missing_question_column(self):
        r = parse_csv("a,b\n1,2\n")
        self.assertFalse(r.ok)
        self.assertIn("question", r.errors[0][1])

    def test_skipped_option_column_rejected(self):
        r = parse_csv("question,option_a,option_c,correct\nQ?,x,y,A\n")
        self.assertTrue(any("continuous" in m for _, m in r.errors))

    def test_non_utf8(self):
        r = parse_csv("question\nñ".encode("latin-1"))
        self.assertTrue(any("UTF-8" in m for _, m in r.errors))


if __name__ == "__main__":
    unittest.main()
