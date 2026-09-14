# core/tests.py
from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from login.models import User

from .models import Notification, NotificationPreference
from .services import create_bulk_notifications, create_notification


class CoreTestBase(TestCase):
    """Minimal fixtures — core stays app-agnostic, so unlike
    liveclass.tests.LiveClassTestBase this doesn't set up a
    Classroom/ClassSession/ClassPass; individual tests create those
    directly only when a test specifically needs the classroom/session
    FK on Notification."""

    def setUp(self):
        self.student = User.objects.create_user(
            username="student1", password="pass12345", email="student1@example.com"
        )
        self.other_student = User.objects.create_user(
            username="student2", password="pass12345", email="student2@example.com"
        )


# ===========================================================================
# Notification model
# ===========================================================================
class NotificationModelTests(CoreTestBase):
    def test_mark_read_sets_is_read_and_read_at(self):
        notification = Notification.objects.create(
            recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="Hi",
        )
        self.assertFalse(notification.is_read)
        self.assertIsNone(notification.read_at)

        notification.mark_read()

        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertIsNotNone(notification.read_at)

    def test_mark_read_is_idempotent(self):
        notification = Notification.objects.create(
            recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="Hi",
        )
        notification.mark_read()
        first_read_at = notification.read_at

        # Calling again shouldn't touch read_at a second time (no-op save
        # skipped entirely once is_read is already True).
        notification.mark_read()
        self.assertEqual(notification.read_at, first_read_at)

    def test_data_field_defaults_to_empty_dict(self):
        notification = Notification.objects.create(
            recipient=self.student, notif_type=Notification.NotifType.CHAT_MESSAGE, title="New message",
        )
        self.assertEqual(notification.data, {})

    def test_message_app_types_set_matches_new_notif_types(self):
        self.assertEqual(
            Notification.MESSAGE_APP_TYPES,
            {Notification.NotifType.CHAT_MESSAGE, Notification.NotifType.MENTION, Notification.NotifType.INCOMING_CALL},
        )


# ===========================================================================
# NotificationPreference — task 47 regression: same assertions as the
# original liveclass.tests.NotificationPreferenceTests, now exercised
# directly against core.models from the new location.
# ===========================================================================
class NotificationPreferenceTests(CoreTestBase):
    def test_for_user_creates_sane_defaults(self):
        pref = NotificationPreference.for_user(self.student)
        self.assertTrue(pref.push_enabled)
        self.assertTrue(pref.email_enabled)
        self.assertFalse(pref.sms_enabled)
        self.assertFalse(pref.whatsapp_enabled)
        self.assertEqual(pref.digest_frequency, NotificationPreference.DigestFrequency.OFF)

    def test_for_user_is_idempotent(self):
        first = NotificationPreference.for_user(self.student)
        second = NotificationPreference.for_user(self.student)
        self.assertEqual(first.pk, second.pk)

    def test_allowed_channels_respects_toggles(self):
        pref = NotificationPreference.objects.create(
            user=self.student, push_enabled=True, email_enabled=False, sms_enabled=True, whatsapp_enabled=False,
        )
        self.assertEqual(pref.allowed_channels_for(Notification.NotifType.SESSION_LIVE), ["push", "sms"])

    def test_muted_type_returns_no_channels(self):
        pref = NotificationPreference.objects.create(
            user=self.student, muted_types=[Notification.NotifType.NOTICE_POSTED],
        )
        self.assertEqual(pref.allowed_channels_for(Notification.NotifType.NOTICE_POSTED), [])
        self.assertNotEqual(pref.allowed_channels_for(Notification.NotifType.SESSION_LIVE), [])


# ===========================================================================
# create_notification() / create_bulk_notifications()
# ===========================================================================
class CreateNotificationTests(CoreTestBase):
    def test_creates_a_row(self):
        notification = create_notification(
            self.student, Notification.NotifType.GENERIC, "Title", "Body",
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification.recipient_id, self.student.id)
        self.assertEqual(notification.title, "Title")
        self.assertEqual(notification.message, "Body")

    def test_accepts_a_raw_recipient_id(self):
        """message/push_utils.py's call-sites only have recipient_ids
        (from _tokens_for_users-style lookups), not hydrated User
        objects — create_notification must accept either."""
        notification = create_notification(
            self.student.id, Notification.NotifType.CHAT_MESSAGE, "New message", "hey",
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification.recipient_id, self.student.id)

    def test_stores_data_dict(self):
        notification = create_notification(
            self.student, Notification.NotifType.MENTION, "Mentioned", "text",
            data={"conversation_id": "abc123"},
        )
        self.assertEqual(notification.data, {"conversation_id": "abc123"})

    def test_swallows_exceptions_and_returns_none(self):
        with patch("core.services.Notification.objects.create", side_effect=Exception("db down")):
            result = create_notification(self.student, Notification.NotifType.GENERIC, "Title")
        self.assertIsNone(result)

    def test_never_sends_a_push_itself(self):
        """Regression guard for create_notification()'s core contract:
        it ONLY ever writes a Notification row. If this starts failing,
        someone added a push/email/sms send inside core.services — that
        belongs at the call-site instead, exactly like every existing
        call-site already does it (create_notification(...) alongside a
        separate send_notification(...)/send_push_to_users(...) call)."""
        with patch("message.push_utils._send_multicast") as mock_send_multicast:
            create_notification(self.student, Notification.NotifType.CHAT_MESSAGE, "New message", "hey")
        mock_send_multicast.assert_not_called()

    def test_create_bulk_notifications_dedupes_recipients(self):
        create_bulk_notifications(
            [self.student, self.student, self.student.id, self.other_student],
            Notification.NotifType.NOTICE_POSTED,
            "New notice",
        )
        self.assertEqual(Notification.objects.for_user(self.student).count(), 1)
        self.assertEqual(Notification.objects.for_user(self.other_student).count(), 1)

    def test_create_bulk_notifications_skips_empty_recipients(self):
        create_bulk_notifications([], Notification.NotifType.NOTICE_POSTED, "New notice")
        self.assertEqual(Notification.objects.count(), 0)


# ===========================================================================
# NotificationViewSet / NotificationPreferenceView
# ===========================================================================
class NotificationViewSetTests(CoreTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.student)

    def test_unread_count(self):
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="A")
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="B")
        Notification.objects.create(
            recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="C", is_read=True,
        )
        # ⚠️ Path assumption: root urlconf mounts core.urls at "core/"
        # (see LearnScroll/urls.py). If that prefix ever changes, this
        # hardcoded path needs updating alongside it.
        response = self.client.get("/core/notifications/unread-count/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["unread_count"], 2)

    def test_list_only_returns_own_notifications(self):
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="Mine")
        Notification.objects.create(
            recipient=self.other_student, notif_type=Notification.NotifType.GENERIC, title="Not mine",
        )
        response = self.client.get("/core/notifications/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Mine")

    def test_list_includes_unread_count(self):
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="A")
        response = self.client.get("/core/notifications/")
        self.assertIn("unread_count", response.data)
        self.assertEqual(response.data["unread_count"], 1)

    def test_source_filter_splits_message_and_liveclass_types(self):
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.CHAT_MESSAGE, title="Chat")
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.SESSION_LIVE, title="Live")

        response = self.client.get("/core/notifications/?source=message")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Chat")
        self.assertEqual(response.data["results"][0]["source"], "message")

        response = self.client.get("/core/notifications/?source=liveclass")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Live")
        self.assertEqual(response.data["results"][0]["source"], "liveclass")

    def test_mark_read(self):
        notification = Notification.objects.create(
            recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="A",
        )
        response = self.client.post(f"/core/notifications/{notification.id}/mark-read/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_mark_all_read(self):
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="A")
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="B")
        response = self.client.post("/core/notifications/mark-all-read/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["marked_read"], 2)
        self.assertEqual(Notification.objects.for_user(self.student).unread().count(), 0)

    def test_cannot_delete_someone_elses_notification(self):
        notification = Notification.objects.create(
            recipient=self.other_student, notif_type=Notification.NotifType.GENERIC, title="Not mine",
        )
        response = self.client.delete(f"/core/notifications/{notification.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Notification.objects.filter(pk=notification.pk).exists())


class NotificationPreferenceViewTests(CoreTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.student)

    def test_get_creates_default_preference(self):
        response = self.client.get("/core/notification-preferences/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["push_enabled"])

    def test_patch_updates_preference(self):
        response = self.client.patch("/core/notification-preferences/me/", {"push_enabled": False}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["push_enabled"])
        self.assertFalse(NotificationPreference.for_user(self.student).push_enabled)

    def test_patch_rejects_unknown_muted_type(self):
        response = self.client.patch(
            "/core/notification-preferences/me/", {"muted_types": ["not_a_real_type"]}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

# ===========================================================================
# SearchView / search_everything() — Task 18, §9 items 14/15
# ===========================================================================
class SearchViewTests(CoreTestBase):
    """[GAP CLOSED — §9 item 14] `SearchView` had zero test coverage
    before this pass.

    `core.views.search_everything` is mocked for most of these —
    deliberately, not out of laziness: real `assigments`/`testseries`
    rows would need this test to guess at those apps' exact model
    schemas (required fields, `full_clean()` constraints on `testseries.
    Question`, etc.), which weren't part of any upload this doc has
    seen (same "stub it, don't guess" reasoning `search.py`'s own
    module docstring already gives for why `post`/`ClassMaterial`
    aren't wired yet). Mocking `search_everything` isolates exactly
    what `SearchView` itself owns: parsing `q`/`sources`, the
    `ValueError` -> `400` translation, and — the actual regression
    target for item 14's \"assigments/testseries scoping\" ask — the
    WHERE-clause shape of the two scoped querysets it builds, verified
    via `str(queryset.query)` rather than by executing them against
    real data. This needs no fixture data and no schema beyond the
    field names `core/views.py::SearchView.get()` itself already
    references (`posted_by`, `source`, `context_type`, `context_id`,
    `creator`, `status`) — nothing here is guessed past what's already
    in the uploaded source.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.student)

    def _capture_scoped_querysets(self):
        """Returns (captured_dict, side_effect_fn) — patch
        `core.views.search_everything` with the side_effect_fn to grab
        the `scoped_querysets` dict `SearchView.get()` builds, without
        actually running any FTS/trigram query against real data."""
        captured = {}

        def _capture(scoped_querysets, query, **kwargs):
            captured.update(scoped_querysets)
            return []

        return captured, _capture

    # --- q / 400 handling ---------------------------------------------

    def test_missing_query_returns_400(self):
        # Real search_everything() — not mocked — since MIN_QUERY_LENGTH
        # is enforced as the very first line of that function, before it
        # ever touches a scoped queryset, so this doesn't need
        # assigments/testseries data to exercise the real 400 path.
        response = self.client.get("/core/search/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)

    def test_empty_query_string_returns_400(self):
        response = self.client.get("/core/search/?q=")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_whitespace_only_query_returns_400(self):
        # search_everything() strips before length-checking — a
        # query of only spaces must be rejected the same as an empty one.
        response = self.client.get("/core/search/?q=   ")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_below_min_length_query_returns_400_not_500(self):
        # Exact MIN_QUERY_LENGTH threshold wasn't part of this upload
        # (it's imported from message.search_utils, not defined in
        # core.search itself) — mocked here with the exact contract
        # search_everything() documents (raises ValueError) rather than
        # guessing the real number of characters that trips it.
        with patch(
            "core.views.search_everything",
            side_effect=ValueError("Query must be at least 2 characters."),
        ) as mock_search:
            response = self.client.get("/core/search/?q=a")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)
        mock_search.assert_called_once()

    # --- success path ----------------------------------------------------

    def test_success_path_returns_search_everything_results_unmodified(self):
        fake_results = [
            {"source": "assigments", "id": 1, "title": "Algebra basics", "snippet": "..."},
            {"source": "testseries", "id": 7, "title": "Algebra mock test", "snippet": "..."},
        ]
        with patch("core.views.search_everything", return_value=fake_results) as mock_search:
            response = self.client.get("/core/search/?q=algebra")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], fake_results)
        mock_search.assert_called_once()
        _, call_kwargs = mock_search.call_args
        # No ?sources= given -> None, meaning "every registered source",
        # per search_everything()'s own contract.
        self.assertIsNone(call_kwargs.get("sources"))

    def test_success_path_scoped_querysets_include_all_wired_sources(self):
        # [Task 13 regression] `SearchView.get()` must build a scoped
        # queryset for every source it wires up — assigments/testseries
        # (Task 18) AND message/campus_notice (Task 13). Losing any one
        # of these silently drops that source from every search — the
        # exact bug this task closes for message/campus_notice.
        captured, side_effect = self._capture_scoped_querysets()
        with patch("core.views.search_everything", side_effect=side_effect):
            response = self.client.get("/core/search/?q=algebra")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(captured.keys()), {"assigments", "testseries", "message", "campus_notice"})

    def test_sources_param_is_parsed_into_a_list(self):
        with patch("core.views.search_everything", return_value=[]) as mock_search:
            response = self.client.get("/core/search/?q=algebra&sources=assigments,testseries")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        _, call_kwargs = mock_search.call_args
        self.assertEqual(call_kwargs["sources"], ["assigments", "testseries"])

    def test_sources_param_ignores_blank_entries(self):
        with patch("core.views.search_everything", return_value=[]) as mock_search:
            self.client.get("/core/search/?q=algebra&sources=assigments,,testseries,")
        _, call_kwargs = mock_search.call_args
        self.assertEqual(call_kwargs["sources"], ["assigments", "testseries"])

    def test_message_and_campus_notice_sources_are_wired_not_skipped(self):
        # [Task 13 regression] `message`/`campus_notice` are registered
        # in search.py's SOURCES AND now get a real scoped queryset from
        # SearchView.get() — requesting them must no longer be silently
        # skipped (the old bug: `search_everything()`'s "unregistered
        # source" skip path masking a scoping gap, not an actually
        # unregistered source). Verified by capturing scoped_querysets
        # rather than by asserting non-empty `results`, since this test
        # has no real Message/Notice fixture data — the previous bug was
        # that these keys were ABSENT from scoped_querysets at all, which
        # this asserts is no longer the case.
        captured, side_effect = self._capture_scoped_querysets()
        with patch("core.views.search_everything", side_effect=side_effect):
            response = self.client.get("/core/search/?q=algebra&sources=message,campus_notice")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(captured.keys()), {"message", "campus_notice"})

    def test_unregistered_sources_return_empty_not_error(self):
        # A genuinely unregistered source (not in core.search.SOURCES at
        # all, e.g. "post" — still a stub per that module's docstring)
        # must still degrade to a clean 200/[], not a 400/500.
        response = self.client.get("/core/search/?q=algebra&sources=post")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])

    # --- assigments/testseries scoping regression -------------------------

    def test_assigments_scoping_denies_staff_shortcut_to_non_staff(self):
        """Non-staff caller's `assigments` queryset must carry the
        posted-by-me-or-my-personal-submission narrowing — regression
        for `assigmentsViewSet.get_queryset()` parity (class docstring)."""
        captured, side_effect = self._capture_scoped_querysets()
        with patch("core.views.search_everything", side_effect=side_effect):
            self.client.get("/core/search/?q=algebra")
        sql = str(captured["assigments"].query).lower()
        self.assertIn("posted_by_id", sql)
        self.assertIn("source", sql)  # the PERSONAL-source half of the OR

    def test_assigments_scoping_staff_sees_everything_unfiltered(self):
        """A staff caller must get `assigments.objects.all()` — no
        posted-by-me narrowing — same as `assigmentsViewSet.get_queryset()`
        for staff."""
        self.student.is_staff = True
        self.student.save()
        captured, side_effect = self._capture_scoped_querysets()
        with patch("core.views.search_everything", side_effect=side_effect):
            self.client.get("/core/search/?q=algebra")
        sql = str(captured["assigments"].query).lower()
        self.assertNotIn("posted_by_id", sql)

    def test_testseries_scoping_includes_own_created_and_campus_enrolled(self):
        """Regression for the four-way OR: individual/published,
        own-created, attempted, and campus-enrolled-section — losing any
        one of these silently shrinks what a user can find via search."""
        captured, side_effect = self._capture_scoped_querysets()
        with patch("core.views.search_everything", side_effect=side_effect):
            self.client.get("/core/search/?q=algebra")
        sql = str(captured["testseries"].query).lower()
        self.assertIn("creator_id", sql)  # own-created
        self.assertIn("context_type", sql)  # campus-context branch
        self.assertIn("context_id", sql)  # campus-enrolled-sections branch
        self.assertIn("status", sql)  # individual/published branch
        # The campus-enrolled branch must be a nested SELECT against
        # `campus.StudentEnrollment` (`context_id__in=active_section_ids`)
        # rather than a hardcoded value — checked structurally (a
        # subquery appears right after `context_id`) rather than by
        # guessing `StudentEnrollment.Status.ACTIVE`'s exact stored
        # string representation, which wasn't confirmed for this
        # project's Django/enum setup (see class docstring).
        self.assertIn("select", sql.split("context_id", 1)[-1][:200])

    # --- message/campus_notice scoping regression [Task 13] --------------

    def test_message_scoping_matches_conversation_search_all(self):
        """Regression for `SearchView.get()`'s `message` queryset —
        must carry every narrowing `ConversationViewSet.search_all()`
        applies (current membership, non-expired disappearing messages,
        excludes deleted-for-everyone/deleted-for-me/scheduled), not
        just some of them. Checked structurally via `str(queryset.query)`
        — same approach already used for assigments/testseries above —
        since no `Message`/`Conversation` fixture data is needed to
        prove the WHERE-clause shape."""
        captured, side_effect = self._capture_scoped_querysets()
        with patch("core.views.search_everything", side_effect=side_effect):
            self.client.get("/core/search/?q=algebra")
        sql = str(captured["message"].query).lower()
        self.assertIn("memberships", sql)  # conversation__memberships__user
        self.assertIn("left_at", sql)  # current-membership-only narrowing
        self.assertIn("expires_at", sql)  # non-expired disappearing messages
        self.assertIn("deleted_for_everyone", sql)
        self.assertIn("deleted_for_users", sql)  # this user's own "delete for me"
        self.assertIn("is_scheduled", sql)

    def test_campus_notice_scoping_filters_by_my_campus_ids(self):
        """Regression for `SearchView.get()`'s `campus_notice` queryset
        — must filter to `get_my_campus_ids(user)` rather than returning
        every campus's notices, mirroring `NoticeViewSet.get_queryset()`.
        """
        captured, side_effect = self._capture_scoped_querysets()
        with patch("core.views.search_everything", side_effect=side_effect):
            self.client.get("/core/search/?q=algebra")
        sql = str(captured["campus_notice"].query).lower()
        self.assertIn("campus_id", sql)