"""Run:  python manage.py test core.tests_notification_cleanup

Covers the two "notification stays forever" fixes:
  * DELETE /core/notifications/clear-read/  — only the caller's READ rows go.
  * manage.py cleanup_notifications          — retention by age.
"""
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Notification

User = get_user_model()
NT = Notification.NotifType
CLEAR_URL = "/core/notifications/clear-read/"


def _notif(user, *, read, age_days=0, ntype=NT.GENERIC):
    n = Notification.objects.create(recipient=user, notif_type=ntype, title="t", is_read=read)
    if age_days:
        # created_at is auto_now_add -> must be back-dated with update().
        Notification.objects.filter(pk=n.pk).update(created_at=timezone.now() - timedelta(days=age_days))
    return n


class ClearReadTests(TestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="me_clear", password="pw12345!")
        self.other = User.objects.create_user(username="other_clear", password="pw12345!")
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def test_deletes_only_my_read_rows(self):
        read_mine = _notif(self.me, read=True)
        unread_mine = _notif(self.me, read=False)
        read_other = _notif(self.other, read=True)

        res = self.client.delete(CLEAR_URL)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["deleted"], 1)
        self.assertFalse(Notification.objects.filter(pk=read_mine.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=unread_mine.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=read_other.pk).exists())

    def test_requires_auth(self):
        res = APIClient().delete(CLEAR_URL)
        self.assertIn(res.status_code, (401, 403))

    def test_idempotent_when_nothing_to_clear(self):
        _notif(self.me, read=False)
        res = self.client.delete(CLEAR_URL)
        self.assertEqual(res.data["deleted"], 0)


class RetentionCommandTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user(username="ret_user", password="pw12345!")

    def _run(self, *args):
        out = StringIO()
        call_command("cleanup_notifications", *args, stdout=out)
        return out.getvalue()

    def test_old_read_and_very_old_unread_are_deleted_recent_kept(self):
        old_read = _notif(self.u, read=True, age_days=40)
        recent_read = _notif(self.u, read=True, age_days=5)
        old_unread = _notif(self.u, read=False, age_days=40)      # unread < 90d -> kept
        ancient_unread = _notif(self.u, read=False, age_days=120)  # > 90d -> gone

        self._run()

        alive = set(Notification.objects.values_list("pk", flat=True))
        self.assertNotIn(old_read.pk, alive)
        self.assertNotIn(ancient_unread.pk, alive)
        self.assertIn(recent_read.pk, alive)
        self.assertIn(old_unread.pk, alive)

    def test_dry_run_deletes_nothing(self):
        n = _notif(self.u, read=True, age_days=400)
        out = self._run("--dry-run")
        self.assertIn("dry-run", out)
        self.assertTrue(Notification.objects.filter(pk=n.pk).exists())
