"""P2 - `GET /testseries/certificates/of/<username>/` (certificates on someone's profile). NOT RUN.

    python manage.py test testseries.tests_user_certificates

Gate = the same as their posts: blocked -> 404, private account + not following -> empty list, revoked certificates
are never shown to visitors (the owner keeps seeing them), unauthenticated -> 401.
"""
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from user_profile.models import BlockUser, Follow

from .models import TestCertificate
from .tests_advanced import _Base, _answers, _client, _series


def _rows(resp):
    d = resp.json()
    return d["results"] if isinstance(d, dict) and "results" in d else d


@mock.patch("testseries.models._notify")
class UserCertificatesTests(_Base):
    """`self.student` earns the certificates, `self.other` is the visitor."""

    def _earn(self, title="Physics Foundations"):
        series = _series(
            self.teacher, questions=2, pass_percentage=50, certificate_enabled=True, certificate_title=title,
        )
        attempt_id = self.start(series).json()["id"]
        self.submit(attempt_id, _answers(series, ["a", "a"]))
        return TestCertificate.objects.get(series=series, student=self.student)

    def _url(self, username=None):
        return reverse("testseries-certificate-of-user", kwargs={"username": username or self.student.username})

    def test_visitor_sees_valid_certificates_only(self, _notify):
        valid = self._earn("Physics Foundations")
        revoked = self._earn("Chemistry Basics")
        revoked.revoked_at = timezone.now()
        revoked.revoked_reason = "test"
        revoked.save(update_fields=["revoked_at", "revoked_reason"])

        codes = [r["code"] for r in _rows(_client(self.other).get(self._url()))]
        self.assertEqual(codes, [valid.code])

    def test_owner_sees_everything_on_the_same_endpoint(self, _notify):
        valid = self._earn("Physics Foundations")
        revoked = self._earn("Chemistry Basics")
        revoked.revoked_at = timezone.now()
        revoked.save(update_fields=["revoked_at"])

        codes = {r["code"] for r in _rows(_client(self.student).get(self._url()))}
        self.assertEqual(codes, {valid.code, revoked.code})

    def test_username_lookup_is_case_insensitive(self, _notify):
        cert = self._earn()
        res = _client(self.other).get(self._url(self.student.username.upper()))
        self.assertEqual(res.status_code, 200)
        self.assertEqual([r["code"] for r in _rows(res)], [cert.code])

    def test_private_account_is_empty_until_the_viewer_follows(self, _notify):
        cert = self._earn()
        self.student.is_private = True
        self.student.save(update_fields=["is_private"])

        res = _client(self.other).get(self._url())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(_rows(res), [])

        # a PENDING request is not enough
        follow = Follow.objects.create(follower=self.other, following=self.student, status=Follow.Status.PENDING)
        self.assertEqual(_rows(_client(self.other).get(self._url())), [])

        follow.status = Follow.Status.ACCEPTED
        follow.save(update_fields=["status"])
        self.assertEqual([r["code"] for r in _rows(_client(self.other).get(self._url()))], [cert.code])

    def test_blocked_either_way_is_404(self, _notify):
        self._earn()
        block = BlockUser.objects.create(blocker=self.student, blocked=self.other)
        self.assertEqual(_client(self.other).get(self._url()).status_code, 404)  # they blocked me
        block.delete()
        BlockUser.objects.create(blocker=self.other, blocked=self.student)
        self.assertEqual(_client(self.other).get(self._url()).status_code, 404)  # I blocked them

    def test_unknown_or_inactive_user_is_404(self, _notify):
        self.assertEqual(_client(self.other).get(self._url("no_such_user_xyz")).status_code, 404)
        self.teacher.is_active = False
        self.teacher.save(update_fields=["is_active"])
        self.assertEqual(_client(self.other).get(self._url(self.teacher.username)).status_code, 404)

    def test_login_required(self, _notify):
        self._earn()
        self.assertIn(_client().get(self._url()).status_code, (401, 403))

    def test_other_users_certificates_never_leak_into_the_list(self, _notify):
        self._earn()
        # `other` has no certificates of their own
        self.assertEqual(_rows(_client(self.student).get(self._url(self.other.username))), [])
