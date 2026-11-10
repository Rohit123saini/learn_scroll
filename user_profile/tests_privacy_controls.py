"""
user_profile/tests_privacy_controls.py  (S4 -- Privacy controls)

Checks that every control of `PrivacySettings` really changes what the server does, not only the screen:

  * privacy.py helpers      allows / allowed_owner_ids / connections / defaults / cache / exclude_hidden_from_search
  * minor locks             a person under 18 cannot be opened to `everyone` (effective value + PATCH refused)
  * GET / PATCH /profile/privacy/   defaults without a row, partial PATCH, bad value, isolation between users
  * enforcement points      new chat, 1-1 call, group add, online status, followers list, people search

Written without running anything (read-only task). Run:  python manage.py test user_profile.tests_privacy_controls
"""
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from user_profile import privacy
from user_profile.models import Follow, PrivacySettings

User = get_user_model()

FOLLOW_FN = "message.message_requests.is_following"  # lets a first DM through the message-request gate


def _user(name, dob=None, **kw):
    return User.objects.create_user(
        username=name, email=f"{name}@example.com", password="pass12345", date_of_birth=dob, **kw
    )


def _years_ago(n):
    today = timezone.localdate()
    try:
        return today.replace(year=today.year - n)
    except ValueError:  # 29 Feb
        return (today - timedelta(days=1)).replace(year=today.year - n)


ADULT_DOB = _years_ago(30)
MINOR_DOB = _years_ago(15)


def _follow(a, b, accepted=True):
    return Follow.objects.create(
        follower=a, following=b, status=Follow.Status.ACCEPTED if accepted else Follow.Status.PENDING
    )


def _set(user, **fields):
    row = PrivacySettings.for_user(user)
    for k, v in fields.items():
        setattr(row, k, v)
    row.save()
    return row


class _Base:
    def setUp(self):
        cache.clear()


# ======================================================================================================
# helpers
# ======================================================================================================
class PrivacyHelperTests(_Base, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = _user("pv_owner")
        self.viewer = _user("pv_viewer")
        self.other = _user("pv_other")

    def test_no_row_means_everything_open(self):
        for field in privacy.AUDIENCE_FIELDS:
            self.assertEqual(privacy.level_of(self.owner, field), privacy.EVERYONE)
            self.assertTrue(privacy.allows(self.owner, self.viewer, field))
        self.assertFalse(privacy.is_hidden_from_search(self.owner))
        self.assertTrue(privacy.discoverable_by_phone(self.owner))
        self.assertTrue(privacy.discoverable_by_email(self.owner))
        self.assertFalse(PrivacySettings.objects.filter(user=self.owner).exists())  # reading never writes

    def test_nobody_refuses_everyone_else_but_not_the_owner(self):
        _set(self.owner, message_from="nobody")
        self.assertFalse(privacy.can_start_chat(self.owner, self.viewer))
        self.assertTrue(privacy.can_start_chat(self.owner, self.owner))

    def test_connections_needs_an_accepted_follow_in_either_direction(self):
        _set(self.owner, call_from="connections")
        self.assertFalse(privacy.can_call(self.owner, self.viewer))
        _follow(self.viewer, self.owner, accepted=False)  # pending request is not a connection
        self.assertFalse(privacy.can_call(self.owner, self.viewer))
        Follow.objects.all().delete()
        _follow(self.viewer, self.owner)  # viewer follows owner
        self.assertTrue(privacy.can_call(self.owner, self.viewer))
        Follow.objects.all().delete()
        _follow(self.owner, self.viewer)  # owner follows viewer
        self.assertTrue(privacy.can_call(self.owner, self.viewer))

    def test_each_named_shortcut_reads_its_own_control(self):
        pairs = [
            (privacy.can_start_chat, "message_from"),
            (privacy.can_call, "call_from"),
            (privacy.can_tag, "tag_from"),
            (privacy.can_mention, "mention_from"),
            (privacy.can_add_to_group, "group_add_from"),
            (privacy.can_see_online_status, "online_status_from"),
            (privacy.can_see_follow_lists, "followers_list_from"),
        ]
        for fn, field in pairs:
            PrivacySettings.objects.filter(user=self.owner).delete()
            cache.clear()
            _set(self.owner, **{field: "nobody"})
            self.assertFalse(fn(self.owner, self.viewer), field)
            # the other six stay open
            for other_fn, other_field in pairs:
                if other_field != field:
                    self.assertTrue(other_fn(self.owner, self.viewer), f"{field} must not touch {other_field}")

    def test_unknown_control_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            privacy.level_of(self.owner, "no_such_control")
        with self.assertRaises(ValueError):
            privacy.allowed_owner_ids(self.viewer, [self.owner.id], "no_such_control")

    def test_hand_edited_bad_level_falls_back_to_default(self):
        _set(self.owner, message_from="garbage")
        cache.clear()
        self.assertEqual(privacy.level_of(self.owner, "message_from"), privacy.EVERYONE)

    def test_save_drops_the_cache_at_once(self):
        self.assertTrue(privacy.can_start_chat(self.owner, self.viewer))  # now cached as "everyone"
        _set(self.owner, message_from="nobody")
        self.assertFalse(privacy.can_start_chat(self.owner, self.viewer))

    def test_allowed_owner_ids_many_owners(self):
        o_all, o_conn, o_none = self.owner, self.other, _user("pv_third")
        _set(o_conn, group_add_from="connections")
        _set(o_none, group_add_from="nobody")
        ids = [o_all.id, o_conn.id, o_none.id]
        self.assertEqual(privacy.allowed_owner_ids(self.viewer, ids, "group_add_from"), {o_all.id})
        _follow(self.viewer, o_conn)
        self.assertEqual(privacy.allowed_owner_ids(self.viewer, ids, "group_add_from"), {o_all.id, o_conn.id})

    def test_allowed_owner_ids_never_limits_a_person_from_themselves(self):
        _set(self.viewer, group_add_from="nobody")
        self.assertEqual(privacy.allowed_owner_ids(self.viewer, [self.viewer.id], "group_add_from"), {self.viewer.id})

    def test_new_chat_blocked_only_before_a_chat_exists(self):
        from message.models import Conversation

        _set(self.owner, message_from="nobody")
        self.assertTrue(privacy.new_chat_blocked(self.viewer, self.owner))
        with mock.patch(FOLLOW_FN, return_value=True):
            Conversation.get_or_create_private(self.viewer, self.owner)
        self.assertFalse(privacy.new_chat_blocked(self.viewer, self.owner))  # an existing chat is never cut off

    def test_exclude_hidden_from_search(self):
        _set(self.owner, hide_from_search=True)
        qs = User.objects.filter(username__startswith="pv_")
        names = set(privacy.exclude_hidden_from_search(qs, self.viewer).values_list("username", flat=True))
        self.assertNotIn("pv_owner", names)
        self.assertIn("pv_other", names)
        _follow(self.viewer, self.owner)  # an accepted follow keeps them findable
        names = set(privacy.exclude_hidden_from_search(qs, self.viewer).values_list("username", flat=True))
        self.assertIn("pv_owner", names)

    def test_hidden_person_still_finds_themselves(self):
        _set(self.owner, hide_from_search=True)
        qs = User.objects.filter(username__startswith="pv_")
        names = set(privacy.exclude_hidden_from_search(qs, self.owner).values_list("username", flat=True))
        self.assertIn("pv_owner", names)


# ======================================================================================================
# minors
# ======================================================================================================
class MinorLockTests(_Base, TestCase):
    def setUp(self):
        super().setUp()
        self.minor = _user("pm_minor", dob=MINOR_DOB)
        self.adult = _user("pm_adult", dob=ADULT_DOB)
        self.unknown = _user("pm_unknown")  # no date of birth: neither minor nor verified adult
        self.stranger = _user("pm_stranger", dob=ADULT_DOB)

    def test_who_is_a_minor(self):
        self.assertTrue(privacy.is_minor_user(self.minor))
        self.assertFalse(privacy.is_minor_user(self.adult))
        self.assertFalse(privacy.is_minor_user(self.unknown))
        self.assertFalse(privacy.is_minor_user(None))
        self.assertEqual(privacy.minor_ids_among([self.minor.id, self.adult.id, self.unknown.id]), {self.minor.id})

    def test_default_minor_is_not_open_to_strangers(self):
        for field in privacy.MINOR_FLOOR_FIELDS:
            self.assertEqual(privacy.level_of(self.minor, field), privacy.CONNECTIONS, field)
            self.assertFalse(privacy.allows(self.minor, self.stranger, field), field)
        self.assertFalse(privacy.discoverable_by_phone(self.minor))
        self.assertFalse(privacy.discoverable_by_email(self.minor))

    def test_followers_list_and_search_are_not_locked(self):
        self.assertEqual(privacy.level_of(self.minor, "followers_list_from"), privacy.EVERYONE)
        self.assertFalse(privacy.is_hidden_from_search(self.minor))

    def test_connection_can_still_reach_a_minor(self):
        _follow(self.stranger, self.minor)
        self.assertTrue(privacy.can_start_chat(self.minor, self.stranger))
        self.assertTrue(privacy.can_call(self.minor, self.stranger))

    def test_minor_can_choose_stricter_but_stored_choice_is_untouched(self):
        _set(self.minor, message_from="nobody")
        self.assertFalse(privacy.can_start_chat(self.minor, self.stranger))
        _follow(self.stranger, self.minor)
        self.assertFalse(privacy.can_start_chat(self.minor, self.stranger))  # "nobody" wins over a follow
        # a stored "everyone" is not rewritten
        _set(self.minor, call_from="everyone")
        self.assertEqual(PrivacySettings.objects.get(user=self.minor).call_from, "everyone")

    def test_allowed_owner_ids_applies_the_floor(self):
        got = privacy.allowed_owner_ids(self.stranger, [self.minor.id, self.adult.id], "group_add_from")
        self.assertEqual(got, {self.adult.id})
        _follow(self.stranger, self.minor)
        got = privacy.allowed_owner_ids(self.stranger, [self.minor.id, self.adult.id], "group_add_from")
        self.assertEqual(got, {self.minor.id, self.adult.id})

    def test_followers_list_control_has_no_floor_in_allowed_owner_ids(self):
        got = privacy.allowed_owner_ids(self.stranger, [self.minor.id], "followers_list_from")
        self.assertEqual(got, {self.minor.id})

    @override_settings(PRIVACY_MINOR_LOCKS=False)
    def test_switch_turns_the_whole_rule_off(self):
        self.assertFalse(privacy.is_minor_user(self.minor))
        self.assertTrue(privacy.can_start_chat(self.minor, self.stranger))
        self.assertEqual(privacy.minor_ids_among([self.minor.id]), set())
        self.assertEqual(privacy.minor_violations(self.minor, {"message_from": "everyone"}), {})

    def test_turning_eighteen_gives_the_stored_choice_back(self):
        _set(self.minor, message_from="everyone")
        self.assertFalse(privacy.can_start_chat(self.minor, self.stranger))
        User.objects.filter(pk=self.minor.pk).update(date_of_birth=ADULT_DOB)
        cache.clear()
        self.assertTrue(privacy.can_start_chat(self.minor, self.stranger))

    def test_minor_violations(self):
        bad = privacy.minor_violations(self.minor, {"message_from": "everyone", "find_by_phone": True, "call_from": "nobody"})
        self.assertEqual(set(bad), {"message_from", "find_by_phone"})
        self.assertEqual(privacy.minor_violations(self.minor, {"find_by_email": "true"}).keys(), {"find_by_email"})
        self.assertEqual(privacy.minor_violations(self.minor, {"find_by_email": False, "tag_from": "connections"}), {})
        self.assertEqual(privacy.minor_violations(self.adult, {"message_from": "everyone"}), {})
        self.assertEqual(privacy.minor_violations(self.minor, {}), {})

    def test_apply_minor_floor_is_a_pure_copy(self):
        src = dict(privacy.DEFAULTS)
        out = privacy.apply_minor_floor(src)
        self.assertEqual(src["message_from"], "everyone")
        self.assertEqual(out["message_from"], "connections")
        self.assertEqual(out["followers_list_from"], "everyone")
        self.assertFalse(out["find_by_phone"])

    def test_locks_for(self):
        self.assertEqual(privacy.locks_for(self.adult), {"is_minor": False, "locked_fields": []})
        got = privacy.locks_for(self.minor)
        self.assertTrue(got["is_minor"])
        self.assertEqual(set(got["locked_fields"]), set(privacy.MINOR_LOCKED_FIELDS))


# ======================================================================================================
# GET / PATCH /profile/privacy/
# ======================================================================================================
class PrivacyApiTests(_Base, APITestCase):
    def setUp(self):
        super().setUp()
        self.me = _user("pa_me", dob=ADULT_DOB)
        self.url = reverse("privacy-settings")

    def test_login_needed(self):
        self.assertIn(self.client.get(self.url).status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_get_without_row_returns_defaults_and_writes_nothing(self):
        self.client.force_authenticate(self.me)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        d = r.data["data"]
        for field in privacy.AUDIENCE_FIELDS:
            self.assertEqual(d[field], "everyone")
        self.assertFalse(d["hide_from_search"])
        self.assertTrue(d["find_by_phone"])
        self.assertFalse(d["is_private"])
        self.assertFalse(d["is_minor"])
        self.assertEqual(d["locked_fields"], [])
        self.assertFalse(PrivacySettings.objects.filter(user=self.me).exists())

    def test_get_shows_private_account_flag(self):
        User.objects.filter(pk=self.me.pk).update(is_private=True)
        self.client.force_authenticate(self.me)
        self.assertTrue(self.client.get(self.url).data["data"]["is_private"])

    def test_patch_changes_only_what_was_sent(self):
        self.client.force_authenticate(self.me)
        r = self.client.patch(self.url, {"message_from": "connections", "hide_from_search": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        row = PrivacySettings.objects.get(user=self.me)
        self.assertEqual(row.message_from, "connections")
        self.assertTrue(row.hide_from_search)
        self.assertEqual(row.call_from, "everyone")
        self.assertEqual(r.data["data"]["message_from"], "connections")
        # second PATCH keeps the first change
        self.client.patch(self.url, {"call_from": "nobody"}, format="json")
        row.refresh_from_db()
        self.assertEqual((row.message_from, row.call_from), ("connections", "nobody"))

    def test_patch_takes_effect_on_the_server_at_once(self):
        self.client.force_authenticate(self.me)
        self.client.patch(self.url, {"tag_from": "nobody"}, format="json")
        self.assertFalse(privacy.can_tag(self.me, _user("pa_x")))

    def test_patch_rejects_unknown_level(self):
        self.client.force_authenticate(self.me)
        r = self.client.patch(self.url, {"message_from": "friends-of-friends"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.data["status"])
        self.assertIn("message_from", r.data["errors"])
        self.assertEqual(PrivacySettings.objects.get(user=self.me).message_from, "everyone")

    def test_patch_cannot_change_read_only_fields_or_other_users(self):
        other = _user("pa_other")
        self.client.force_authenticate(self.me)
        self.client.patch(self.url, {"user": other.id, "is_private": True}, format="json")
        self.assertFalse(PrivacySettings.objects.filter(user=other).exists())
        self.me.refresh_from_db()
        self.assertFalse(self.me.is_private)  # the private account switch is still changed through /profile/

    def test_two_people_never_see_each_others_settings(self):
        other = _user("pa_other2")
        _set(other, message_from="nobody")
        self.client.force_authenticate(self.me)
        self.assertEqual(self.client.get(self.url).data["data"]["message_from"], "everyone")

    def test_minor_sees_effective_values_and_locked_fields(self):
        minor = _user("pa_minor", dob=MINOR_DOB)
        self.client.force_authenticate(minor)
        d = self.client.get(self.url).data["data"]
        self.assertTrue(d["is_minor"])
        self.assertEqual(d["message_from"], "connections")
        self.assertEqual(d["followers_list_from"], "everyone")
        self.assertFalse(d["find_by_phone"])
        self.assertEqual(set(d["locked_fields"]), set(privacy.MINOR_LOCKED_FIELDS))

    def test_minor_cannot_open_up_but_can_close_down(self):
        minor = _user("pa_minor2", dob=MINOR_DOB)
        self.client.force_authenticate(minor)
        r = self.client.patch(self.url, {"message_from": "everyone", "find_by_phone": True}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["code"], privacy.MINOR_LOCKED_CODE)
        self.assertEqual(set(r.data["errors"]), {"message_from", "find_by_phone"})
        self.assertFalse(PrivacySettings.objects.filter(user=minor).exists())  # nothing half-saved
        ok = self.client.patch(self.url, {"message_from": "nobody", "hide_from_search": True}, format="json")
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(PrivacySettings.objects.get(user=minor).message_from, "nobody")

    def test_minor_can_resend_the_value_the_screen_showed(self):
        minor = _user("pa_minor3", dob=MINOR_DOB)
        self.client.force_authenticate(minor)
        r = self.client.patch(self.url, {"message_from": "connections", "find_by_email": False}, format="json")
        self.assertEqual(r.status_code, 200, r.data)

    @override_settings(PRIVACY_MINOR_LOCKS=False)
    def test_minor_lock_switch_off_lets_a_minor_choose_everyone(self):
        minor = _user("pa_minor4", dob=MINOR_DOB)
        self.client.force_authenticate(minor)
        r = self.client.patch(self.url, {"message_from": "everyone"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)


# ======================================================================================================
# enforcement points -- the control has to change what the server does
# ======================================================================================================
class PrivacyEnforcementTests(_Base, APITestCase):
    def setUp(self):
        super().setUp()
        self.owner = _user("pe_owner", dob=ADULT_DOB)
        self.viewer = _user("pe_viewer", dob=ADULT_DOB)

    # ---- new chat ----
    def _start_chat(self, who, target):
        self.client.force_authenticate(who)
        with mock.patch(FOLLOW_FN, return_value=True):
            return self.client.post(reverse("conversation-start-private"), {"user_id": target.id}, format="json")

    def test_new_chat_refused_with_code_when_owner_limited_it(self):
        _set(self.owner, message_from="nobody")
        r = self._start_chat(self.viewer, self.owner)
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(r.data["code"], privacy.MESSAGE_BLOCKED_CODE)

    def test_new_chat_allowed_for_a_connection(self):
        _set(self.owner, message_from="connections")
        _follow(self.viewer, self.owner)
        self.assertIn(self._start_chat(self.viewer, self.owner).status_code, (200, 201))

    def test_existing_chat_is_not_cut_off(self):
        from message.models import Conversation

        with mock.patch(FOLLOW_FN, return_value=True):
            Conversation.get_or_create_private(self.viewer, self.owner)
        _set(self.owner, message_from="nobody")
        self.assertIn(self._start_chat(self.viewer, self.owner).status_code, (200, 201))

    def test_owner_can_still_start_a_chat_themselves(self):
        _set(self.owner, message_from="nobody")
        self.assertIn(self._start_chat(self.owner, self.viewer).status_code, (200, 201))

    def test_minor_default_blocks_a_new_chat_from_a_stranger(self):
        minor = _user("pe_minor", dob=MINOR_DOB)
        r = self._start_chat(self.viewer, minor)
        self.assertEqual(r.status_code, 403, r.data)

    # ---- call ----
    def test_call_refused_with_code_when_owner_limited_it(self):
        from message.models import Conversation

        with mock.patch(FOLLOW_FN, return_value=True):
            conv, _created = Conversation.get_or_create_private(self.viewer, self.owner)
        _set(self.owner, call_from="nobody")
        self.client.force_authenticate(self.viewer)
        r = self.client.post(reverse("call-initiate"), {"conversation_id": str(conv.id), "type": "audio"}, format="json")
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(r.data["code"], privacy.CALL_BLOCKED_CODE)

    # ---- group add ----
    def test_group_add_skips_the_person_who_declined_and_reports_them(self):
        from message import services as chat_services

        _set(self.owner, group_add_from="nobody")
        third = _user("pe_third", dob=ADULT_DOB)
        group = chat_services.create_group(created_by=self.viewer, name="pe group", member_ids=[])
        declined = []
        added = chat_services.add_members_to_group(
            group=group, actor=self.viewer, user_ids=[self.owner.id, third.id], declined_out=declined
        )
        self.assertEqual({u.id for u in added}, {third.id})
        self.assertEqual({int(x) for x in declined}, {self.owner.id})

    def test_group_create_quietly_leaves_out_the_person_who_declined(self):
        from message import services as chat_services

        _set(self.owner, group_add_from="nobody")
        group = chat_services.create_group(created_by=self.viewer, name="pe group 2", member_ids=[self.owner.id])
        self.assertFalse(group.group_members.filter(user=self.owner).exists())

    # ---- online status ----
    def test_online_status_hidden_when_limited(self):
        from message.models import UserPresence

        UserPresence.objects.update_or_create(user=self.owner, defaults={"is_online": True})
        self.client.force_authenticate(self.viewer)
        self.assertTrue(self.client.get(reverse("user-presence", args=[self.owner.id])).data["is_online"])
        _set(self.owner, online_status_from="nobody")
        r = self.client.get(reverse("user-presence", args=[self.owner.id]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["is_online"])

    def test_online_status_still_visible_to_the_owner(self):
        from message.models import UserPresence

        UserPresence.objects.update_or_create(user=self.owner, defaults={"is_online": True})
        _set(self.owner, online_status_from="nobody")
        self.client.force_authenticate(self.owner)
        self.assertTrue(self.client.get(reverse("user-presence", args=[self.owner.id])).data["is_online"])

    # ---- followers / following list ----
    def test_followers_list_hidden_flag_and_empty_page(self):
        fan = _user("pe_fan", dob=ADULT_DOB)
        _follow(fan, self.owner)
        self.client.force_authenticate(self.viewer)
        url = reverse("user-followers", args=[self.owner.username])
        shown = self.client.get(url)
        self.assertEqual(shown.status_code, 200)
        self.assertNotIn("hidden", shown.data)
        _set(self.owner, followers_list_from="nobody")
        hidden = self.client.get(url)
        self.assertEqual(hidden.status_code, 200)
        self.assertTrue(hidden.data["hidden"])
        self.assertEqual(hidden.data["data"]["results"], [])

    def test_following_list_uses_the_same_control(self):
        _set(self.owner, followers_list_from="nobody")
        self.client.force_authenticate(self.viewer)
        r = self.client.get(reverse("user-following", args=[self.owner.username]))
        self.assertTrue(r.data["hidden"])

    def test_followers_list_open_to_connections_only(self):
        _set(self.owner, followers_list_from="connections")
        self.client.force_authenticate(self.viewer)
        url = reverse("user-followers", args=[self.owner.username])
        self.assertTrue(self.client.get(url).data["hidden"])
        _follow(self.viewer, self.owner)
        self.assertNotIn("hidden", self.client.get(url).data)

    def test_owner_sees_their_own_list(self):
        _set(self.owner, followers_list_from="nobody")
        self.client.force_authenticate(self.owner)
        r = self.client.get(reverse("user-followers", args=[self.owner.username]))
        self.assertNotIn("hidden", r.data)

    # ---- search ----
    def _search_names(self, who, q):
        self.client.force_authenticate(who)
        r = self.client.get(reverse("user-search"), {"search": q})
        self.assertEqual(r.status_code, 200, r.data)
        data = r.data["data"]
        rows = data["results"] if isinstance(data, dict) and "results" in data else data
        return {row["username"] for row in rows}

    def test_hide_from_search_removes_the_person_for_strangers(self):
        self.assertIn("pe_owner", self._search_names(self.viewer, "pe_owner"))
        _set(self.owner, hide_from_search=True)
        self.assertNotIn("pe_owner", self._search_names(self.viewer, "pe_owner"))

    def test_hide_from_search_keeps_people_with_an_accepted_follow(self):
        _set(self.owner, hide_from_search=True)
        _follow(self.viewer, self.owner)
        self.assertIn("pe_owner", self._search_names(self.viewer, "pe_owner"))

    def test_pending_follow_does_not_unhide(self):
        _set(self.owner, hide_from_search=True)
        _follow(self.viewer, self.owner, accepted=False)
        self.assertNotIn("pe_owner", self._search_names(self.viewer, "pe_owner"))


class PrivacyDefaultsDoNotChangeBehaviourTests(_Base, APITestCase):
    """Nobody has saved anything: the app must behave exactly as before S4."""

    def test_no_row_chat_call_search_all_open(self):
        owner, viewer = _user("pd_owner", dob=ADULT_DOB), _user("pd_viewer", dob=ADULT_DOB)
        self.client.force_authenticate(viewer)
        with mock.patch(FOLLOW_FN, return_value=True):
            r = self.client.post(reverse("conversation-start-private"), {"user_id": owner.id}, format="json")
        self.assertIn(r.status_code, (200, 201), r.data)
        self.assertTrue(privacy.can_call(owner, viewer))
        self.assertFalse(PrivacySettings.objects.exists())
