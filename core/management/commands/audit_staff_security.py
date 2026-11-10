"""
Who can reach the admin / copyright moderation, and are they protected? (task 14)

  python manage.py audit_staff_security            # print a table
  python manage.py audit_staff_security --strict   # exit code 1 if any active staff user lacks 2FA
                                                   # (use in CI / a cron job / deploy pipeline)
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice

from copyrights.permissions import level_for


class Command(BaseCommand):
    help = "List active staff accounts, their copyright level and 2FA status."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true", help="fail (exit 1) if anyone lacks 2FA")

    def handle(self, *args, **opts):
        User = get_user_model()
        staff = User.objects.filter(is_active=True).filter(is_staff=True) | User.objects.filter(
            is_active=True, is_superuser=True)
        staff = staff.distinct().order_by("username")
        with_2fa = set(TOTPDevice.objects.filter(confirmed=True).values_list("user_id", flat=True))
        missing = []
        self.stdout.write(f"{'username':<24}{'super':<7}{'copyright':<11}{'2FA':<6}last_login")
        for u in staff:
            ok = u.pk in with_2fa
            if not ok:
                missing.append(u.username)
            lvl = level_for(u)
            last = f"{u.last_login:%Y-%m-%d}" if u.last_login else "never"
            self.stdout.write(
                f"{u.username:<24}{('yes' if u.is_superuser else '-'):<7}"
                f"{('L%d' % lvl if lvl else '-'):<11}{('yes' if ok else 'NO'):<6}{last}")
        self.stdout.write(f"\n{staff.count()} active staff, {len(missing)} without 2FA.")
        if missing:
            self.stdout.write(self.style.WARNING("Enrol with: manage.py setup_staff_2fa <username>  -> " + ", ".join(missing)))
            if opts["strict"]:
                raise CommandError("Staff without 2FA: " + ", ".join(missing))
