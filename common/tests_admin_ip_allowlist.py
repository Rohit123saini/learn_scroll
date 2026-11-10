"""P-B8 tests: admin IP allow-list (common/admin_ip_allowlist.py) + 2FA stays enforced behind it.

Written WITHOUT being run:  python manage.py test common.tests_admin_ip_allowlist
"""
import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings

from common import admin_ip_allowlist as ipl
from core.tests_admin_security import PLAIN_STATIC, STRONG

User = get_user_model()


class ParseTests(SimpleTestCase):
    def test_parse_ips_cidrs_and_garbage(self):
        nets, bad = ipl.parse_allowlist("203.0.113.7, 198.51.100.0/24;2001:db8::/32  banana")
        self.assertEqual(len(nets), 3)
        self.assertEqual(bad, ["banana"])

    def test_empty_means_off_and_garbage_only_still_counts_as_enabled(self):
        self.assertFalse(ipl.is_enabled(""))
        self.assertFalse(ipl.is_enabled("   "))
        self.assertTrue(ipl.is_enabled("banana"))

    def test_membership_v4_v6_and_mapped(self):
        nets, _ = ipl.parse_allowlist("203.0.113.0/24, 2001:db8::/32")
        self.assertTrue(ipl.is_allowed("203.0.113.9", nets))
        self.assertFalse(ipl.is_allowed("203.0.114.9", nets))
        self.assertTrue(ipl.is_allowed("2001:db8::1", nets))
        self.assertTrue(ipl.is_allowed("::ffff:203.0.113.9", nets))   # IPv4 seen through a dual-stack socket
        self.assertFalse(ipl.is_allowed("not-an-ip", nets))
        self.assertFalse(ipl.is_allowed("", nets))

    def test_open_entry_detected(self):
        self.assertTrue(ipl.has_open_entry(ipl.parse_allowlist("0.0.0.0/0")[0]))
        self.assertFalse(ipl.has_open_entry(ipl.parse_allowlist("10.0.0.0/8")[0]))

    def test_num_proxies_parsing(self):
        self.assertIsNone(ipl.parse_num_proxies(""))
        self.assertIsNone(ipl.parse_num_proxies("x"))
        self.assertIsNone(ipl.parse_num_proxies(-1))
        self.assertEqual(ipl.parse_num_proxies("2"), 2)
        self.assertEqual(ipl.parse_num_proxies(0), 0)


class ClientIpTests(SimpleTestCase):
    def test_without_a_proxy_count_the_header_is_ignored(self):
        meta = {"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "203.0.113.7"}
        self.assertEqual(ipl.client_ip(meta, None), "10.0.0.2")
        self.assertEqual(ipl.client_ip(meta, 0), "10.0.0.2")

    def test_one_proxy_takes_the_last_address_so_a_spoofed_prefix_does_not_work(self):
        meta = {"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "1.2.3.4, 203.0.113.7"}   # 1.2.3.4 typed by the attacker
        self.assertEqual(ipl.client_ip(meta, 1), "203.0.113.7")

    def test_two_proxies_take_the_second_from_the_right(self):
        meta = {"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "9.9.9.9, 203.0.113.7, 172.16.0.5"}
        self.assertEqual(ipl.client_ip(meta, 2), "203.0.113.7")

    def test_shorter_chain_than_count_uses_the_first_and_missing_header_the_remote(self):
        self.assertEqual(ipl.client_ip({"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "203.0.113.7"}, 3), "203.0.113.7")
        self.assertEqual(ipl.client_ip({"REMOTE_ADDR": "10.0.0.2"}, 1), "10.0.0.2")


@override_settings(STORAGES=PLAIN_STATIC, ADMIN_REQUIRE_2FA=False, ADMIN_URL="admin/")
class MiddlewareTests(TestCase):
    def setUp(self):
        cache.clear()
        self.c = Client()

    def get(self, path="/admin/login/", **meta):
        return self.c.get(path, **meta)

    @override_settings(ADMIN_IP_ALLOWLIST="")
    def test_off_by_default_nothing_changes(self):
        self.assertNotEqual(self.get(REMOTE_ADDR="8.8.8.8").status_code, 404)

    @override_settings(ADMIN_IP_ALLOWLIST="203.0.113.7", ADMIN_NUM_PROXIES=None)
    def test_listed_ip_gets_in_and_others_get_a_plain_404(self):
        self.assertNotEqual(self.get(REMOTE_ADDR="203.0.113.7").status_code, 404)
        blocked = self.get(REMOTE_ADDR="8.8.8.8")
        self.assertEqual(blocked.status_code, 404)
        self.assertEqual(blocked.content, b"")

    @override_settings(ADMIN_IP_ALLOWLIST="203.0.113.7", ADMIN_NUM_PROXIES=None)
    def test_forged_forwarded_for_does_not_help_when_no_proxy_is_trusted(self):
        r = self.get(REMOTE_ADDR="8.8.8.8", HTTP_X_FORWARDED_FOR="203.0.113.7")
        self.assertEqual(r.status_code, 404)

    @override_settings(ADMIN_IP_ALLOWLIST="203.0.113.7", ADMIN_NUM_PROXIES=1)
    def test_behind_one_proxy_the_visitor_address_is_used(self):
        self.assertNotEqual(self.get(REMOTE_ADDR="10.0.0.2", HTTP_X_FORWARDED_FOR="203.0.113.7").status_code, 404)
        # attacker prepends a listed address; the proxy appended the real one last
        self.assertEqual(self.get(REMOTE_ADDR="10.0.0.2", HTTP_X_FORWARDED_FOR="203.0.113.7, 8.8.8.8").status_code, 404)

    @override_settings(ADMIN_IP_ALLOWLIST="203.0.113.7", ADMIN_NUM_PROXIES=None, REST_FRAMEWORK={"NUM_PROXIES": 1})
    def test_falls_back_to_the_drf_num_proxies(self):
        self.assertNotEqual(self.get(REMOTE_ADDR="10.0.0.2", HTTP_X_FORWARDED_FOR="203.0.113.7").status_code, 404)

    @override_settings(ADMIN_IP_ALLOWLIST="banana")
    def test_a_list_with_no_valid_entry_refuses_everybody(self):
        self.assertEqual(self.get(REMOTE_ADDR="203.0.113.7").status_code, 404)

    @override_settings(ADMIN_IP_ALLOWLIST="203.0.113.7", ADMIN_NUM_PROXIES=None)
    def test_only_the_admin_path_is_guarded(self):
        self.assertEqual(self.get("/admin", REMOTE_ADDR="8.8.8.8").status_code, 404)           # no slash is guarded too
        self.assertEqual(self.get("/admin/anything/", REMOTE_ADDR="8.8.8.8").status_code, 404)
        self.assertNotEqual(self.get("/healthz/", REMOTE_ADDR="8.8.8.8").status_code, 404)     # other URLs never touched

    @override_settings(ADMIN_IP_ALLOWLIST="203.0.113.7", ADMIN_NUM_PROXIES=None)
    def test_blocked_attempt_is_logged(self):
        with mock.patch.object(ipl.logger, "warning") as warn:
            self.get(REMOTE_ADDR="8.8.8.8")
        self.assertTrue(warn.called)

    @override_settings(ADMIN_IP_ALLOWLIST="203.0.113.7", ADMIN_NUM_PROXIES=None, ADMIN_URL="ops-9f3k2/")
    def test_follows_a_custom_admin_url(self):
        # the URLconf was built with "admin/", so this only proves the middleware looks at the CONFIGURED prefix
        self.assertEqual(self.get("/ops-9f3k2/login/", REMOTE_ADDR="8.8.8.8").status_code, 404)


@override_settings(STORAGES=PLAIN_STATIC, ADMIN_REQUIRE_2FA=True, ADMIN_IP_ALLOWLIST="203.0.113.7", ADMIN_NUM_PROXIES=None,
                   ADMIN_URL="admin/")
class TwoFactorStillRequiredTests(TestCase):
    """The allow-list is an extra door, not a replacement: a listed IP + correct password still needs a verified 2FA device."""

    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user("ipstaff", "ipstaff@x.com", STRONG, is_staff=True)

    def test_listed_ip_with_password_but_no_device_cannot_open_the_admin(self):
        c = Client()
        c.force_login(self.staff)                                  # session login WITHOUT an OTP step
        r = c.get("/admin/", REMOTE_ADDR="203.0.113.7")
        self.assertNotEqual(r.status_code, 200)                    # redirected to the login page, not the index

    def test_unlisted_ip_never_reaches_the_login_form(self):
        r = Client().post("/admin/login/", {"username": "ipstaff", "password": STRONG}, REMOTE_ADDR="8.8.8.8")
        self.assertEqual(r.status_code, 404)

    def test_lockout_counts_the_visitor_not_the_proxy(self):
        from common import admin_site

        req = mock.Mock(META={"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "203.0.113.7"})
        with override_settings(ADMIN_NUM_PROXIES=1):
            self.assertEqual(admin_site._client_ip(req), "203.0.113.7")
        with override_settings(ADMIN_NUM_PROXIES=None):
            self.assertEqual(admin_site._client_ip(req), "10.0.0.2")


@override_settings(ADMIN_IP_ALLOWLIST="203.0.113.0/24", ADMIN_NUM_PROXIES=1, ADMIN_URL="admin/")
class CommandTests(SimpleTestCase):
    def run_cmd(self, *args):
        out, err = io.StringIO(), io.StringIO()
        try:
            call_command("admin_ip_check", *args, stdout=out, stderr=err)
            code = 0
        except SystemExit as e:
            code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_allowed_and_refused(self):
        code, out, _ = self.run_cmd("10.0.0.2", "--xff", "203.0.113.9")
        self.assertEqual(code, 0)
        self.assertIn("ALLOWED", out)
        code, out, _ = self.run_cmd("10.0.0.2", "--xff", "8.8.8.8")
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", out)

    @override_settings(ADMIN_IP_ALLOWLIST="203.0.113.0/24 oops")
    def test_invalid_entry_is_reported(self):
        code, _out, err = self.run_cmd()
        self.assertEqual(code, 1)
        self.assertIn("oops", err)
