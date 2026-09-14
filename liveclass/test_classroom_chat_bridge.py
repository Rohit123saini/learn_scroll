# liveclass/test_classroom_chat_bridge.py
"""
TASK 29 — real test coverage for `core/classroom_chat_bridge.py`.

The previous upload under this filename was confirmed stale: it contained
the bridge module's own source code, not tests. This file replaces it with
actual tests written against that real source (all 8 public sync/notify
functions, plus the two shared internal helpers `_get_group_for_classroom`
and `_classroom_cover_image_url`).

APPROACH — the bridge module is intentionally decoupled from both
`liveclass` and `message`: every cross-app model/service it touches is
imported *locally*, inside each function, specifically so this file can
mock every one of those import targets (`message.models.*`,
`message.services.*`, `liveclass.models.*`) without needing the real
`liveclass`/`message` app models loaded. `Classroom`/`ClassJoinRequest`/
`ClassroomStaff` field names are best-guesses per the module's own
"ASSUMPTIONS" docstring note (liveclass/models.py wasn't uploaded for
this pass) — a lightweight `SimpleNamespace`-based fake stands in for
`Classroom`/`Session` so these tests exercise the bridge's own logic
(the branching, the exception-swallowing, the exact calls it makes)
without depending on guessed field names lining up with a real model.

Uses `django.test.TestCase` (not `unittest.TestCase`) solely because
`create_classroom_group()` opens a `transaction.atomic()` block, which
expects a real DB connection/test-transaction to nest into.

If `liveclass/models.py` or `message/models.py` get uploaded later, the
`FakeClassroom`/`FakeSession` helpers below should be swapped for real
model instances built with `.objects.create(...)` — ask for those two
files to upgrade this from a pure unit-test file to an integration one.
"""
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from core.classroom_chat_bridge import (
    archive_group_on_classroom_close,
    create_classroom_group,
    get_groups_for_classrooms,
    post_session_live_announcement,
    post_welcome_message,
    promote_to_moderator,
    sync_group_metadata,
    sync_membership_on_join_accept,
    sync_membership_on_removal,
)
from core.classroom_chat_bridge import _classroom_cover_image_url, _get_group_for_classroom


def FakeClassroom(**kwargs):
    """Stand-in for the real `Classroom` model. Records `.save()` calls
    the same way a Django model instance would let us assert on, without
    needing the real model (not uploaded for this pass)."""
    defaults = dict(
        id=1,
        chat_group_enabled=False,
        linked_conversation_id=None,
        teacher_id=10,
        teacher=SimpleNamespace(id=10),
        title="Physics 101",
        description="",
        cover_image=None,
        pk=1,
    )
    defaults.update(kwargs)
    ns = SimpleNamespace(save=mock.Mock(), **defaults)
    return ns


def FakeGroup(**kwargs):
    defaults = dict(id=99, conversation_id=555, conversation=SimpleNamespace(id=555, soft_delete=mock.Mock()))
    defaults.update(kwargs)
    return SimpleNamespace(save=mock.Mock(), **defaults)


class GetGroupForClassroomTests(TestCase):
    """Covers the private `_get_group_for_classroom` helper directly —
    every public sync function is a thin wrapper around it, so its
    edge cases deserve their own tests rather than only being exercised
    incidentally through whichever public function happens to call it."""

    def test_returns_none_when_chat_group_disabled(self):
        classroom = FakeClassroom(chat_group_enabled=False, linked_conversation_id=555)
        with mock.patch("message.models.Group") as MockGroup:
            self.assertIsNone(_get_group_for_classroom(classroom))
            MockGroup.objects.select_related.assert_not_called()

    def test_returns_none_when_no_linked_conversation(self):
        classroom = FakeClassroom(chat_group_enabled=True, linked_conversation_id=None)
        with mock.patch("message.models.Group") as MockGroup:
            self.assertIsNone(_get_group_for_classroom(classroom))
            MockGroup.objects.select_related.assert_not_called()

    def test_returns_group_when_linked_and_enabled(self):
        classroom = FakeClassroom(chat_group_enabled=True, linked_conversation_id=555)
        fake_group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup:
            MockGroup.objects.select_related.return_value.get.return_value = fake_group
            result = _get_group_for_classroom(classroom)
        self.assertIs(result, fake_group)

    def test_returns_none_and_does_not_raise_when_group_row_missing(self):
        classroom = FakeClassroom(chat_group_enabled=True, linked_conversation_id=555)
        with mock.patch("message.models.Group") as MockGroup:
            MockGroup.DoesNotExist = Exception
            MockGroup.objects.select_related.return_value.get.side_effect = MockGroup.DoesNotExist
            result = _get_group_for_classroom(classroom)
        self.assertIsNone(result)


class GetGroupsForClassroomsTests(TestCase):
    def test_no_enabled_classrooms_short_circuits_without_querying(self):
        classrooms = [FakeClassroom(id=1, chat_group_enabled=False)]
        with mock.patch("message.models.Group") as MockGroup:
            result = get_groups_for_classrooms(classrooms)
        self.assertEqual(result, {})
        MockGroup.objects.filter.assert_not_called()

    def test_maps_classroom_id_to_group_for_enabled_classrooms(self):
        c1 = FakeClassroom(id=1, chat_group_enabled=True, linked_conversation_id=100)
        c2 = FakeClassroom(id=2, chat_group_enabled=True, linked_conversation_id=200)
        g1 = FakeGroup(conversation_id=100)
        g2 = FakeGroup(conversation_id=200)
        with mock.patch("message.models.Group") as MockGroup:
            MockGroup.objects.filter.return_value.select_related.return_value = [g1, g2]
            result = get_groups_for_classrooms([c1, c2])
        self.assertEqual(result, {1: g1, 2: g2})

    def test_classroom_with_deleted_group_is_absent_from_result(self):
        c1 = FakeClassroom(id=1, chat_group_enabled=True, linked_conversation_id=100)
        c2 = FakeClassroom(id=2, chat_group_enabled=True, linked_conversation_id=200)
        g1 = FakeGroup(conversation_id=100)
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("core.classroom_chat_bridge.logger") as mock_logger:
            # only g1's conversation comes back; 200 has no matching Group row
            MockGroup.objects.filter.return_value.select_related.return_value = [g1]
            result = get_groups_for_classrooms([c1, c2])
        self.assertEqual(result, {1: g1})
        self.assertNotIn(2, result)
        mock_logger.warning.assert_called_once()

    def test_disabled_classroom_is_excluded_even_if_others_are_enabled(self):
        enabled = FakeClassroom(id=1, chat_group_enabled=True, linked_conversation_id=100)
        disabled = FakeClassroom(id=2, chat_group_enabled=False, linked_conversation_id=None)
        g1 = FakeGroup(conversation_id=100)
        with mock.patch("message.models.Group") as MockGroup:
            MockGroup.objects.filter.return_value.select_related.return_value = [g1]
            result = get_groups_for_classrooms([enabled, disabled])
        self.assertEqual(result, {1: g1})


class ClassroomCoverImageUrlTests(TestCase):
    def test_returns_none_when_no_cover_image(self):
        classroom = FakeClassroom(cover_image=None)
        self.assertIsNone(_classroom_cover_image_url(classroom))

    def test_imagefield_style_cover_uses_url_attribute(self):
        cover = SimpleNamespace(url="https://cdn.example.com/cover.jpg")
        classroom = FakeClassroom(cover_image=cover)
        self.assertEqual(
            _classroom_cover_image_url(classroom), "https://cdn.example.com/cover.jpg"
        )

    def test_plain_string_url_falls_back_to_str(self):
        # A bare string has no `.url` attribute -> AttributeError -> str() fallback.
        classroom = FakeClassroom(cover_image="https://cdn.example.com/plain.jpg")
        self.assertEqual(
            _classroom_cover_image_url(classroom), "https://cdn.example.com/plain.jpg"
        )


class CreateClassroomGroupTests(TestCase):
    def test_non_teacher_actor_raises_value_error(self):
        classroom = FakeClassroom(teacher_id=10)
        not_the_teacher = SimpleNamespace(id=999)
        with self.assertRaises(ValueError):
            create_classroom_group(classroom, actor=not_the_teacher)

    def test_idempotent_returns_existing_group_without_creating_new_one(self):
        classroom = FakeClassroom(
            teacher_id=10, chat_group_enabled=True, linked_conversation_id=555,
        )
        teacher = SimpleNamespace(id=10)
        existing_group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("message.services.create_group") as mock_create_group:
            MockGroup.objects.select_related.return_value.get.return_value = existing_group
            result = create_classroom_group(classroom, actor=teacher)
        self.assertIs(result, existing_group)
        mock_create_group.assert_not_called()

    def test_creates_group_with_accepted_students_and_staff_as_members(self):
        classroom = FakeClassroom(teacher_id=10, chat_group_enabled=False, linked_conversation_id=None)
        teacher = SimpleNamespace(id=10)
        new_group = FakeGroup(conversation_id=777)

        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("message.services.create_group") as mock_create_group, \
                mock.patch("liveclass.models.ClassJoinRequest") as MockJoinRequest, \
                mock.patch("liveclass.models.ClassroomStaff") as MockStaff, \
                mock.patch("message.models.GroupMember") as MockGroupMember, \
                mock.patch("core.classroom_chat_bridge.post_welcome_message") as mock_welcome:
            MockGroup.DoesNotExist = Exception
            MockGroup.objects.select_related.return_value.get.side_effect = MockGroup.DoesNotExist
            MockJoinRequest.Status.ACCEPTED = "ACCEPTED"
            MockJoinRequest.objects.filter.return_value.values_list.return_value = [201, 202]
            MockStaff.objects.filter.return_value.values_list.return_value = [301]
            mock_create_group.return_value = new_group

            result = create_classroom_group(classroom, actor=teacher)

        self.assertIs(result, new_group)
        _, kwargs = mock_create_group.call_args
        self.assertEqual(kwargs["created_by"], classroom.teacher)
        self.assertEqual(kwargs["name"], classroom.title)
        self.assertTrue(kwargs["is_private"])
        self.assertEqual(kwargs["member_ids"], {201, 202, 301})

        # co-teacher/staff promoted to MODERATOR after creation
        MockGroupMember.objects.filter.assert_called_once_with(
            group=new_group, user_id__in=[301],
        )
        MockGroupMember.objects.filter.return_value.update.assert_called_once_with(
            role=MockGroupMember.Role.MODERATOR,
        )

        # classroom is linked and flipped on, and the welcome message fires
        classroom.save.assert_called_once_with(
            update_fields=["linked_conversation_id", "chat_group_enabled"],
        )
        self.assertEqual(classroom.linked_conversation_id, new_group.conversation_id)
        self.assertTrue(classroom.chat_group_enabled)
        mock_welcome.assert_called_once_with(classroom)

    def test_no_staff_promotion_call_when_there_is_no_staff(self):
        classroom = FakeClassroom(teacher_id=10, chat_group_enabled=False, linked_conversation_id=None)
        teacher = SimpleNamespace(id=10)
        new_group = FakeGroup(conversation_id=777)

        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("message.services.create_group") as mock_create_group, \
                mock.patch("liveclass.models.ClassJoinRequest") as MockJoinRequest, \
                mock.patch("liveclass.models.ClassroomStaff") as MockStaff, \
                mock.patch("message.models.GroupMember") as MockGroupMember, \
                mock.patch("core.classroom_chat_bridge.post_welcome_message"):
            MockGroup.DoesNotExist = Exception
            MockGroup.objects.select_related.return_value.get.side_effect = MockGroup.DoesNotExist
            MockJoinRequest.Status.ACCEPTED = "ACCEPTED"
            MockJoinRequest.objects.filter.return_value.values_list.return_value = [201]
            MockStaff.objects.filter.return_value.values_list.return_value = []
            mock_create_group.return_value = new_group

            create_classroom_group(classroom, actor=teacher)

        MockGroupMember.objects.filter.assert_not_called()


class SyncMembershipOnJoinAcceptTests(TestCase):
    def test_noop_when_no_linked_group(self):
        classroom = FakeClassroom(chat_group_enabled=False)
        student = SimpleNamespace(id=42)
        with mock.patch("message.services.add_members_to_group") as mock_add:
            sync_membership_on_join_accept(classroom, student)
        mock_add.assert_not_called()

    def test_adds_student_to_group_as_system_call(self):
        classroom = FakeClassroom(chat_group_enabled=True, linked_conversation_id=555)
        student = SimpleNamespace(id=42, pk=42)
        group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("message.services.add_members_to_group") as mock_add:
            MockGroup.objects.select_related.return_value.get.return_value = group
            sync_membership_on_join_accept(classroom, student)
        mock_add.assert_called_once_with(group=group, actor=None, user_ids=[42])

    def test_swallows_exception_from_add_members(self):
        classroom = FakeClassroom(chat_group_enabled=True, linked_conversation_id=555)
        student = SimpleNamespace(id=42, pk=42)
        group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("message.services.add_members_to_group", side_effect=Exception("boom")):
            MockGroup.objects.select_related.return_value.get.return_value = group
            sync_membership_on_join_accept(classroom, student)  # must not raise


class SyncMembershipOnRemovalTests(TestCase):
    def test_noop_when_no_linked_group(self):
        classroom = FakeClassroom(chat_group_enabled=False)
        student = SimpleNamespace(id=42)
        with mock.patch("message.services.remove_group_member") as mock_remove:
            sync_membership_on_removal(classroom, student, reason="banned")
        mock_remove.assert_not_called()

    def test_removes_student_from_group(self):
        classroom = FakeClassroom(chat_group_enabled=True, linked_conversation_id=555)
        student = SimpleNamespace(id=42, pk=42)
        group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("message.services.remove_group_member") as mock_remove:
            MockGroup.objects.select_related.return_value.get.return_value = group
            sync_membership_on_removal(classroom, student, reason="kicked")
        mock_remove.assert_called_once_with(group=group, actor=None, user_id=42)

    def test_swallows_exception_from_remove(self):
        classroom = FakeClassroom(chat_group_enabled=True, linked_conversation_id=555)
        student = SimpleNamespace(id=42, pk=42)
        group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("message.services.remove_group_member", side_effect=Exception("boom")):
            MockGroup.objects.select_related.return_value.get.return_value = group
            sync_membership_on_removal(classroom, student)  # must not raise


class PromoteToModeratorTests(TestCase):
    def test_noop_when_no_linked_group(self):
        classroom = FakeClassroom(chat_group_enabled=False)
        user = SimpleNamespace(id=7)
        with mock.patch("message.services.add_members_to_group") as mock_add, \
                mock.patch("message.services.update_group_member_role") as mock_role:
            promote_to_moderator(classroom, user)
        mock_add.assert_not_called()
        mock_role.assert_not_called()

    def test_adds_member_first_when_not_already_in_group_then_promotes(self):
        classroom = FakeClassroom(chat_group_enabled=True, linked_conversation_id=555)
        user = SimpleNamespace(id=7, pk=7)
        group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("message.models.GroupMember") as MockGroupMember, \
                mock.patch("message.services.add_members_to_group") as mock_add, \
                mock.patch("message.services.update_group_member_role") as mock_role:
            MockGroup.objects.select_related.return_value.get.return_value = group
            MockGroupMember.objects.filter.return_value.exists.return_value = False
            promote_to_moderator(classroom, user)
        mock_add.assert_called_once_with(group=group, actor=None, user_ids=[7])
        mock_role.assert_called_once_with(
            group=group, actor=None, user_id=7, data={"role": MockGroupMember.Role.MODERATOR},
        )

    def test_skips_add_when_already_a_member(self):
        classroom = FakeClassroom(chat_group_enabled=True, linked_conversation_id=555)
        user = SimpleNamespace(id=7, pk=7)
        group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("message.models.GroupMember") as MockGroupMember, \
                mock.patch("message.services.add_members_to_group") as mock_add, \
                mock.patch("message.services.update_group_member_role") as mock_role:
            MockGroup.objects.select_related.return_value.get.return_value = group
            MockGroupMember.objects.filter.return_value.exists.return_value = True
            promote_to_moderator(classroom, user)
        mock_add.assert_not_called()
        mock_role.assert_called_once()

    def test_swallows_exception(self):
        classroom = FakeClassroom(chat_group_enabled=True, linked_conversation_id=555)
        user = SimpleNamespace(id=7, pk=7)
        group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("message.models.GroupMember") as MockGroupMember, \
                mock.patch("message.services.add_members_to_group", side_effect=Exception("boom")):
            MockGroup.objects.select_related.return_value.get.return_value = group
            MockGroupMember.objects.filter.return_value.exists.return_value = False
            promote_to_moderator(classroom, user)  # must not raise


class SyncGroupMetadataTests(TestCase):
    def test_noop_when_no_linked_group(self):
        classroom = FakeClassroom(chat_group_enabled=False)
        sync_group_metadata(classroom)  # must not raise, nothing to assert on

    def test_updates_group_fields_from_classroom(self):
        classroom = FakeClassroom(
            chat_group_enabled=True, linked_conversation_id=555,
            title="New Title", description="New description", cover_image=None,
        )
        group = FakeGroup(name="Old Title", description="Old", photo_url="old.jpg")
        with mock.patch("message.models.Group") as MockGroup:
            MockGroup.objects.select_related.return_value.get.return_value = group
            sync_group_metadata(classroom)
        self.assertEqual(group.name, "New Title")
        self.assertEqual(group.description, "New description")
        self.assertIsNone(group.photo_url)
        group.save.assert_called_once_with(update_fields=["name", "description", "photo_url"])

    def test_swallows_exception_on_save_failure(self):
        classroom = FakeClassroom(chat_group_enabled=True, linked_conversation_id=555)
        group = FakeGroup()
        group.save.side_effect = Exception("db down")
        with mock.patch("message.models.Group") as MockGroup:
            MockGroup.objects.select_related.return_value.get.return_value = group
            sync_group_metadata(classroom)  # must not raise


class ArchiveGroupOnClassroomCloseTests(TestCase):
    def test_noop_when_no_linked_group(self):
        classroom = FakeClassroom(chat_group_enabled=False)
        with mock.patch("core.classroom_chat_bridge._post_system_message") as mock_post:
            archive_group_on_classroom_close(classroom)
        mock_post.assert_not_called()

    def test_posts_message_broadcasts_and_soft_deletes(self):
        classroom = FakeClassroom(
            chat_group_enabled=True, linked_conversation_id=555, teacher_id=10,
            teacher=SimpleNamespace(id=10), title="Physics 101",
        )
        group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("asgiref.sync.async_to_sync") as mock_async_to_sync, \
                mock.patch("channels.layers.get_channel_layer") as mock_get_layer:
            MockGroup.objects.select_related.return_value.get.return_value = group
            archive_group_on_classroom_close(classroom)

        mock_get_layer.assert_called_once()
        mock_async_to_sync.assert_called_once()
        group.conversation.soft_delete.assert_called_once()
        group.save.assert_not_called()  # bridge calls group.soft_delete(), not .save()

    def test_broadcast_failure_does_not_prevent_soft_delete(self):
        classroom = FakeClassroom(
            chat_group_enabled=True, linked_conversation_id=555, teacher_id=10,
            teacher=SimpleNamespace(id=10), title="Physics 101",
        )
        group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("channels.layers.get_channel_layer", side_effect=Exception("no layer")):
            MockGroup.objects.select_related.return_value.get.return_value = group
            archive_group_on_classroom_close(classroom)
        # the broadcast's own try/except is inside the outer try, so a
        # broadcast failure must not stop the soft-delete calls after it.
        group.conversation.soft_delete.assert_called_once()

    def test_swallows_outer_exception(self):
        classroom = FakeClassroom(chat_group_enabled=True, linked_conversation_id=555)
        group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch(
                    "core.classroom_chat_bridge._post_system_message",
                    side_effect=Exception("boom"),
                ):
            MockGroup.objects.select_related.return_value.get.return_value = group
            archive_group_on_classroom_close(classroom)  # must not raise


class PostWelcomeMessageTests(TestCase):
    def test_noop_when_no_linked_group(self):
        classroom = FakeClassroom(chat_group_enabled=False)
        with mock.patch("core.classroom_chat_bridge._post_system_message") as mock_post:
            post_welcome_message(classroom)
        mock_post.assert_not_called()

    def test_posts_welcome_text_from_teacher(self):
        classroom = FakeClassroom(
            chat_group_enabled=True, linked_conversation_id=555,
            teacher=SimpleNamespace(id=10), title="Physics 101",
        )
        group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("core.classroom_chat_bridge._post_system_message") as mock_post:
            MockGroup.objects.select_related.return_value.get.return_value = group
            post_welcome_message(classroom)
        mock_post.assert_called_once()
        called_group, called_sender, called_text = mock_post.call_args[0]
        self.assertIs(called_group, group)
        self.assertIs(called_sender, classroom.teacher)
        self.assertIn("Physics 101", called_text)


class PostSessionLiveAnnouncementTests(TestCase):
    def test_noop_when_session_classroom_has_no_linked_group(self):
        classroom = FakeClassroom(chat_group_enabled=False)
        session = SimpleNamespace(classroom=classroom)
        with mock.patch("core.classroom_chat_bridge._post_system_message") as mock_post:
            post_session_live_announcement(session)
        mock_post.assert_not_called()

    def test_posts_announcement_naming_the_classroom(self):
        classroom = FakeClassroom(
            chat_group_enabled=True, linked_conversation_id=555,
            teacher=SimpleNamespace(id=10), title="Physics 101",
        )
        session = SimpleNamespace(classroom=classroom)
        group = FakeGroup()
        with mock.patch("message.models.Group") as MockGroup, \
                mock.patch("core.classroom_chat_bridge._post_system_message") as mock_post:
            MockGroup.objects.select_related.return_value.get.return_value = group
            post_session_live_announcement(session)
        mock_post.assert_called_once()
        called_group, called_sender, called_text = mock_post.call_args[0]
        self.assertIs(called_group, group)
        self.assertIs(called_sender, classroom.teacher)
        self.assertIn("Physics 101", called_text)