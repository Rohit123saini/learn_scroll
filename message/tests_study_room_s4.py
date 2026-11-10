# message/tests_study_room_s4.py
"""
S4 — study room host / moderation controls: roles, join gate (lock / waiting room / max participants /
removed), mute / remove / role / admit / hand / report endpoints, group `study_room_permission` -> role.

LiveKit (RoomService + token) aur WebSocket broadcast mock hain — koi network nahi.

    python manage.py test message.tests_study_room_s4
"""
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from . import livekit_utils as lk
from . import study_room_roles as sr
from .models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Group,
    GroupMember,
    StudyRoomParticipant,
    StudyRoomReport,
    StudyRoomSession,
)

User = get_user_model()
Role = StudyRoomParticipant.Role
Status = StudyRoomParticipant.Status


def _uid():
    return uuid.uuid4().hex[:8]


# ----------------------------------------------------------------------
# Pure logic (DB nahi)
# ----------------------------------------------------------------------
class RoleLogicTests(SimpleTestCase):
    def test_can_act_on_role_order(self):
        self.assertTrue(sr.can_act_on(Role.HOST, Role.CO_HOST))
        self.assertTrue(sr.can_act_on(Role.HOST, Role.VIEWER))
        self.assertFalse(sr.can_act_on(Role.HOST, Role.HOST))
        self.assertTrue(sr.can_act_on(Role.CO_HOST, Role.PARTICIPANT))
        self.assertFalse(sr.can_act_on(Role.CO_HOST, Role.CO_HOST))
        self.assertFalse(sr.can_act_on(Role.CO_HOST, Role.HOST))
        self.assertFalse(sr.can_act_on(Role.PARTICIPANT, Role.VIEWER))
        self.assertFalse(sr.can_act_on(Role.VIEWER, Role.PARTICIPANT))

    def test_can_edit_board(self):
        for policy in ('everyone', 'host_only'):
            self.assertTrue(sr.can_edit_board(Role.HOST, policy))
            self.assertTrue(sr.can_edit_board(Role.CO_HOST, policy))
            self.assertFalse(sr.can_edit_board(Role.VIEWER, policy))
        self.assertTrue(sr.can_edit_board(Role.PARTICIPANT, 'everyone'))
        self.assertFalse(sr.can_edit_board(Role.PARTICIPANT, 'host_only'))

    def test_ws_action_gate(self):
        ok = sr.ws_action_allowed
        self.assertEqual(ok('draw_point', None, 'everyone'), (False, 'removed'))
        self.assertEqual(ok('end_session', Role.PARTICIPANT, 'everyone'), (False, 'host_required'))
        self.assertEqual(ok('clear_board', Role.VIEWER, 'everyone'), (False, 'host_required'))
        self.assertEqual(ok('end_session', Role.CO_HOST, 'everyone'), (True, ''))
        self.assertEqual(ok('draw_point', Role.VIEWER, 'everyone'), (False, 'view_only'))
        self.assertEqual(ok('user_joined', Role.VIEWER, 'everyone'), (True, ''))
        self.assertEqual(ok('draw_point', Role.PARTICIPANT, 'host_only'), (False, 'host_only_share'))
        self.assertEqual(ok('quick_chat', Role.PARTICIPANT, 'host_only'), (True, ''))
        self.assertEqual(ok('note_add', Role.PARTICIPANT, 'host_only', is_note_action=True), (False, 'host_only_share'))
        self.assertEqual(ok('draw_point', Role.HOST, 'host_only'), (True, ''))

    def test_livekit_grants_by_role(self):
        viewer = lk.study_room_grants(Role.VIEWER, 'everyone')
        self.assertFalse(viewer['can_publish'])
        self.assertFalse(viewer['can_publish_data'])
        self.assertEqual(viewer['sources'], [])
        part_open = lk.study_room_grants(Role.PARTICIPANT, 'everyone')
        part_host_only = lk.study_room_grants(Role.PARTICIPANT, 'host_only')
        self.assertTrue(part_open['can_publish'])
        self.assertGreater(len(part_open['sources']), len(part_host_only['sources']))
        host = lk.study_room_grants(Role.HOST, 'host_only')
        self.assertEqual(host['sources'], part_open['sources'])

    def test_grants_for_flags(self):
        g = sr.grants_for(Role.PARTICIPANT, 'host_only')
        self.assertTrue(g['can_publish'])
        self.assertFalse(g['can_screen_share'])
        self.assertFalse(g['can_edit_board'])
        self.assertFalse(g['can_manage'])
        self.assertTrue(g['can_raise_hand'])
        self.assertTrue(sr.grants_for(Role.CO_HOST, 'host_only')['can_manage'])
        self.assertFalse(sr.grants_for(Role.VIEWER, 'everyone')['can_publish'])


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
class _RoomBase(TestCase):
    """a = session starter (host), b / c = members. Private DM-style conversation (no Group)."""

    group = False
    group_private = True
    study_room_permission = 'everyone'

    def setUp(self):
        cache.clear()
        self.a = User.objects.create_user(username=f"a{_uid()}", password="x")
        self.b = User.objects.create_user(username=f"b{_uid()}", password="x")
        self.c = User.objects.create_user(username=f"c{_uid()}", password="x")
        self.conv = Conversation.objects.create(
            type=ConversationType.GROUP if self.group else ConversationType.PRIVATE
        )
        for u in (self.a, self.b, self.c):
            ConversationParticipant.objects.create(conversation=self.conv, user=u)
        self.group_obj = None
        if self.group:
            self.group_obj = Group.objects.create(
                conversation=self.conv, name="g", created_by=self.a,
                is_private=self.group_private, study_room_permission=self.study_room_permission,
            )
            GroupMember.objects.create(group=self.group_obj, user=self.a, role=GroupMember.Role.MEMBER)
            GroupMember.objects.create(group=self.group_obj, user=self.b, role=GroupMember.Role.MEMBER)
            GroupMember.objects.create(group=self.group_obj, user=self.c, role=GroupMember.Role.MEMBER)

        # LiveKit + realtime mocks
        self.token_mock = mock.MagicMock(return_value="tok")
        patches = {
            'message.views.study_room.livekit_configured': mock.MagicMock(return_value=True),
            'message.views.study_room.ensure_study_room': mock.MagicMock(return_value=True),
            'message.views.study_room.generate_study_room_token': self.token_mock,
            'message.livekit_utils.livekit_configured': mock.MagicMock(return_value=True),
            'message.livekit_utils.list_room_identities': mock.MagicMock(return_value=set()),
            'message.livekit_utils.mute_microphones': mock.MagicMock(return_value=1),
            'message.livekit_utils.remove_participant': mock.MagicMock(return_value=True),
            'message.livekit_utils.update_participant_permissions': mock.MagicMock(return_value=True),
            'message.livekit_utils.update_room_metadata': mock.MagicMock(return_value=True),
            'message.study_room_events.broadcast': mock.MagicMock(return_value=True),
        }
        self.m = {}
        for target, m in patches.items():
            p = mock.patch(target, m)
            p.start()
            self.addCleanup(p.stop)
            self.m[target.rsplit('.', 1)[1]] = m

    # --- helpers ---
    def client(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def url(self, path):
        return f"/message/study-room/{self.conv.id}/{path}/"

    def join(self, user, new_session=False):
        return self.client(user).post(self.url('join'), {"new_session": new_session}, format='json')

    def post(self, user, path, data=None):
        return self.client(user).post(self.url(path), data or {}, format='json')

    def start_room(self):
        """a join karke session start karta hai (a = host)."""
        r = self.join(self.a, new_session=True)
        self.assertEqual(r.status_code, 200, r.data)
        return r


class JoinAndRoleTests(_RoomBase):
    def test_starter_is_host_and_member_is_participant(self):
        r = self.start_room()
        self.assertEqual(r.data['role'], 'host')
        self.assertTrue(r.data['permissions']['can_manage'])
        self.assertEqual(self.token_mock.call_args.kwargs['role'], 'host')

        r2 = self.join(self.b)
        self.assertEqual(r2.status_code, 200, r2.data)
        self.assertEqual(r2.data['role'], 'participant')
        self.assertFalse(r2.data['permissions']['can_manage'])
        self.assertIn('room_settings', r2.data)

    def test_room_info_requires_membership_in_room(self):
        self.start_room()
        r = self.client(self.a).get(self.url('room'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['role'], 'host')
        self.assertIn('waiting', r.data)          # manager ko
        self.assertIn('hands', r.data)
        self.join(self.b)
        rb = self.client(self.b).get(self.url('room'))
        self.assertEqual(rb.status_code, 200)
        self.assertNotIn('waiting', rb.data)      # participant ko nahi


class LockWaitingRoomCapTests(_RoomBase):
    def test_lock_blocks_new_joiners_not_host(self):
        self.start_room()
        r = self.post(self.a, 'settings', {"is_locked": True})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['room_settings']['is_locked'])
        blocked = self.join(self.c)
        self.assertEqual(blocked.status_code, 423)
        self.assertEqual(blocked.data['code'], 'room_locked')
        self.assertEqual(self.join(self.a).status_code, 200)   # host exempt
        # unlock -> aa sakta hai
        self.post(self.a, 'settings', {"is_locked": False})
        self.assertEqual(self.join(self.c).status_code, 200)

    def test_already_joined_user_can_rejoin_while_locked(self):
        self.start_room()
        self.assertEqual(self.join(self.b).status_code, 200)
        self.post(self.a, 'settings', {"is_locked": True})
        self.assertEqual(self.join(self.b).status_code, 200)   # reconnect

    def test_participant_cannot_change_settings(self):
        self.start_room()
        self.join(self.b)
        r = self.post(self.b, 'settings', {"is_locked": True})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data['code'], 'host_required')

    def test_settings_validation(self):
        self.start_room()
        self.assertEqual(self.post(self.a, 'settings', {}).data['code'], 'nothing_to_update')
        self.assertEqual(self.post(self.a, 'settings', {"share_policy": "nope"}).data['code'], 'invalid_share_policy')
        self.assertEqual(self.post(self.a, 'settings', {"max_participants": 1}).data['code'], 'invalid_max_participants')
        self.assertEqual(self.post(self.a, 'settings', {"max_participants": "x"}).data['code'], 'invalid_max_participants')
        ok = self.post(self.a, 'settings', {"max_participants": 5})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.data['room_settings']['max_participants'], 5)

    def test_waiting_room_flow_admit(self):
        self.start_room()
        self.post(self.a, 'settings', {"waiting_room_enabled": True})
        w = self.join(self.b)
        self.assertEqual(w.status_code, 202)
        self.assertEqual(w.data['status'], 'waiting')
        self.assertNotIn('livekit_token', w.data)
        # manager ko notification gayi
        actions = [c.args[1] for c in self.m['broadcast'].call_args_list]
        self.assertIn('waiting_room_request', actions)
        # waiting me refresh-token se token nahi
        room_name = StudyRoomSession.objects.get(conversation=self.conv).room_name
        rt = self.post(self.b, 'refresh-token', {"room_name": room_name})
        self.assertEqual(rt.status_code, 403)
        # host ki list me
        info = self.client(self.a).get(self.url('room'))
        self.assertEqual([x['user_id'] for x in info.data['waiting']], [str(self.b.id)])
        # admit -> join 200
        ad = self.post(self.a, 'admit', {"user_id": str(self.b.id), "action": "admit"})
        self.assertEqual(ad.status_code, 200, ad.data)
        self.assertEqual(self.join(self.b).status_code, 200)

    def test_waiting_room_deny(self):
        self.start_room()
        self.post(self.a, 'settings', {"waiting_room_enabled": True})
        self.assertEqual(self.join(self.b).status_code, 202)
        d = self.post(self.a, 'admit', {"user_id": str(self.b.id), "action": "deny"})
        self.assertEqual(d.status_code, 200)
        r = self.join(self.b)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data['code'], 'denied_by_host')

    def test_turning_off_waiting_room_admits_everyone(self):
        self.start_room()
        self.post(self.a, 'settings', {"waiting_room_enabled": True})
        self.assertEqual(self.join(self.b).status_code, 202)
        self.post(self.a, 'settings', {"waiting_room_enabled": False})
        self.assertEqual(self.join(self.b).status_code, 200)

    def test_admit_all(self):
        self.start_room()
        self.post(self.a, 'settings', {"waiting_room_enabled": True})
        self.join(self.b)
        self.join(self.c)
        r = self.post(self.a, 'admit', {"action": "admit_all"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.data['user_ids']), {str(self.b.id), str(self.c.id)})
        self.assertEqual(self.join(self.b).status_code, 200)
        self.assertEqual(self.join(self.c).status_code, 200)

    def test_max_participants_room_full(self):
        self.start_room()
        self.post(self.a, 'settings', {"max_participants": 2})
        self.m['list_room_identities'].return_value = {"u1", "u2"}
        r = self.join(self.b)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['code'], 'room_full')
        self.assertEqual(self.join(self.a).status_code, 200)   # host exempt


class PublicGroupWaitingRoomTests(_RoomBase):
    group = True
    group_private = False

    def test_public_group_new_session_defaults_to_waiting_room(self):
        r = self.join(self.a, new_session=True)
        self.assertEqual(r.status_code, 200, r.data)           # starter = host
        self.assertTrue(r.data['room_settings']['waiting_room_enabled'])
        self.assertEqual(self.join(self.b).status_code, 202)

    @override_settings(STUDY_ROOM_PUBLIC_GROUP_WAITING_ROOM=False)
    def test_public_group_waiting_room_can_be_disabled(self):
        r = self.join(self.a, new_session=True)
        self.assertFalse(r.data['room_settings']['waiting_room_enabled'])
        self.assertEqual(self.join(self.b).status_code, 200)


class ModerationTests(_RoomBase):
    def test_remove_blocks_rejoin_and_kicks_livekit(self):
        self.start_room()
        self.join(self.b)
        r = self.post(self.a, 'remove', {"user_id": str(self.b.id)})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['kicked'])
        room_name = StudyRoomSession.objects.get(conversation=self.conv).room_name
        self.m['remove_participant'].assert_called_with(room_name, str(self.b.id))
        again = self.join(self.b)
        self.assertEqual(again.status_code, 403)
        self.assertEqual(again.data['code'], 'removed_from_room')
        rt = self.post(self.b, 'refresh-token', {"room_name": room_name})
        self.assertEqual(rt.status_code, 403)
        # host wapas admit kar sakta hai
        self.assertEqual(self.post(self.a, 'admit', {"user_id": str(self.b.id), "action": "admit"}).status_code, 200)
        self.assertEqual(self.join(self.b).status_code, 200)

    def test_participant_cannot_moderate(self):
        self.start_room()
        self.join(self.b)
        self.join(self.c)
        for path in ('remove', 'mute', 'role'):
            r = self.post(self.b, path, {"user_id": str(self.c.id), "role": "viewer"})
            self.assertEqual(r.status_code, 403, path)
            self.assertEqual(r.data['code'], 'host_required', path)

    def test_cannot_target_self_or_host(self):
        self.start_room()
        self.join(self.b)
        self.post(self.a, 'role', {"user_id": str(self.b.id), "role": "co_host"})
        self.assertEqual(self.post(self.a, 'remove', {"user_id": str(self.a.id)}).data['code'], 'cannot_target_self')
        r = self.post(self.b, 'remove', {"user_id": str(self.a.id)})   # co-host -> host
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data['code'], 'insufficient_role')

    def test_mute_single_and_all(self):
        self.start_room()
        self.join(self.b)
        self.join(self.c)
        r = self.post(self.a, 'mute', {"user_id": str(self.b.id)})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.m['mute_microphones'].call_args.kwargs['identities'], {str(self.b.id)})

        self.m['list_room_identities'].return_value = {str(self.a.id), str(self.b.id), str(self.c.id)}
        r = self.post(self.a, 'mute', {"all": True})
        self.assertEqual(r.status_code, 200, r.data)
        # host khud mute nahi hota
        self.assertEqual(self.m['mute_microphones'].call_args.kwargs['identities'], {str(self.b.id), str(self.c.id)})

    def test_mute_livekit_down_is_502(self):
        self.start_room()
        self.join(self.b)
        self.m['mute_microphones'].return_value = None
        r = self.post(self.a, 'mute', {"user_id": str(self.b.id)})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.data['code'], 'livekit_unavailable')

    def test_role_change_viewer_and_token(self):
        self.start_room()
        self.join(self.b)
        r = self.post(self.a, 'role', {"user_id": str(self.b.id), "role": "viewer"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['role'], 'viewer')
        self.assertFalse(r.data['permissions']['can_publish'])
        self.assertFalse(r.data['permissions']['can_edit_board'])
        rejoin = self.join(self.b)
        self.assertEqual(rejoin.data['role'], 'viewer')
        self.assertEqual(self.token_mock.call_args.kwargs['role'], 'viewer')
        # wapas participant (default) -> override hat jata hai
        self.post(self.a, 'role', {"user_id": str(self.b.id), "role": "participant"})
        row = StudyRoomParticipant.objects.get(session__conversation=self.conv, user=self.b)
        self.assertIsNone(row.role_override)

    def test_cohost_rules(self):
        self.start_room()
        self.join(self.b)
        self.join(self.c)
        self.assertEqual(self.post(self.a, 'role', {"user_id": str(self.b.id), "role": "co_host"}).status_code, 200)
        # co-host naya co-host nahi bana sakta, par viewer bana sakta hai
        no = self.post(self.b, 'role', {"user_id": str(self.c.id), "role": "co_host"})
        self.assertEqual(no.status_code, 403)
        self.assertEqual(no.data['code'], 'host_required')
        yes = self.post(self.b, 'role', {"user_id": str(self.c.id), "role": "viewer"})
        self.assertEqual(yes.status_code, 200)
        # co-host mute / settings kar sakta hai
        self.assertEqual(self.post(self.b, 'settings', {"is_locked": True}).status_code, 200)

    def test_invalid_role_and_user(self):
        self.start_room()
        self.assertEqual(self.post(self.a, 'role', {"user_id": str(self.b.id), "role": "host"}).data['code'], 'invalid_role')
        self.assertEqual(self.post(self.a, 'remove', {"user_id": "nope"}).data['code'], 'invalid_user')
        stranger = User.objects.create_user(username=f"s{_uid()}", password="x")
        r = self.post(self.a, 'remove', {"user_id": str(stranger.id)})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.data['code'], 'not_member')

    def test_controls_need_active_session(self):
        r = self.post(self.a, 'settings', {"is_locked": True})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.data['code'], 'no_active_session')


class HandAndReportTests(_RoomBase):
    def test_raise_and_lower_hand(self):
        self.start_room()
        self.join(self.b)
        self.assertEqual(self.post(self.b, 'hand', {"raised": True}).data['raised'], True)
        info = self.client(self.a).get(self.url('room'))
        self.assertEqual([h['user_id'] for h in info.data['hands']], [str(self.b.id)])
        # participant dusre ka haath neeche nahi kar sakta
        self.join(self.c)
        self.assertEqual(self.post(self.c, 'hand', {"user_id": str(self.b.id)}).status_code, 403)
        # host kar sakta hai
        r = self.post(self.a, 'hand', {"user_id": str(self.b.id)})
        self.assertEqual(r.status_code, 200)
        info = self.client(self.a).get(self.url('room'))
        self.assertEqual(info.data['hands'], [])

    def test_viewer_can_raise_hand_and_lower_all(self):
        self.start_room()
        self.join(self.b)
        self.post(self.a, 'role', {"user_id": str(self.b.id), "role": "viewer"})
        self.assertEqual(self.post(self.b, 'hand', {"raised": True}).status_code, 200)
        self.assertEqual(self.post(self.a, 'hand', {"all": True}).data['lowered'], 1)

    def test_report_flow_and_dedupe(self):
        self.start_room()
        r = self.post(self.b, 'report', {"user_id": str(self.c.id), "reason": "spam", "details": "x" * 2000})
        self.assertEqual(r.status_code, 201, r.data)
        rep = StudyRoomReport.objects.get()
        self.assertEqual(rep.reporter_id, self.b.id)
        self.assertEqual(rep.reported_user_id, self.c.id)
        self.assertEqual(len(rep.details), 1000)
        self.assertIsNotNone(rep.session_id)
        dup = self.post(self.b, 'report', {"user_id": str(self.c.id), "reason": "spam"})
        self.assertEqual(dup.status_code, 200)
        self.assertTrue(dup.data['duplicate'])
        self.assertEqual(StudyRoomReport.objects.count(), 1)

    def test_report_validation(self):
        self.start_room()
        self.assertEqual(self.post(self.b, 'report', {"user_id": str(self.b.id), "reason": "spam"}).data['code'],
                         'cannot_target_self')
        self.assertEqual(self.post(self.b, 'report', {"user_id": str(self.c.id), "reason": "zzz"}).data['code'],
                         'invalid_reason')
        stranger = User.objects.create_user(username=f"s{_uid()}", password="x")
        self.assertEqual(self.post(self.b, 'report', {"user_id": str(stranger.id), "reason": "spam"}).status_code, 404)

    def test_removed_user_can_still_report(self):
        self.start_room()
        self.join(self.b)
        self.post(self.a, 'remove', {"user_id": str(self.b.id)})
        r = self.post(self.b, 'report', {"user_id": str(self.a.id), "reason": "harassment"})
        self.assertEqual(r.status_code, 201)


class GroupRoleMappingTests(_RoomBase):
    group = True
    study_room_permission = 'admins_only'

    def setUp(self):
        super().setUp()
        # c = group admin, a = normal member (session starter / host), b = normal member
        GroupMember.objects.filter(group=self.group_obj, user=self.c).update(role=GroupMember.Role.ADMIN)
        cache.clear()

    def test_admins_only_group_members_join_as_viewer_not_403(self):
        self.start_room()                          # a host (starter)
        rb = self.join(self.b)
        self.assertEqual(rb.status_code, 200, rb.data)
        self.assertEqual(rb.data['role'], 'viewer')
        self.assertFalse(rb.data['permissions']['can_publish'])
        rc = self.join(self.c)
        self.assertEqual(rc.data['role'], 'co_host')   # group admin = co-host

    def test_group_admin_is_protected_from_session_host(self):
        self.start_room()
        self.join(self.c)
        r = self.post(self.a, 'remove', {"user_id": str(self.c.id)})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data['code'], 'group_staff_protected')
        r2 = self.post(self.a, 'role', {"user_id": str(self.c.id), "role": "viewer"})
        self.assertEqual(r2.status_code, 403)
        # group admin session host par... host ko koi nahi hata sakta
        self.assertEqual(self.post(self.c, 'remove', {"user_id": str(self.a.id)}).status_code, 403)
        # group admin normal member ko hata sakta hai
        self.join(self.b)
        self.assertEqual(self.post(self.c, 'remove', {"user_id": str(self.b.id)}).status_code, 200)

    def test_admin_can_promote_viewer_member_to_participant(self):
        self.start_room()
        self.join(self.b)
        r = self.post(self.a, 'role', {"user_id": str(self.b.id), "role": "participant"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['permissions']['can_publish'])
        self.assertEqual(self.join(self.b).data['role'], 'participant')
