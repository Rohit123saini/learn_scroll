# core/test_parent_bridge.py
"""
Tests for `core.classroom_chat_bridge.resolve_parent_from_token()` (Task 5).

Scope: this is a pure unit test of the token-resolution function itself —
it does NOT touch any `liveclass` view/endpoint (that's Task 9, not yet
built — see `liveclass/tests.py` for the note on why the corresponding
`ParentSessionJoinTests` aren't included yet).

Fixtures directly create `message.models.ParentAccessCode`/`ParentToken`
rows per the confirmed shape (CHAT_APP_DOCUMENTATION.md §7.16):
    ParentAccessCode: student (FK), label, code, is_active, expires_at,
                       last_used_at, last_revealed_at
    ParentToken:       parent_access_code (FK), token, last_seen_at
                       (INACTIVITY_TTL_DAYS = 30, rolling expiry)

Run: python manage.py test core.test_parent_bridge
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from login.models import User
from message.models import ParentAccessCode, ParentToken

from core.classroom_chat_bridge import resolve_parent_from_token


class ResolveParentFromTokenTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="student1", password="pass12345", email="student1@example.com",
        )
        self.other_student = User.objects.create_user(
            username="student2", password="pass12345", email="student2@example.com",
        )

        self.access_code = ParentAccessCode.objects.create(
            student=self.student,
            label="Mummy's phone",
            code="ABCD2345",  # excludes 0/O/1/I per the real generator; doesn't matter for a manual row
            is_active=True,
            expires_at=timezone.now() + timedelta(days=180),
        )
        self.token_str = "test-parent-token-0001"
        self.parent_token = ParentToken.objects.create(
            parent_access_code=self.access_code,
            token=self.token_str,
            last_seen_at=timezone.now(),
        )

    # -----------------------------------------------------------------
    # Success path
    # -----------------------------------------------------------------
    def test_valid_token_resolves_to_correct_student(self):
        resolution = resolve_parent_from_token(self.token_str)

        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.student.id, self.student.id)
        self.assertEqual(resolution.parent_access_code.id, self.access_code.id)

    def test_valid_token_does_not_resolve_to_a_different_student(self):
        """Sanity check the fixture isn't accidentally matching everything —
        a token tied to `self.student`'s code must never resolve to
        `self.other_student`."""
        resolution = resolve_parent_from_token(self.token_str)
        self.assertNotEqual(resolution.student.id, self.other_student.id)

    def test_successful_resolution_touches_the_token(self):
        """Mirrors HasValidParentToken's own behaviour — a successful
        resolution should bump last_seen_at so the rolling 30-day
        inactivity window stays accurate."""
        old_last_seen = timezone.now() - timedelta(days=5)
        ParentToken.objects.filter(pk=self.parent_token.pk).update(last_seen_at=old_last_seen)

        resolve_parent_from_token(self.token_str)

        self.parent_token.refresh_from_db()
        self.assertGreater(self.parent_token.last_seen_at, old_last_seen)

    # -----------------------------------------------------------------
    # Rejection paths — every one of these must return None, never raise
    # -----------------------------------------------------------------
    def test_unknown_token_returns_none(self):
        self.assertIsNone(resolve_parent_from_token("this-token-does-not-exist"))

    def test_empty_token_returns_none(self):
        self.assertIsNone(resolve_parent_from_token(""))

    def test_none_token_returns_none(self):
        self.assertIsNone(resolve_parent_from_token(None))

    def test_expired_token_returns_none(self):
        """ParentToken.is_expired — rolling INACTIVITY_TTL_DAYS=30 from
        last_seen_at, independent of the access code's own expires_at."""
        ParentToken.objects.filter(pk=self.parent_token.pk).update(
            last_seen_at=timezone.now() - timedelta(days=31),
        )
        self.assertIsNone(resolve_parent_from_token(self.token_str))

    def test_expired_access_code_returns_none_even_with_fresh_token(self):
        """ParentAccessCode.expires_at is an independent, absolute expiry
        on the code itself — a device that was active minutes ago must
        still be rejected once the code itself has expired."""
        self.access_code.expires_at = timezone.now() - timedelta(days=1)
        self.access_code.save(update_fields=["expires_at"])

        self.assertIsNone(resolve_parent_from_token(self.token_str))

    def test_deactivated_access_code_returns_none(self):
        """Revoking ParentAccessCode.is_active must invalidate every token
        issued against it at once — this is the single-code-revokes-all-
        devices guarantee CHAT_APP_DOCUMENTATION.md §7.16 describes."""
        self.access_code.is_active = False
        self.access_code.save(update_fields=["is_active"])

        self.assertIsNone(resolve_parent_from_token(self.token_str))

    def test_unexpected_lookup_error_fails_closed(self):
        """An unexpected DB/lookup error must deny access, never raise
        out of this function and never silently grant access — this
        function gates a live audio/video room, unlike the best-effort
        sync functions elsewhere in this module."""
        with patch.object(
            ParentToken.objects, "select_related", side_effect=RuntimeError("boom"),
        ):
            self.assertIsNone(resolve_parent_from_token(self.token_str))

    def test_second_students_parent_token_never_resolves_to_first_student(self):
        """Two independent parent/student pairs — a token for one must
        never leak the other's student. This is the direct regression
        test for 'dusre student ke parent ka reject' from Task 11."""
        other_code = ParentAccessCode.objects.create(
            student=self.other_student,
            label="Papa's phone",
            code="WXYZ6789",
            is_active=True,
            expires_at=timezone.now() + timedelta(days=180),
        )
        other_token_str = "test-parent-token-0002"
        ParentToken.objects.create(
            parent_access_code=other_code, token=other_token_str, last_seen_at=timezone.now(),
        )

        resolution = resolve_parent_from_token(other_token_str)
        self.assertEqual(resolution.student.id, self.other_student.id)
        self.assertNotEqual(resolution.student.id, self.student.id)