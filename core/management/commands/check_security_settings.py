"""X6 -- verify the LIVE security settings (CSRF / ALLOWED_HOSTS / cookies / CORS / middleware) + scan for csrf_exempt.

    python manage.py check_security_settings                  # DEBUG off => production rules, DEBUG on => dev (warnings)
    python manage.py check_security_settings --production     # force production rules even with DEBUG on
    python manage.py check_security_settings --fail-on warn   # warnings also fail (CI "strict")
    python manage.py check_security_settings --json           # machine readable

Two checks:
  1. core/security_settings_check.py on the settings that really loaded (not the .env file - that is `release_check`).
  2. a static scan of the project's .py files for `csrf_exempt` (a view that switches CSRF off). The app talks Bearer
     tokens (DRF APIViews are already csrf-exempt by design), so a Django `@csrf_exempt` is only expected on a webhook.
     Anything not in settings.CSRF_EXEMPT_ALLOWED (a list of "path/to/file.py") is a warning.
Reads only, writes nothing. Exit code 0 = fine. Not run by the author.
"""
import json
import os
import re

from django.conf import settings
from django.core.management import BaseCommand, CommandError

from core.release_check import ERROR, WARN, Finding, exit_code, summarize
from core.security_settings_check import check_settings, settings_snapshot

_EXEMPT_RE = re.compile(r"\bcsrf_exempt\b")
_SKIP_DIRS = {"migrations", "__pycache__", "node_modules", ".git", "venv", ".venv", "staticfiles"}


def scan_csrf_exempt(root, allowed=()):
    """Findings for every non-test .py file under ``root`` that mentions csrf_exempt and is not allow-listed."""
    findings = []
    allowed = {a.replace("\\", "/") for a in allowed}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if not name.endswith(".py") or name.startswith("tests") or name.startswith("test_"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root).replace("\\", "/")
            if rel in allowed or rel.endswith("management/commands/check_security_settings.py"):
                continue
            if rel.endswith("core/security_settings_check.py"):
                continue
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, 1):
                        code = line.split("#", 1)[0]
                        if _EXEMPT_RE.search(code):
                            findings.append(Finding(
                                WARN, "csrf_exempt",
                                f"{rel}:{lineno} switches CSRF off for a view. Fine for a signed webhook - add the file "
                                f"to settings.CSRF_EXEMPT_ALLOWED once you have confirmed that; otherwise remove it."))
            except OSError:
                continue
    return findings


class Command(BaseCommand):
    help = "Verify the live CSRF / host / cookie / CORS settings and scan for csrf_exempt (X6)."

    def add_arguments(self, parser):
        parser.add_argument("--production", action="store_true", help="Apply production rules even when DEBUG is on.")
        parser.add_argument("--fail-on", choices=["error", "warn"], default="error")
        parser.add_argument("--json", action="store_true", help="Print findings as JSON.")
        parser.add_argument("--skip-scan", action="store_true", help="Do not scan the source tree for csrf_exempt.")

    def handle(self, *args, **opts):
        fail_on = ERROR if opts["fail_on"] == "error" else WARN
        findings = check_settings(settings_snapshot(settings), production=True if opts["production"] else None)
        if not opts["skip_scan"]:
            findings += scan_csrf_exempt(str(settings.BASE_DIR), getattr(settings, "CSRF_EXEMPT_ALLOWED", ()))

        if opts["json"]:
            self.stdout.write(json.dumps(
                [{"level": x.level, "key": x.key, "message": x.message} for x in findings], indent=2))
        else:
            for x in findings:
                line = str(x)
                self.stdout.write(self.style.ERROR(line) if x.level == ERROR else self.style.WARNING(line))
            s = summarize(findings)
            self.stdout.write(f"{s['error']} error(s), {s['warn']} warning(s)")

        if exit_code(findings, fail_on):
            raise CommandError("check_security_settings FAILED.")
        if not opts["json"]:
            self.stdout.write(self.style.SUCCESS("check_security_settings OK"))
