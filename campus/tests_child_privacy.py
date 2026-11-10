# campus/tests_child_privacy.py
"""
Parent / child privacy API tests: minors' consent rule, per-campus policy switches, admin compliance list / summary /
child detail, parent "my children" view, reminders, and the `allowed()` gate.

Run: `python manage.py test campus.tests_child_privacy`
(The pure rules have their own DB-free file: `campus/tests_privacy_rules.py`.)

Written against the code only — NOT RUN when written (no Django in that environment).
"""
import json
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from . import child_privacy as svc
from . import consent as consent_svc
from .bridge import NotifTypes
from .models import (
    AcademicSession,
    Campus,
    CampusAuditLog,
    CampusConsentRecord,
    CampusParentLink,
    CampusPrivacyPolicy,
    SchoolClass,
    Section,
    StudentEnrollment,
)
from .tests_safety import SafetyBase

User = get_user_model()

OLD_VERSION = "2020-01"


class PrivacyBase(SafetyBase):
    """
    SafetyBase gives: campus, admin (ADMIN), principal, teacher(s), outsider, sa1 / sa2 (section A), sb (section B),
    parent (linked to sa1), notify mock.

    Here: sa1 = MINOR (12), sa2 = ADULT (20), sb = NO date of birth. A second parent `parent2` exists but is NOT
    linked to anybody until a test links it.
    """

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        User.objects.filter(pk=self.sa1.pk).update(date_of_birth=today - timedelta(days=365 * 12 + 3))
        User.objects.filter(pk=self.sa2.pk).update(date_of_birth=today - timedelta(days=365 * 20 + 10))
        for u in (self.sa1, self.sa2, self.sb):
            u.refresh_from_db()  # the instances the tests pass around must carry the new date of birth
        self.parent2 = User.objects.create_user(username="parent2", password="pass12345")

    # ---- helpers
    def p(self, name, *args):
        return reverse(f"campus-privacy-{name}", args=[str(self.campus.id), *args])

    def link_parent2(self, student=None):
        return CampusParentLink.objects.create(campus=self.campus, parent=self.parent2, student=student or self.sa1)

    def rec(self, subject, purpose, granted, student=None, version=None):
        """
        Write a consent row directly (no throttle, no HTTP). Every row gets its own, strictly later `created_at`, so
        "the latest answer wins" never depends on the clock resolution of the machine running the tests.
        """
        row = consent_svc.record(self.campus, subject, purpose, granted, student=student, version=version)
        self._tick = getattr(self, "_tick", 0) + 1
        stamp = timezone.now() - timedelta(hours=1) + timedelta(seconds=self._tick)
        CampusConsentRecord.objects.filter(pk=row.pk).update(created_at=stamp)   # queryset update: the model blocks save()
        return row

    def answer(self, parent, purpose, granted, student=None, version=None):
        """A parent's answer about a child (default child: sa1)."""
        return self.rec(parent, purpose, granted, student=student or self.sa1, version=version)

    def policy(self, **values):
        row, _ = CampusPrivacyPolicy.objects.get_or_create(campus=self.campus)
        for key, value in values.items():
            setattr(row, key, value)
        row.save()
        return row

    def children(self, user=None, **params):
        return self.as_(user or self.admin).get(self.p("children"), params)

    def by_username(self, resp):
        return {r["student"]["username"]: r for r in resp.data["results"]}

    def purpose_row(self, resp, purpose):
        return next(x for x in resp.data["purposes"] if x["purpose"] == purpose)


# ===========================================================================
# 1. consent.record(): what a minor may and may not do
# ===========================================================================
class MinorConsentRuleTests(PrivacyBase):
    def me_post(self, user, **body):
        return self.as_(user).post(reverse("campus-safety-consent-me", args=[str(self.campus.id)]), body, format="json")

    def test_minor_cannot_grant_photo_media_for_themselves(self):
        resp = self.me_post(self.sa1, purpose="photo_media", granted=True)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get("code"), "guardian_consent_required")
        self.assertFalse(CampusConsentRecord.objects.filter(subject=self.sa1, purpose="photo_media").exists())

    def test_minor_cannot_grant_ai_features_for_themselves(self):
        resp = self.me_post(self.sa1, purpose="ai_features", granted=True)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get("code"), "guardian_consent_required")

    def test_minor_may_always_withdraw(self):
        for purpose in ("photo_media", "ai_features"):
            resp = self.me_post(self.sa1, purpose=purpose, granted=False)
            self.assertEqual(resp.status_code, 201, resp.data)
            self.assertEqual(resp.data["status"], "withdrawn")

    def test_minor_may_still_answer_other_purposes(self):
        self.assertEqual(self.me_post(self.sa1, purpose="communications", granted=True).status_code, 201)
        self.assertEqual(self.me_post(self.sa1, purpose="terms_privacy", granted=True).status_code, 201)

    def test_adult_and_missing_dob_are_not_blocked(self):
        self.assertEqual(self.me_post(self.sa2, purpose="photo_media", granted=True).status_code, 201)
        self.assertEqual(self.me_post(self.sb, purpose="ai_features", granted=True).status_code, 201)

    def test_linked_parent_may_grant_photo_media_and_ai_for_the_child(self):
        resp = self.me_post(self.parent, purpose="photo_media", granted=True, student=self.sa1.pk)
        self.assertEqual(resp.status_code, 201, resp.data)
        resp = self.me_post(self.parent, purpose="child_ai_features", granted=True, student=self.sa1.pk)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["purpose"], "child_ai_features")
        self.assertEqual(resp.data["student"]["id"], self.sa1.pk)

    def test_child_ai_features_needs_a_child(self):
        resp = self.me_post(self.parent, purpose="child_ai_features", granted=True)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "student_required")

    def test_parent_cannot_answer_child_ai_features_for_an_unlinked_child(self):
        resp = self.me_post(self.parent, purpose="child_ai_features", granted=True, student=self.sb.pk)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get("code"), "not_a_linked_parent")

    def test_staff_cannot_type_in_a_grant_with_the_minor_as_the_subject(self):
        resp = self.as_(self.admin).post(
            reverse("campus-safety-consent-record", args=[str(self.campus.id)]),
            {"subject": self.sa1.pk, "purpose": "photo_media", "granted": True}, format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get("code"), "guardian_consent_required")

    def test_staff_can_type_in_the_parents_paper_consent_for_the_child(self):
        resp = self.as_(self.admin).post(
            reverse("campus-safety-consent-record", args=[str(self.campus.id)]),
            {"subject": self.parent.pk, "purpose": "photo_media", "granted": True, "student": self.sa1.pk},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["source"], "admin_entry")

    def test_parent_state_lists_child_ai_features_per_child_and_it_is_optional(self):
        resp = self.as_(self.parent).get(reverse("campus-safety-consent-me", args=[str(self.campus.id)]))
        items = [i for i in resp.data["items"] if i["purpose"] == "child_ai_features"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["student"]["id"], self.sa1.pk)
        self.assertFalse(items[0]["required"])

    def test_student_state_has_no_child_ai_features_item(self):
        resp = self.as_(self.sa1).get(reverse("campus-safety-consent-me", args=[str(self.campus.id)]))
        self.assertNotIn("child_ai_features", {i["purpose"] for i in resp.data["items"]})

    def test_child_ai_features_never_counts_as_a_required_consent_missing(self):
        resp = self.as_(self.admin).get(reverse("campus-safety-consent-summary", args=[str(self.campus.id)]))
        by = {p["purpose"]: p for p in resp.data["purposes"]}
        self.assertIn("child_ai_features", by)
        self.assertFalse(by["child_ai_features"]["required"])
        self.assertEqual(by["child_ai_features"]["asked"], 1)   # one (parent, child) link
        self.assertEqual(
            resp.data["required_missing"], sum(p["missing"] for p in resp.data["purposes"] if p["required"])
        )


# ===========================================================================
# 2. per-campus policy switches
# ===========================================================================
class PolicyTests(PrivacyBase):
    def test_defaults_when_no_row_exists(self):
        resp = self.as_(self.admin).get(self.p("policy"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["enforce_guardian_consent"])
        self.assertTrue(resp.data["unknown_dob_is_minor"])
        self.assertEqual(resp.data["adult_age"], 18)
        self.assertIsNone(resp.data["updated_at"])
        self.assertFalse(CampusPrivacyPolicy.objects.filter(campus=self.campus).exists())   # a GET creates nothing

    def test_admin_and_principal_can_read(self):
        for user in (self.admin, self.principal):
            self.assertEqual(self.as_(user).get(self.p("policy")).status_code, 200, user.username)

    def test_others_cannot_read_or_change(self):
        for user in (self.teacher, self.sa1, self.parent, self.outsider):
            self.assertEqual(self.as_(user).get(self.p("policy")).status_code, 403, user.username)
            self.assertEqual(
                self.as_(user).post(self.p("policy"), {"enforce_guardian_consent": True}, format="json").status_code,
                403, user.username,
            )
        self.assertFalse(CampusPrivacyPolicy.objects.filter(campus=self.campus).exists())

    def test_admin_switches_enforcement_on_and_it_is_audit_logged(self):
        resp = self.as_(self.admin).post(self.p("policy"), {"enforce_guardian_consent": True}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["enforce_guardian_consent"])
        self.assertTrue(resp.data["unknown_dob_is_minor"])   # untouched
        self.assertIsNotNone(resp.data["updated_at"])
        row = CampusPrivacyPolicy.objects.get(campus=self.campus)
        self.assertTrue(row.enforce_guardian_consent)
        self.assertEqual(row.updated_by_id, self.admin.pk)
        self.assertIn("privacy.policy", self.audit_actions())
        self.assertTrue(self.as_(self.admin).get(self.p("policy")).data["enforce_guardian_consent"])

    def test_principal_may_change_it(self):
        resp = self.as_(self.principal).post(self.p("policy"), {"unknown_dob_is_minor": False}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data["unknown_dob_is_minor"])

    def test_both_keys_at_once_and_a_second_save_updates_the_same_row(self):
        self.as_(self.admin).post(
            self.p("policy"), {"enforce_guardian_consent": True, "unknown_dob_is_minor": False}, format="json"
        )
        self.as_(self.admin).post(self.p("policy"), {"enforce_guardian_consent": False}, format="json")
        self.assertEqual(CampusPrivacyPolicy.objects.filter(campus=self.campus).count(), 1)
        row = CampusPrivacyPolicy.objects.get(campus=self.campus)
        self.assertFalse(row.enforce_guardian_consent)
        self.assertFalse(row.unknown_dob_is_minor)

    def test_a_string_is_not_a_boolean(self):
        for bad in ("false", "true", 0, 1, None):
            resp = self.as_(self.admin).post(self.p("policy"), {"enforce_guardian_consent": bad}, format="json")
            self.assertEqual(resp.status_code, 400, bad)
            self.assertEqual(resp.data.get("code"), "must_be_boolean", bad)
        self.assertFalse(CampusPrivacyPolicy.objects.filter(campus=self.campus).exists())

    def test_empty_body_and_unknown_keys_change_nothing(self):
        for body in ({}, {"something_else": True}):
            resp = self.as_(self.admin).post(self.p("policy"), body, format="json")
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.data.get("code"), "nothing_to_change")

    def test_a_non_object_body_is_refused(self):
        resp = self.as_(self.admin).post(self.p("policy"), [True], format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "invalid_body")

    def test_policy_is_per_campus(self):
        other = Campus.objects.create(name="Other", created_by=self.admin)
        self.policy(enforce_guardian_consent=True)
        self.assertTrue(svc.get_policy(self.campus)["enforce_guardian_consent"])
        self.assertFalse(svc.get_policy(other)["enforce_guardian_consent"])

    def test_unknown_campus_is_404(self):
        url = reverse("campus-privacy-policy", args=["00000000-0000-0000-0000-000000000000"])
        self.assertEqual(self.as_(self.admin).get(url).status_code, 404)


# ===========================================================================
# 3. admin: summary
# ===========================================================================
class SummaryTests(PrivacyBase):
    def get(self):
        resp = self.as_(self.admin).get(self.p("summary"))
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data

    def test_counts_for_a_fresh_campus(self):
        d = self.get()
        self.assertEqual(d["students"], 3)
        self.assertEqual((d["minors"], d["adults"], d["dob_missing"]), (1, 1, 1))
        self.assertEqual(d["no_linked_parent"], 1)      # sb (DOB missing -> treated as a minor, no parent)
        self.assertEqual(d["consent_missing"], 1)       # sa1 (parent linked, nothing answered)
        self.assertEqual(d["consent_withdrawn"], 0)
        self.assertEqual(d["consent_stale"], 0)
        self.assertEqual(d["covered"], 0)
        self.assertEqual(d["needs_attention"], 2)       # sa1 + sb
        self.assertFalse(d["policy"]["enforce_guardian_consent"])

    def test_a_current_grant_moves_the_child_to_covered(self):
        self.answer(self.parent, "child_data", True)
        d = self.get()
        self.assertEqual(d["consent_missing"], 0)
        self.assertEqual(d["covered"], 1)
        self.assertEqual(d["needs_attention"], 1)

    def test_withdrawn_and_stale_are_counted_separately(self):
        self.answer(self.parent, "child_data", False)
        self.assertEqual(self.get()["consent_withdrawn"], 1)
        self.answer(self.parent, "child_data", True, version=OLD_VERSION)
        d = self.get()
        self.assertEqual((d["consent_withdrawn"], d["consent_stale"]), (0, 1))

    def test_one_parent_withdrawing_wins_over_the_other_parents_grant(self):
        self.link_parent2()
        self.answer(self.parent, "child_data", True)
        self.answer(self.parent2, "child_data", False)
        d = self.get()
        self.assertEqual(d["consent_withdrawn"], 1)
        self.assertEqual(d["covered"], 0)

    def test_unknown_dob_policy_off_stops_counting_no_linked_parent_for_that_student(self):
        self.policy(unknown_dob_is_minor=False)
        d = self.get()
        self.assertEqual(d["no_linked_parent"], 0)
        self.assertEqual(d["dob_missing"], 1)           # still listed so the admin fills the date in
        self.assertEqual(d["needs_attention"], 2)       # sb still has the DOB issue

    def test_permissions(self):
        for user in (self.teacher, self.sa1, self.parent, self.outsider):
            self.assertEqual(self.as_(user).get(self.p("summary")).status_code, 403, user.username)
        self.assertEqual(self.as_(self.principal).get(self.p("summary")).status_code, 200)

    def test_other_campus_students_are_not_counted(self):
        other = Campus.objects.create(name="Other", created_by=self.admin)
        session = AcademicSession.objects.create(
            campus=other, name="2026-27", start_date=date(2026, 6, 1), end_date=date(2027, 4, 30), is_current=True)
        cls = SchoolClass.objects.create(campus=other, session=session, name="5")
        sec = Section.objects.create(school_class=cls, name="A")
        kid = User.objects.create_user(username="otherkid", password="pass12345")
        StudentEnrollment.objects.create(student=kid, section=sec, session=session, roll_number="1")
        self.assertEqual(self.get()["students"], 3)
        self.assertNotIn("otherkid", {r["student"]["username"] for r in self.children(include_adults="1").data["results"]})


# ===========================================================================
# 4. admin: children list
# ===========================================================================
class ChildrenListTests(PrivacyBase):
    def test_default_list_has_minors_and_missing_dob_but_no_adults(self):
        resp = self.children()
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(set(self.by_username(resp)), {"sa1", "sb"})
        self.assertEqual(resp.data["count"], 2)

    def test_include_adults(self):
        resp = self.children(include_adults="1")
        self.assertEqual(set(self.by_username(resp)), {"sa1", "sa2", "sb"})

    def test_row_shape_for_a_minor_with_a_parent(self):
        row = self.by_username(self.children())["sa1"]
        self.assertEqual(row["age_status"], "minor")
        self.assertEqual(row["age"], 12)
        self.assertTrue(row["treated_as_minor"])
        self.assertEqual(row["parent_count"], 1)
        self.assertEqual(row["consent"], {"child_data": "missing", "photo_media": "missing", "child_ai_features": "missing"})
        self.assertEqual(row["issues"], ["consent_missing"])
        self.assertEqual(row["section"]["id"], str(self.secA.id))
        self.assertEqual(row["student"]["id"], self.sa1.pk)

    def test_row_shape_for_a_student_without_a_date_of_birth(self):
        row = self.by_username(self.children())["sb"]
        self.assertEqual(row["age_status"], "dob_missing")
        self.assertIsNone(row["age"])
        self.assertTrue(row["treated_as_minor"])
        self.assertEqual(row["parent_count"], 0)
        self.assertEqual(row["issues"], ["dob_missing", "no_linked_parent"])

    def test_consent_states_follow_the_parents_answers(self):
        self.answer(self.parent, "child_data", True)
        self.answer(self.parent, "photo_media", False)
        self.answer(self.parent, "child_ai_features", True, version=OLD_VERSION)
        row = self.by_username(self.children())["sa1"]
        self.assertEqual(row["consent"], {"child_data": "granted", "photo_media": "withdrawn", "child_ai_features": "stale"})
        self.assertEqual(row["issues"], [])   # only child_data decides the compliance issue

    def test_only_the_latest_answer_of_a_parent_counts(self):
        self.answer(self.parent, "child_data", False)
        self.answer(self.parent, "child_data", True)
        self.assertEqual(self.by_username(self.children())["sa1"]["consent"]["child_data"], "granted")

    def test_an_answer_from_someone_no_longer_linked_does_not_count(self):
        link = self.link_parent2()
        self.answer(self.parent2, "child_data", False)
        self.assertEqual(self.by_username(self.children())["sa1"]["consent"]["child_data"], "withdrawn")
        link.delete()
        self.assertEqual(self.by_username(self.children())["sa1"]["consent"]["child_data"], "missing")

    def test_issue_filter(self):
        self.assertEqual(set(self.by_username(self.children(issue="no_linked_parent"))), {"sb"})
        self.assertEqual(set(self.by_username(self.children(issue="consent_missing"))), {"sa1"})
        self.assertEqual(set(self.by_username(self.children(issue="consent_missing,no_linked_parent"))), {"sa1", "sb"})
        self.assertEqual(set(self.by_username(self.children(issue="any"))), {"sa1", "sb"})
        self.answer(self.parent, "child_data", True)
        self.assertEqual(set(self.by_username(self.children(issue="any"))), {"sb"})

    def test_bad_issue_code_is_refused(self):
        resp = self.children(issue="nope")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "invalid_issue")

    def test_age_status_filter(self):
        self.assertEqual(set(self.by_username(self.children(age_status="minor"))), {"sa1"})
        self.assertEqual(set(self.by_username(self.children(age_status="dob_missing"))), {"sb"})
        self.assertEqual(set(self.by_username(self.children(age_status="adult"))), {"sa2"})   # asking for adults shows them

    def test_bad_age_status_is_refused(self):
        resp = self.children(age_status="old")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "invalid_age_status")

    def test_section_and_class_filters(self):
        self.assertEqual(set(self.by_username(self.children(section=str(self.secA.id), include_adults="1"))), {"sa1", "sa2"})
        self.assertEqual(set(self.by_username(self.children(section=str(self.secB.id)))), {"sb"})
        self.assertEqual(set(self.by_username(self.children(school_class=str(self.cls2.id)))), {"sb"})

    def test_search(self):
        self.assertEqual(set(self.by_username(self.children(q="sa1"))), {"sa1"})
        self.assertEqual(self.children(q="nobody-has-this-name").data["count"], 0)

    def test_paging(self):
        page1 = self.children(page_size="1")
        self.assertEqual((page1.data["count"], len(page1.data["results"]), page1.data["page"]), (2, 1, 1))
        self.assertEqual(page1.data["next"], 2)
        self.assertIsNone(page1.data["previous"])
        page2 = self.children(page_size="1", page="2")
        self.assertEqual(len(page2.data["results"]), 1)
        self.assertIsNone(page2.data["next"])
        self.assertEqual(page2.data["previous"], 1)
        self.assertNotEqual(page1.data["results"][0]["student"]["id"], page2.data["results"][0]["student"]["id"])

    def test_page_past_the_end_is_empty_not_an_error(self):
        resp = self.children(page="9")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["results"], [])

    def test_junk_paging_values_fall_back(self):
        resp = self.children(page="abc", page_size="-3")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["page"], 1)

    def test_listing_does_not_write_an_audit_row(self):
        self.children()
        self.assertNotIn("privacy.child_view", self.audit_actions())

    def test_permissions(self):
        for user in (self.teacher, self.sa1, self.parent, self.outsider):
            self.assertEqual(self.children(user).status_code, 403, user.username)
        self.assertEqual(self.children(self.principal).status_code, 200)


# ===========================================================================
# 5. admin: one child
# ===========================================================================
class ChildDetailTests(PrivacyBase):
    def get(self, student=None, user=None):
        return self.as_(user or self.admin).get(self.p("child", str((student or self.sa1).pk)))

    def test_detail_lists_each_linked_parent_per_purpose(self):
        self.link_parent2()
        self.answer(self.parent, "child_data", True)
        self.answer(self.parent2, "child_data", False)
        resp = self.get()
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual([x["purpose"] for x in resp.data["purposes"]], ["child_data", "photo_media", "child_ai_features"])
        self.assertEqual(resp.data["parent_count"], 2)
        row = self.purpose_row(resp, "child_data")
        self.assertEqual(row["decision"], "withdrawn")
        status_of = {p["id"]: p["status"] for p in row["parents"]}
        self.assertEqual(status_of, {self.parent.pk: "granted", self.parent2.pk: "withdrawn"})
        self.assertEqual(resp.data["issues"], ["consent_withdrawn"])

    def test_a_parent_who_did_not_answer_shows_no_status(self):
        resp = self.get()
        row = self.purpose_row(resp, "photo_media")
        self.assertEqual(row["decision"], "missing")
        self.assertEqual(len(row["parents"]), 1)
        self.assertIsNone(row["parents"][0]["status"])
        self.assertEqual(resp.data["issues"], ["consent_missing"])

    def test_policy_version_and_age_status(self):
        resp = self.get()
        self.assertEqual(resp.data["policy_version"], consent_svc.policy_version())
        self.assertEqual(resp.data["age_status"], "minor")
        self.assertTrue(resp.data["treated_as_minor"])

    def test_opening_the_detail_is_audit_logged_with_the_actor(self):
        self.assertNotIn("privacy.child_view", self.audit_actions())
        self.get()
        log = CampusAuditLog.objects.get(campus=self.campus, action="privacy.child_view")
        self.assertEqual(log.actor_id, self.admin.pk)
        self.assertEqual(log.target_id, str(self.sa1.pk))

    def test_a_denied_request_writes_no_audit_row(self):
        self.assertEqual(self.get(user=self.teacher).status_code, 403)
        self.assertNotIn("privacy.child_view", self.audit_actions())

    def test_student_outside_the_campus_is_404(self):
        resp = self.get(student=self.outsider)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.data.get("code"), "student_not_in_campus")
        self.assertNotIn("privacy.child_view", self.audit_actions())

    def test_non_numeric_student_id_is_400(self):
        resp = self.as_(self.admin).get(self.p("child", "abc"))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "invalid_student")

    def test_permissions(self):
        for user in (self.teacher, self.sa1, self.parent, self.outsider):
            self.assertEqual(self.get(user=user).status_code, 403, user.username)
        self.assertEqual(self.get(user=self.principal).status_code, 200)

    def test_a_student_with_no_parent_has_missing_everywhere(self):
        resp = self.get(student=self.sb)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["parent_count"], 0)
        self.assertEqual({x["decision"] for x in resp.data["purposes"]}, {"missing"})
        self.assertEqual(resp.data["issues"], ["dob_missing", "no_linked_parent"])


# ===========================================================================
# 6. parent: my children
# ===========================================================================
class MyChildrenTests(PrivacyBase):
    def get(self, user=None):
        return self.as_(user or self.parent).get(self.p("my-children"))

    def child(self, resp, student=None):
        return next(c for c in resp.data["children"] if c["student"]["id"] == (student or self.sa1).pk)

    def item(self, child, purpose):
        return next(i for i in child["purposes"] if i["purpose"] == purpose)

    def test_parent_sees_only_their_own_child(self):
        resp = self.get()
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual([c["student"]["id"] for c in resp.data["children"]], [self.sa1.pk])
        self.assertEqual(resp.data["policy_version"], consent_svc.policy_version())
        child = self.child(resp)
        self.assertEqual(child["age_status"], "minor")
        self.assertEqual([i["purpose"] for i in child["purposes"]], ["child_data", "photo_media", "child_ai_features"])
        for i in child["purposes"]:
            self.assertEqual((i["decision"], i["my_answer"], i["other_guardian_differs"]), ("missing", "missing", False))

    def test_my_own_grant_shows_as_granted(self):
        self.answer(self.parent, "child_data", True)
        item = self.item(self.child(self.get()), "child_data")
        self.assertEqual((item["decision"], item["my_answer"], item["other_guardian_differs"]), ("granted", "granted", False))

    def test_a_stale_grant_is_stale_for_me_too(self):
        self.answer(self.parent, "child_data", True, version=OLD_VERSION)
        item = self.item(self.child(self.get()), "child_data")
        self.assertEqual((item["decision"], item["my_answer"]), ("stale", "stale"))

    def test_other_guardian_withdrawal_changes_the_result_without_naming_them(self):
        self.link_parent2()
        self.answer(self.parent, "child_data", True)
        self.answer(self.parent2, "child_data", False)

        mine = self.item(self.child(self.get(self.parent)), "child_data")
        self.assertEqual((mine["decision"], mine["my_answer"], mine["other_guardian_differs"]), ("withdrawn", "granted", True))

        theirs = self.item(self.child(self.get(self.parent2)), "child_data")
        self.assertEqual((theirs["decision"], theirs["my_answer"], theirs["other_guardian_differs"]), ("withdrawn", "withdrawn", False))

    def test_the_other_parents_identity_and_answer_never_leave_the_server(self):
        self.link_parent2()
        self.parent2.first_name = "Zebulon"
        self.parent2.save()
        self.answer(self.parent2, "child_data", False)
        resp = self.get(self.parent)
        body = json.dumps(resp.data, default=str)
        self.assertNotIn("parent2", body)
        self.assertNotIn("Zebulon", body)
        self.assertNotIn(f'"id": {self.parent2.pk},', body)
        for child in resp.data["children"]:
            for i in child["purposes"]:
                self.assertEqual(set(i), {"purpose", "decision", "my_answer", "other_guardian_differs"})
        self.assertNotIn("parents", resp.data["children"][0])

    def test_a_silent_other_parent_changes_nothing(self):
        self.link_parent2()
        self.answer(self.parent, "child_data", True)
        item = self.item(self.child(self.get(self.parent)), "child_data")
        self.assertEqual((item["decision"], item["other_guardian_differs"]), ("granted", False))

    def test_a_member_with_no_linked_child_gets_an_empty_list(self):
        for user in (self.sa1, self.teacher):
            resp = self.get(user)
            self.assertEqual(resp.status_code, 200, user.username)
            self.assertEqual(resp.data["children"], [], user.username)

    def test_a_non_member_is_refused(self):
        self.assertEqual(self.get(self.outsider).status_code, 403)
        self.assertEqual(self.get(self.parent2).status_code, 403)   # not linked to anybody yet

    def test_two_children_two_entries(self):
        CampusParentLink.objects.create(campus=self.campus, parent=self.parent, student=self.sb)
        self.answer(self.parent, "child_data", True, student=self.sb)
        resp = self.get()
        self.assertEqual({c["student"]["id"] for c in resp.data["children"]}, {self.sa1.pk, self.sb.pk})
        self.assertEqual(self.item(self.child(resp, self.sb), "child_data")["decision"], "granted")
        self.assertEqual(self.item(self.child(resp, self.sa1), "child_data")["decision"], "missing")

    def test_it_does_not_write_an_audit_row(self):
        self.get()
        self.assertNotIn("privacy.child_view", self.audit_actions())


# ===========================================================================
# 7. admin: remind parents
# ===========================================================================
class RemindTests(PrivacyBase):
    def remind(self, user=None):
        return self.as_(user or self.admin).post(self.p("remind"), {}, format="json")

    def test_parents_of_children_with_missing_consent_are_notified(self):
        resp = self.remind()
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data, {"parents": 1, "children": 1})   # sa1 only; sb has no parent to ask
        self.notify.assert_called_once()
        kwargs = self.notify.call_args.kwargs
        self.assertEqual(set(kwargs["users"]), {self.parent.pk})
        self.assertEqual(kwargs["notif_type"], NotifTypes.CAMPUS_SAFETY)
        self.assertTrue(kwargs["push"])
        self.assertEqual(kwargs["data"]["event"], "consent_reminder")
        self.assertEqual(kwargs["data"]["campus_id"], str(self.campus.id))
        self.assertIn("privacy.remind", self.audit_actions())

    def test_second_reminder_inside_24_hours_is_refused(self):
        self.assertEqual(self.remind().status_code, 200)
        resp = self.remind()
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.data.get("code"), "reminder_too_soon")
        self.assertEqual(self.notify.call_count, 1)

    def test_reminder_is_allowed_again_after_the_cooldown(self):
        self.remind()
        CampusAuditLog.objects.filter(campus=self.campus, action="privacy.remind").update(
            created_at=timezone.now() - timedelta(hours=25)
        )
        self.assertEqual(self.remind().status_code, 200)
        self.assertEqual(self.notify.call_count, 2)

    def test_a_withdrawal_is_never_nagged(self):
        self.answer(self.parent, "child_data", False)
        resp = self.remind()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, {"parents": 0, "children": 0})
        self.notify.assert_not_called()

    def test_a_covered_child_is_not_nagged(self):
        self.answer(self.parent, "child_data", True)
        self.assertEqual(self.remind().data, {"parents": 0, "children": 0})
        self.notify.assert_not_called()

    def test_a_stale_grant_is_reminded(self):
        self.answer(self.parent, "child_data", True, version=OLD_VERSION)
        self.assertEqual(self.remind().data, {"parents": 1, "children": 1})

    def test_adults_are_never_targeted(self):
        CampusParentLink.objects.create(campus=self.campus, parent=self.parent2, student=self.sa2)   # adult child
        resp = self.remind()
        self.assertEqual(resp.data, {"parents": 1, "children": 1})
        self.assertEqual(set(self.notify.call_args.kwargs["users"]), {self.parent.pk})

    def test_every_linked_parent_of_a_child_is_asked(self):
        self.link_parent2()
        resp = self.remind()
        self.assertEqual(resp.data, {"parents": 2, "children": 1})
        self.assertEqual(set(self.notify.call_args.kwargs["users"]), {self.parent.pk, self.parent2.pk})

    def test_permissions(self):
        for user in (self.teacher, self.sa1, self.parent, self.outsider):
            self.assertEqual(self.remind(user).status_code, 403, user.username)
        self.notify.assert_not_called()
        self.assertNotIn("privacy.remind", self.audit_actions())
        self.assertEqual(self.remind(self.principal).status_code, 200)


# ===========================================================================
# 8. the gate other features call: child_privacy.allowed()
# ===========================================================================
class AllowedGateTests(PrivacyBase):
    def test_switch_off_always_allows(self):
        for student in (self.sa1, self.sa2, self.sb):
            for purpose in ("photo_media", "ai_features"):
                self.assertTrue(svc.allowed(self.campus, student, purpose), f"{student.username}/{purpose}")

    def test_switch_off_allows_even_after_a_withdrawal(self):
        self.answer(self.parent, "photo_media", False)
        self.assertTrue(svc.allowed(self.campus, self.sa1, "photo_media"))

    def test_a_purpose_that_is_not_gated_is_always_allowed(self):
        self.policy(enforce_guardian_consent=True)
        for purpose in ("communications", "child_data", "terms_privacy", "anything"):
            self.assertTrue(svc.allowed(self.campus, self.sa1, purpose), purpose)

    def test_minor_needs_the_parents_grant(self):
        self.policy(enforce_guardian_consent=True)
        self.assertFalse(svc.allowed(self.campus, self.sa1, "photo_media"))
        self.answer(self.parent, "photo_media", True)
        self.assertTrue(svc.allowed(self.campus, self.sa1, "photo_media"))

    def test_ai_for_a_minor_is_decided_by_child_ai_features(self):
        self.policy(enforce_guardian_consent=True)
        self.answer(self.parent, "photo_media", True)
        self.assertFalse(svc.allowed(self.campus, self.sa1, "ai_features"))   # photo consent does not cover AI
        self.answer(self.parent, "child_ai_features", True)
        self.assertTrue(svc.allowed(self.campus, self.sa1, "ai_features"))
        self.assertTrue(svc.allowed(self.campus, self.sa1, "photo_media"))

    def test_a_minors_own_old_grant_row_does_not_open_the_gate(self):
        self.policy(enforce_guardian_consent=True)
        CampusConsentRecord.objects.create(
            campus=self.campus, subject=self.sa1, purpose="ai_features", status="granted",
            policy_version=consent_svc.policy_version(),
        )   # as if an old row exists from before the rule was introduced
        self.assertFalse(svc.allowed(self.campus, self.sa1, "ai_features"))

    def test_one_parent_withdrawing_closes_the_gate(self):
        self.policy(enforce_guardian_consent=True)
        self.link_parent2()
        self.answer(self.parent, "photo_media", True)
        self.assertTrue(svc.allowed(self.campus, self.sa1, "photo_media"))
        self.answer(self.parent2, "photo_media", False)
        self.assertFalse(svc.allowed(self.campus, self.sa1, "photo_media"))

    def test_a_stale_grant_does_not_open_the_gate(self):
        self.policy(enforce_guardian_consent=True)
        self.answer(self.parent, "photo_media", True, version=OLD_VERSION)
        self.assertFalse(svc.allowed(self.campus, self.sa1, "photo_media"))

    def test_a_minor_with_no_linked_parent_is_not_allowed(self):
        self.policy(enforce_guardian_consent=True)
        User.objects.filter(pk=self.sb.pk).update(date_of_birth=timezone.localdate() - timedelta(days=365 * 10))
        self.sb.refresh_from_db()
        self.assertFalse(svc.allowed(self.campus, self.sb, "photo_media"))

    def test_adult_needs_their_own_grant_and_can_take_it_back(self):
        self.policy(enforce_guardian_consent=True)
        self.assertFalse(svc.allowed(self.campus, self.sa2, "photo_media"))
        self.rec(self.sa2, "photo_media", True)
        self.assertTrue(svc.allowed(self.campus, self.sa2, "photo_media"))
        self.rec(self.sa2, "photo_media", False)
        self.assertFalse(svc.allowed(self.campus, self.sa2, "photo_media"))

    def test_missing_dob_follows_the_unknown_dob_switch(self):
        self.policy(enforce_guardian_consent=True)   # unknown_dob_is_minor defaults to True
        self.assertFalse(svc.allowed(self.campus, self.sb, "ai_features"))
        self.rec(self.sb, "ai_features", True)       # their own answer: not enough while treated as a minor
        self.assertFalse(svc.allowed(self.campus, self.sb, "ai_features"))
        self.policy(unknown_dob_is_minor=False)                              # now treated like an adult: own answer counts
        self.assertTrue(svc.allowed(self.campus, self.sb, "ai_features"))

    def test_another_campuss_switch_does_not_apply(self):
        other = Campus.objects.create(name="Other", created_by=self.admin)
        CampusPrivacyPolicy.objects.create(campus=other, enforce_guardian_consent=True)
        self.assertTrue(svc.allowed(self.campus, self.sa1, "photo_media"))   # this campus never switched it on
