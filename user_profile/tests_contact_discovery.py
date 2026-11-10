"""
S4b - "Find me by phone / e-mail" is now a real thing: POST /profile/contacts/match/.

NOT RUN when written (read-only session). Run: python manage.py test user_profile.tests_contact_discovery

Covers: phone / e-mail normalisation, request limits (refused whole with 400), the privacy switches
(find_by_phone / find_by_email), minors never found, only VERIFIED phone / e-mail match, "hide from search",
blocks (both directions), the caller and inactive accounts never returned, a user listed under several refs,
and that the response carries no more than the search card (no phone / e-mail echoed back).
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import NotificationPreference
from user_profile import contact_discovery as cd
from user_profile.models import BlockUser, Follow, PrivacySettings

User = get_user_model()
URL_NAME = "contact-match"


def _years_ago(n):
    today = timezone.localdate()
    try:
        return today.replace(year=today.year - n)
    except ValueError:
        return (today - timedelta(days=1)).replace(year=today.year - n)


def _user(name, phone=None, verified=True, email_verified=True, dob=None, **kw):
    user = User.objects.create_user(
        username=name, email=f"{name}@example.com", password="pass12345", date_of_birth=dob, **kw
    )
    if phone:
        user.phone = phone
        user.phone_verified = verified
        user.save(update_fields=["phone", "phone_verified"])
    if email_verified:
        pref, _ = NotificationPreference.objects.get_or_create(user=user)
        pref.email_verified_address = user.email
        pref.save(update_fields=["email_verified_address"])
    return user


def _set(user, **fields):
    row = PrivacySettings.for_user(user)
    for key, value in fields.items():
        setattr(row, key, value)
    row.save()


# ======================================================================================================
# normalisation + limits (no DB)
# ======================================================================================================
class NormaliseTests(SimpleTestCase):
    def test_phone_shapes(self):
        self.assertEqual(cd.normalize_phone("+91 98765-43210"), "+919876543210")
        self.assertEqual(cd.normalize_phone("9876543210"), "+919876543210")      # bare 10 digits -> default country
        self.assertEqual(cd.normalize_phone("09876543210"), "+919876543210")     # trunk 0
        self.assertEqual(cd.normalize_phone("00919876543210"), "+919876543210")  # 00 international prefix
        self.assertEqual(cd.normalize_phone("(+1) 415 555 0100"), "+14155550100")

    def test_phone_garbage_is_none(self):
        for bad in (None, "", "abc", "123", "+0123456789", "1" * 20):
            self.assertIsNone(cd.normalize_phone(bad), bad)

    @override_settings(CONTACT_DISCOVERY_DEFAULT_COUNTRY_CODE="44")
    def test_default_country_is_configurable(self):
        self.assertEqual(cd.normalize_phone("07911123456"), "+447911123456")

    def test_email(self):
        self.assertEqual(cd.normalize_email("  Asha@Example.COM "), "asha@example.com")
        for bad in (None, "", "no-at-sign", "a@b", "a b@c.com", "x" * 260 + "@a.com"):
            self.assertIsNone(cd.normalize_email(bad), bad)


class ParseTests(SimpleTestCase):
    def test_ok_body(self):
        out = cd.parse_contacts({"contacts": [{"ref": "c1", "phones": ["9876543210", "bad"], "emails": ["A@b.co"]}]})
        self.assertEqual(out, [("c1", {"+919876543210"}, {"a@b.co"})])

    def test_missing_ref_falls_back_to_the_index(self):
        out = cd.parse_contacts({"contacts": [{"phones": ["9876543210"]}, {"emails": ["a@b.co"]}]})
        self.assertEqual([ref for ref, _p, _e in out], ["0", "1"])

    def test_bad_shapes_raise(self):
        for body in (None, [], {}, {"contacts": []}, {"contacts": "x"}, {"contacts": ["x"]},
                     {"contacts": [{"phones": "9876543210"}]}):
            with self.assertRaises(cd.ContactMatchError, msg=repr(body)):
                cd.parse_contacts(body)

    def test_limits_refuse_the_whole_request(self):
        with self.assertRaises(cd.ContactMatchError) as ctx:
            cd.parse_contacts({"contacts": [{"phones": []}] * (cd.MAX_CONTACTS + 1)})
        self.assertEqual(ctx.exception.code, "too_many_contacts")
        with self.assertRaises(cd.ContactMatchError) as ctx:
            cd.parse_contacts({"contacts": [{"phones": ["9876543210"] * (cd.MAX_IDS_PER_CONTACT * 2 + 1)}]})
        self.assertEqual(ctx.exception.code, "too_many_identifiers")
        per = cd.MAX_IDS_PER_CONTACT * 2
        many = [{"phones": ["9876543210"] * per} for _ in range(cd.MAX_IDENTIFIERS // per + 1)]
        with self.assertRaises(cd.ContactMatchError) as ctx:
            cd.parse_contacts({"contacts": many})
        self.assertEqual(ctx.exception.code, "too_many_identifiers")


# ======================================================================================================
# endpoint
# ======================================================================================================
class _Base(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = _user("cm_me", phone="+919000000001")
        self.client.force_authenticate(self.me)
        self.url = reverse(URL_NAME)

    def match(self, *contacts):
        return self.client.post(self.url, {"contacts": list(contacts)}, format="json")

    def ids(self, res):
        self.assertEqual(res.status_code, 200, res.data)
        return {row["user"]["id"] for row in res.data["data"]}


class MatchTests(_Base):
    def setUp(self):
        super().setUp()
        self.asha = _user("cm_asha", phone="+919876543210")

    def test_match_by_verified_phone_in_any_format(self):
        for raw in ("+919876543210", "9876543210", "098765 43210", "+91-98765-43210"):
            with self.subTest(raw=raw):
                self.assertEqual(self.ids(self.match({"ref": "a", "phones": [raw]})), {self.asha.id})

    def test_match_by_verified_email_case_insensitive(self):
        self.assertEqual(self.ids(self.match({"ref": "a", "emails": ["CM_Asha@Example.com"]})), {self.asha.id})

    def test_response_is_the_search_card_only(self):
        res = self.match({"ref": "r1", "phones": ["9876543210"]})
        row = res.data["data"][0]
        self.assertEqual(row["ref"], "r1")
        self.assertEqual(set(row["user"]), {"id", "username", "first_name", "last_name", "profile_photo"})
        self.assertNotIn("9876543210", str(res.data))
        self.assertNotIn("example.com", str(res.data))

    def test_unknown_numbers_and_emails_match_nothing(self):
        self.assertEqual(self.ids(self.match({"phones": ["9111111111"], "emails": ["nobody@nowhere.com"]})), set())

    def test_phone_that_is_not_verified_is_never_matched(self):
        _user("cm_unver", phone="+919222222222", verified=False)
        self.assertEqual(self.ids(self.match({"phones": ["9222222222"]})), set())

    def test_email_that_is_not_verified_is_never_matched(self):
        _user("cm_unver_mail", email_verified=False)
        self.assertEqual(self.ids(self.match({"emails": ["cm_unver_mail@example.com"]})), set())

    def test_changed_email_needs_re_verification(self):
        u = _user("cm_changed")
        User.objects.filter(pk=u.pk).update(email="new-address@example.com")  # verified address is the old one
        self.assertEqual(self.ids(self.match({"emails": ["new-address@example.com"]})), set())

    def test_the_caller_is_never_returned(self):
        self.assertEqual(self.ids(self.match({"phones": ["+919000000001"], "emails": ["cm_me@example.com"]})), set())

    def test_inactive_account_is_never_returned(self):
        User.objects.filter(pk=self.asha.pk).update(is_active=False)
        self.assertEqual(self.ids(self.match({"phones": ["9876543210"]})), set())

    def test_same_user_by_phone_and_email_in_one_contact_appears_once(self):
        res = self.match({"ref": "a", "phones": ["9876543210"], "emails": ["cm_asha@example.com"]})
        self.assertEqual(len(res.data["data"]), 1)

    def test_same_user_under_two_refs_appears_once_per_ref(self):
        res = self.match({"ref": "a", "phones": ["9876543210"]}, {"ref": "b", "emails": ["cm_asha@example.com"]})
        self.assertEqual([row["ref"] for row in res.data["data"]], ["a", "b"])


class PrivacySwitchTests(_Base):
    def setUp(self):
        super().setUp()
        self.asha = _user("cm_asha", phone="+919876543210")

    def test_find_by_phone_off_hides_the_phone_match_only(self):
        _set(self.asha, find_by_phone=False)
        self.assertEqual(self.ids(self.match({"phones": ["9876543210"]})), set())
        self.assertEqual(self.ids(self.match({"emails": ["cm_asha@example.com"]})), {self.asha.id})

    def test_find_by_email_off_hides_the_email_match_only(self):
        _set(self.asha, find_by_email=False)
        self.assertEqual(self.ids(self.match({"emails": ["cm_asha@example.com"]})), set())
        self.assertEqual(self.ids(self.match({"phones": ["9876543210"]})), {self.asha.id})

    def test_turning_the_switch_back_on_makes_them_findable_again(self):
        _set(self.asha, find_by_phone=False)
        self.assertEqual(self.ids(self.match({"phones": ["9876543210"]})), set())
        _set(self.asha, find_by_phone=True)
        self.assertEqual(self.ids(self.match({"phones": ["9876543210"]})), {self.asha.id})

    def test_a_minor_is_never_found_even_with_the_switches_on(self):
        minor = _user("cm_minor", phone="+919333333333", dob=_years_ago(15))
        _set(minor, find_by_phone=True, find_by_email=True)
        got = self.ids(self.match({"phones": ["9333333333"], "emails": ["cm_minor@example.com"]}))
        self.assertEqual(got, set())

    @override_settings(PRIVACY_MINOR_LOCKS=False)
    def test_with_the_minor_locks_off_a_minor_follows_their_own_switch(self):
        minor = _user("cm_minor2", phone="+919333333334", dob=_years_ago(15))
        self.assertEqual(self.ids(self.match({"phones": ["9333333334"]})), {minor.id})

    def test_hide_from_search_hides_them_unless_there_is_a_follow_link(self):
        _set(self.asha, hide_from_search=True)
        self.assertEqual(self.ids(self.match({"phones": ["9876543210"]})), set())
        Follow.objects.create(follower=self.me, following=self.asha, status=Follow.Status.ACCEPTED)
        self.assertEqual(self.ids(self.match({"phones": ["9876543210"]})), {self.asha.id})

    def test_a_pending_follow_request_does_not_lift_hide_from_search(self):
        _set(self.asha, hide_from_search=True)
        Follow.objects.create(follower=self.me, following=self.asha, status=Follow.Status.PENDING)
        self.assertEqual(self.ids(self.match({"phones": ["9876543210"]})), set())

    def test_blocked_either_way_is_never_returned(self):
        BlockUser.objects.create(blocker=self.me, blocked=self.asha)
        self.assertEqual(self.ids(self.match({"phones": ["9876543210"]})), set())
        BlockUser.objects.all().delete()
        BlockUser.objects.create(blocker=self.asha, blocked=self.me)
        self.assertEqual(self.ids(self.match({"phones": ["9876543210"]})), set())

    def test_discoverable_owner_ids_helper(self):
        from user_profile import privacy

        _set(self.asha, find_by_phone=False)
        other = _user("cm_other", phone="+919444444444")
        minor = _user("cm_minor3", dob=_years_ago(15))
        ids = [self.asha.id, other.id, minor.id]
        self.assertEqual(privacy.discoverable_owner_ids(ids, "phone"), {other.id})
        self.assertEqual(privacy.discoverable_owner_ids(ids, "email"), {self.asha.id, other.id})
        with self.assertRaises(ValueError):
            privacy.discoverable_owner_ids(ids, "fax")


class RequestRuleTests(_Base):
    def test_login_required(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.post(self.url, {"contacts": [{"phones": ["9876543210"]}]}, format="json").status_code,
                      (401, 403))

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_empty_and_malformed_bodies_are_400_with_a_code(self):
        res = self.client.post(self.url, {}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "contacts_required")
        res = self.client.post(self.url, {"contacts": ["x"]}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "bad_contact")

    def test_over_the_limit_is_refused_whole(self):
        res = self.match(*[{"ref": str(i), "phones": ["9876543210"]} for i in range(cd.MAX_CONTACTS + 1)])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "too_many_contacts")

    def test_nothing_that_was_sent_is_stored(self):
        before = (User.objects.count(), PrivacySettings.objects.count())
        self.match({"phones": ["9876543210"], "emails": ["x@y.co"]})
        self.assertEqual((User.objects.count(), PrivacySettings.objects.count()), before)

    def test_matches_are_capped(self):
        for i in range(5):
            _user(f"cm_cap{i}", phone=f"+9198000000{i:02d}")
        with override_settings():
            old = cd.MAX_MATCHES
            cd.MAX_MATCHES = 3
            try:
                res = self.match(*[{"ref": str(i), "phones": [f"98000000{i:02d}"]} for i in range(5)])
            finally:
                cd.MAX_MATCHES = old
        self.assertEqual(len(res.data["data"]), 3)
