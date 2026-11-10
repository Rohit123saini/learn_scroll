# campus/tests_privacy_rules.py
"""
Pure rules of the parent / child privacy module (`privacy_rules.py`) — no Django, no database, no clock.

Run: `python -m unittest campus.tests_privacy_rules`   (works without any project setup)

Written against the code only — NOT RUN when written.
"""
import importlib.util
import os
import unittest
from datetime import date

_spec = importlib.util.spec_from_file_location("privacy_rules", os.path.join(os.path.dirname(__file__), "privacy_rules.py"))
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)

TODAY = date(2026, 10, 21)
CURRENT = "2026-10"
OLD = "2025-01"


class AgeTests(unittest.TestCase):
    def test_age_on_counts_full_years_only(self):
        self.assertEqual(rules.age_on(date(2008, 10, 21), TODAY), 18)   # birthday today -> already 18
        self.assertEqual(rules.age_on(date(2008, 10, 22), TODAY), 17)   # birthday tomorrow -> still 17
        self.assertEqual(rules.age_on(date(2008, 12, 31), TODAY), 17)
        self.assertEqual(rules.age_on(date(2026, 10, 21), TODAY), 0)

    def test_status_minor_adult_and_missing(self):
        self.assertEqual(rules.age_status(date(2014, 3, 1), TODAY), rules.AGE_MINOR)
        self.assertEqual(rules.age_status(date(2000, 3, 1), TODAY), rules.AGE_ADULT)
        self.assertEqual(rules.age_status(None, TODAY), rules.AGE_DOB_MISSING)

    def test_exactly_eighteen_is_adult_and_one_day_short_is_minor(self):
        self.assertEqual(rules.age_status(date(2008, 10, 21), TODAY), rules.AGE_ADULT)
        self.assertEqual(rules.age_status(date(2008, 10, 22), TODAY), rules.AGE_MINOR)

    def test_custom_adult_age(self):
        self.assertEqual(rules.age_status(date(2008, 3, 1), TODAY, adult_age=21), rules.AGE_MINOR)

    def test_missing_dob_follows_the_campus_policy(self):
        self.assertTrue(rules.treated_as_minor(rules.AGE_DOB_MISSING, True))
        self.assertFalse(rules.treated_as_minor(rules.AGE_DOB_MISSING, False))
        self.assertTrue(rules.treated_as_minor(rules.AGE_MINOR, False))     # a known minor is a minor whatever the policy says
        self.assertFalse(rules.treated_as_minor(rules.AGE_ADULT, True))


class ResolveGuardianTests(unittest.TestCase):
    def g(self, version=CURRENT):
        return {"status": "granted", "version": version}

    def w(self, version=CURRENT):
        return {"status": "withdrawn", "version": version}

    def test_nobody_answered_is_missing(self):
        self.assertEqual(rules.resolve_guardian([None, None], CURRENT), rules.DEC_MISSING)
        self.assertEqual(rules.resolve_guardian([], CURRENT), rules.DEC_MISSING)

    def test_one_parent_granted_on_current_text(self):
        self.assertEqual(rules.resolve_guardian([self.g()], CURRENT), rules.DEC_GRANTED)

    def test_grant_on_an_old_text_is_stale(self):
        self.assertEqual(rules.resolve_guardian([self.g(OLD)], CURRENT), rules.DEC_STALE)

    def test_any_withdrawal_wins_even_when_the_other_parent_granted(self):
        self.assertEqual(rules.resolve_guardian([self.g(), self.w()], CURRENT), rules.DEC_WITHDRAWN)
        self.assertEqual(rules.resolve_guardian([self.w(), self.g()], CURRENT), rules.DEC_WITHDRAWN)

    def test_withdrawal_wins_over_a_stale_grant_too(self):
        self.assertEqual(rules.resolve_guardian([self.g(OLD), self.w(OLD)], CURRENT), rules.DEC_WITHDRAWN)

    def test_one_current_grant_beats_another_parents_stale_grant(self):
        self.assertEqual(rules.resolve_guardian([self.g(OLD), self.g()], CURRENT), rules.DEC_GRANTED)

    def test_a_parent_who_did_not_answer_does_not_block_the_other(self):
        self.assertEqual(rules.resolve_guardian([None, self.g()], CURRENT), rules.DEC_GRANTED)

    def test_empty_version_strings_compare_equal(self):
        self.assertEqual(rules.resolve_guardian([{"status": "granted", "version": None}], ""), rules.DEC_GRANTED)

    def test_result_is_always_a_known_decision(self):
        for case in ([], [None], [self.g()], [self.g(OLD)], [self.w()], [self.g(), self.w()]):
            self.assertIn(rules.resolve_guardian(case, CURRENT), rules.DECISIONS)


class ChildIssuesTests(unittest.TestCase):
    def test_adult_never_has_issues(self):
        self.assertEqual(rules.child_issues(rules.AGE_ADULT, True, 0, rules.DEC_MISSING), [])

    def test_dob_missing_flagged_and_follows_policy(self):
        # policy says "treat as minor": DOB issue + the usual paperwork checks
        self.assertEqual(
            rules.child_issues(rules.AGE_DOB_MISSING, True, 1, rules.DEC_MISSING),
            [rules.ISSUE_DOB_MISSING, rules.ISSUE_CONSENT_MISSING],
        )
        # policy says "not a minor": only the DOB issue
        self.assertEqual(rules.child_issues(rules.AGE_DOB_MISSING, False, 0, rules.DEC_MISSING), [rules.ISSUE_DOB_MISSING])

    def test_minor_without_a_parent_reports_only_that(self):
        self.assertEqual(rules.child_issues(rules.AGE_MINOR, True, 0, rules.DEC_MISSING), [rules.ISSUE_NO_PARENT])

    def test_dob_missing_without_parent_reports_both(self):
        self.assertEqual(
            rules.child_issues(rules.AGE_DOB_MISSING, True, 0, rules.DEC_MISSING),
            [rules.ISSUE_DOB_MISSING, rules.ISSUE_NO_PARENT],
        )

    def test_minor_with_parent_maps_each_decision(self):
        cases = {
            rules.DEC_MISSING: [rules.ISSUE_CONSENT_MISSING],
            rules.DEC_WITHDRAWN: [rules.ISSUE_CONSENT_WITHDRAWN],
            rules.DEC_STALE: [rules.ISSUE_CONSENT_STALE],
            rules.DEC_GRANTED: [],
        }
        for decision, expected in cases.items():
            self.assertEqual(rules.child_issues(rules.AGE_MINOR, True, 2, decision), expected, decision)

    def test_every_issue_code_is_listed(self):
        seen = set()
        for status in rules.AGE_STATUSES:
            for unknown in (True, False):
                for parents in (0, 1):
                    for decision in rules.DECISIONS:
                        seen.update(rules.child_issues(status, unknown, parents, decision))
        self.assertEqual(seen, set(rules.ISSUES))


class MinorSelfGrantTests(unittest.TestCase):
    def test_minor_cannot_grant_photo_or_ai_for_themselves(self):
        self.assertTrue(rules.minor_may_not_grant(rules.AGE_MINOR, "photo_media", True))
        self.assertTrue(rules.minor_may_not_grant(rules.AGE_MINOR, "ai_features", True))

    def test_minor_can_always_withdraw(self):
        self.assertFalse(rules.minor_may_not_grant(rules.AGE_MINOR, "photo_media", False))
        self.assertFalse(rules.minor_may_not_grant(rules.AGE_MINOR, "ai_features", False))

    def test_minor_can_grant_other_purposes(self):
        for purpose in ("terms_privacy", "communications"):
            self.assertFalse(rules.minor_may_not_grant(rules.AGE_MINOR, purpose, True), purpose)

    def test_adult_and_missing_dob_are_not_blocked(self):
        self.assertFalse(rules.minor_may_not_grant(rules.AGE_ADULT, "photo_media", True))
        self.assertFalse(rules.minor_may_not_grant(rules.AGE_DOB_MISSING, "ai_features", True))

    def test_only_a_real_boolean_true_counts_as_a_grant(self):
        self.assertFalse(rules.minor_may_not_grant(rules.AGE_MINOR, "photo_media", "yes"))
        self.assertFalse(rules.minor_may_not_grant(rules.AGE_MINOR, "photo_media", 1))
        self.assertFalse(rules.minor_may_not_grant(rules.AGE_MINOR, "photo_media", None))


class FeatureAllowedTests(unittest.TestCase):
    def test_switch_off_always_allows(self):
        for minor in (True, False):
            for decision in rules.DECISIONS:
                self.assertTrue(rules.feature_allowed(False, minor, decision, None))

    def test_minor_needs_the_combined_guardian_decision_to_be_granted(self):
        self.assertTrue(rules.feature_allowed(True, True, rules.DEC_GRANTED, None))
        for decision in (rules.DEC_MISSING, rules.DEC_STALE, rules.DEC_WITHDRAWN):
            self.assertFalse(rules.feature_allowed(True, True, decision, "granted"), decision)

    def test_a_minors_own_answer_does_not_count(self):
        self.assertFalse(rules.feature_allowed(True, True, rules.DEC_MISSING, "granted"))

    def test_adult_needs_their_own_granted_answer(self):
        self.assertTrue(rules.feature_allowed(True, False, rules.DEC_MISSING, "granted"))
        self.assertFalse(rules.feature_allowed(True, False, rules.DEC_GRANTED, "withdrawn"))
        self.assertFalse(rules.feature_allowed(True, False, rules.DEC_GRANTED, None))

    def test_feature_to_guardian_mapping(self):
        self.assertEqual(rules.FEATURE_TO_GUARDIAN["photo_media"], "photo_media")
        self.assertEqual(rules.FEATURE_TO_GUARDIAN["ai_features"], "child_ai_features")


class PurposeListTests(unittest.TestCase):
    def test_required_purpose_is_one_of_the_guardian_purposes(self):
        self.assertIn(rules.REQUIRED_GUARDIAN_PURPOSE, rules.GUARDIAN_PURPOSES)
        self.assertEqual(rules.GUARDIAN_PURPOSES, ("child_data", "photo_media", "child_ai_features"))


class PagingTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(rules.clean_page({}), (1, rules.PAGE_SIZE_DEFAULT))

    def test_junk_falls_back_to_defaults(self):
        self.assertEqual(rules.clean_page({"page": "abc", "page_size": "x"}), (1, rules.PAGE_SIZE_DEFAULT))
        self.assertEqual(rules.clean_page({"page": None, "page_size": None}), (1, rules.PAGE_SIZE_DEFAULT))

    def test_page_is_at_least_one(self):
        self.assertEqual(rules.clean_page({"page": "0"})[0], 1)
        self.assertEqual(rules.clean_page({"page": "-4"})[0], 1)

    def test_page_size_is_clamped(self):
        self.assertEqual(rules.clean_page({"page_size": "0"})[1], 1)
        self.assertEqual(rules.clean_page({"page_size": "-5"})[1], 1)
        self.assertEqual(rules.clean_page({"page_size": "100000"})[1], rules.PAGE_SIZE_MAX)
        self.assertEqual(rules.clean_page({"page": "3", "page_size": "50"}), (3, 50))


class IssueFilterTests(unittest.TestCase):
    def test_empty_means_no_filter(self):
        self.assertEqual(rules.clean_issue_filter(None), [])
        self.assertEqual(rules.clean_issue_filter(""), [])
        self.assertEqual(rules.clean_issue_filter("   "), [])

    def test_any(self):
        self.assertEqual(rules.clean_issue_filter("any"), ["any"])
        self.assertEqual(rules.clean_issue_filter(" any "), ["any"])

    def test_one_and_many_codes(self):
        self.assertEqual(rules.clean_issue_filter("consent_missing"), ["consent_missing"])
        self.assertEqual(
            rules.clean_issue_filter("consent_missing, no_linked_parent"), ["consent_missing", "no_linked_parent"]
        )

    def test_stray_commas_are_ignored(self):
        self.assertEqual(rules.clean_issue_filter(",consent_stale,,"), ["consent_stale"])

    def test_unknown_code_raises(self):
        with self.assertRaises(ValueError):
            rules.clean_issue_filter("consent_missing,nope")
        with self.assertRaises(ValueError):
            rules.clean_issue_filter("any,consent_missing")   # `any` is only valid on its own


if __name__ == "__main__":
    unittest.main()
