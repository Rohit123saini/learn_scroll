"""P-B8 - "would this IP get into /<ADMIN_URL>?" with the LIVE settings (run it BEFORE you turn the allow-list on).

    python manage.py admin_ip_check                 # show the parsed list, proxy count and any bad entries
    python manage.py admin_ip_check 203.0.113.7     # + is this IP allowed
    python manage.py admin_ip_check 10.0.0.2 --xff "203.0.113.7, 10.0.0.2"   # simulate a proxied request (REMOTE_ADDR = the IP)

Exit code 1 when the checked IP would be refused, or when the list has invalid entries.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from common import admin_ip_allowlist as ipl


class Command(BaseCommand):
    help = "Show the admin IP allow-list and test whether an IP would be allowed."

    def add_arguments(self, parser):
        parser.add_argument("ip", nargs="?", help="REMOTE_ADDR to test")
        parser.add_argument("--xff", default="", help="X-Forwarded-For header to simulate")

    def handle(self, *args, **opts):
        raw = getattr(settings, "ADMIN_IP_ALLOWLIST", "") or ""
        nets, bad = ipl.parse_allowlist(raw)
        n = ipl.settings_num_proxies(settings)
        self.stdout.write(f"admin path      : /{settings.ADMIN_URL}")
        self.stdout.write(f"allow-list      : {'OFF (empty)' if not ipl.is_enabled(raw) else ', '.join(str(x) for x in nets) or 'NO VALID ENTRY - everybody is refused'}")
        self.stdout.write(f"trusted proxies : {'not set (only REMOTE_ADDR is used, X-Forwarded-For ignored)' if n is None else n}")
        code = 0
        if bad:
            self.stderr.write(f"invalid entries : {', '.join(bad)}")
            code = 1
        if ipl.has_open_entry(nets):
            self.stderr.write("warning         : a /0 entry lets everybody in - the list does nothing.")
        if opts["ip"]:
            meta = {"REMOTE_ADDR": opts["ip"]}
            if opts["xff"]:
                meta["HTTP_X_FORWARDED_FOR"] = opts["xff"]
            allowed, seen, reason = ipl.decide(settings, meta)
            self.stdout.write(f"client IP seen  : {seen or '?'}")
            self.stdout.write(f"result          : {'ALLOWED' if allowed else 'REFUSED'} ({reason})")
            if not allowed:
                code = 1
        if code:
            raise SystemExit(code)
