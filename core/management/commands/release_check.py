"""X1 — one command before every release.

    python manage.py release_check                       # checks the CURRENT environment
    python manage.py release_check --env-file .env.production    # checks a file without loading it
    python manage.py release_check --fail-on warn        # warnings also fail (CI "strict")

Runs three gates and prints one verdict:
  1. env rules (core/release_check.py: SECRET_KEY, ALLOWED_HOSTS, Postgres, Redis, S3/CDN, FCM, APNs, LiveKit, Razorpay, Sentry ...)
  2. `manage.py check --deploy --fail-level WARNING`
  3. `manage.py makemigrations --check --dry-run`   (model change without a migration file = failure)
Exit code 0 only when every gate passes. Reads / checks only - writes nothing. Not run by the author.
"""
import io
import os

from django.conf import settings
from django.core.management import BaseCommand, call_command
from django.core.management.base import CommandError, SystemCheckError

from core.release_check import ERROR, WARN, check_env, exit_code, parse_env_file, summarize


class Command(BaseCommand):
    help = "Release gate: production env rules + check --deploy + makemigrations --check."

    def add_arguments(self, parser):
        parser.add_argument("--env-file", help="Check this KEY=VALUE file instead of os.environ.")
        parser.add_argument("--fail-on", choices=["error", "warn"], default="error")
        parser.add_argument("--skip-env", action="store_true")
        parser.add_argument("--skip-deploy-check", action="store_true")
        parser.add_argument("--skip-migrations", action="store_true")

    def handle(self, *args, **opts):
        failed = False
        fail_on = ERROR if opts["fail_on"] == "error" else WARN

        if not opts["skip_env"]:
            self.stdout.write(self.style.MIGRATE_HEADING("1/3 environment rules"))
            if opts["env_file"]:
                try:
                    with open(opts["env_file"], encoding="utf-8") as fh:
                        env = parse_env_file(fh.read())
                except OSError as exc:
                    raise CommandError(f"cannot read {opts['env_file']}: {exc}")
            else:
                env = dict(os.environ)
            fcm_default = str(getattr(settings, "FCM_SERVICE_ACCOUNT_JSON_PATH", "firebase-service-account.json"))
            findings = check_env(env, default_fcm_path=fcm_default)
            for x in findings:
                line = str(x)
                self.stdout.write(self.style.ERROR(line) if x.level == ERROR else self.style.WARNING(line))
            s = summarize(findings)
            self.stdout.write(f"   {s['error']} error(s), {s['warn']} warning(s)")
            failed |= bool(exit_code(findings, fail_on))

        if not opts["skip_deploy_check"]:
            self.stdout.write(self.style.MIGRATE_HEADING("2/3 manage.py check --deploy --fail-level WARNING"))
            buf = io.StringIO()
            try:
                call_command("check", deploy=True, fail_level="WARNING", stdout=buf, stderr=buf)
                self.stdout.write("   clean")
            except SystemCheckError as exc:
                self.stdout.write(self.style.ERROR(str(exc)))
                failed = True

        if not opts["skip_migrations"]:
            self.stdout.write(self.style.MIGRATE_HEADING("3/3 makemigrations --check --dry-run"))
            buf = io.StringIO()
            try:
                call_command("makemigrations", check=True, dry_run=True, verbosity=1, stdout=buf, stderr=buf)
                self.stdout.write("   no model changes without a migration")
            except SystemExit:
                self.stdout.write(self.style.ERROR("   models changed but no migration file exists:\n" + buf.getvalue()))
                failed = True

        if failed:
            raise CommandError("release_check FAILED - do not ship.")
        self.stdout.write(self.style.SUCCESS("release_check OK"))
