"""
Enrol a moderator / admin in two-factor authentication (task 14).

  Step 1:  python manage.py setup_staff_2fa alice
           -> creates an UNCONFIRMED authenticator device, prints the QR code + otpauth URL.
              The person scans it with Google Authenticator / Authy / 1Password.
  Step 2:  python manage.py setup_staff_2fa alice --confirm 123456
           -> verifies the 6-digit code from the app, activates the device and prints 8
              one-time BACKUP CODES (shown once; store them safely).

  python manage.py setup_staff_2fa alice --reset     start over (lost phone): removes old devices.

Until a device is confirmed the user cannot log into /admin/ when ADMIN_REQUIRE_2FA is on.
Run this on the server (shell access) so the secret never travels over chat / e-mail.
"""
import io

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

DEVICE_NAME = "authenticator"
BACKUP_CODES = 8


class Command(BaseCommand):
    help = "Enrol a staff user in TOTP two-factor authentication."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--confirm", metavar="CODE", help="6-digit code from the authenticator app")
        parser.add_argument("--reset", action="store_true", help="delete existing 2FA devices and start over")

    def handle(self, *args, **opts):
        User = get_user_model()
        try:
            user = User.objects.get(**{User.USERNAME_FIELD: opts["username"]})
        except User.DoesNotExist:
            raise CommandError(f"No such user: {opts['username']}")
        if not user.is_active:
            raise CommandError("User is not active.")
        if not (user.is_staff or user.is_superuser):
            raise CommandError("2FA is only for staff users (is_staff / is_superuser).")

        with transaction.atomic():
            if opts["reset"]:
                TOTPDevice.objects.filter(user=user).delete()
                StaticDevice.objects.filter(user=user).delete()
                self.stdout.write(self.style.WARNING("Old 2FA devices removed."))

            device = TOTPDevice.objects.filter(user=user, name=DEVICE_NAME).first()

            if opts["confirm"]:
                if device is None:
                    raise CommandError("No pending device. Run without --confirm first.")
                if device.confirmed:
                    raise CommandError("Already confirmed. Use --reset to re-enrol.")
                if not device.verify_token(opts["confirm"].strip()):
                    raise CommandError("That code is wrong or expired. Check the phone clock and try again.")
                device.confirmed = True
                device.save(update_fields=["confirmed"])
                codes = self._make_backup_codes(user)
                self.stdout.write(self.style.SUCCESS(f"2FA is now ACTIVE for {user}."))
                self.stdout.write("One-time backup codes (shown ONLY now; each works once):")
                for c in codes:
                    self.stdout.write(f"   {c}")
                return

            if device is not None and device.confirmed:
                raise CommandError("2FA is already active for this user. Use --reset to re-enrol.")
            if device is None:
                device = TOTPDevice.objects.create(user=user, name=DEVICE_NAME, confirmed=False)

        self.stdout.write(f"Scan this with the authenticator app, then run:\n"
                          f"  python manage.py setup_staff_2fa {opts['username']} --confirm <6-digit code>\n")
        try:
            import qrcode  # optional

            qr = qrcode.QRCode(border=1)
            qr.add_data(device.config_url)
            buf = io.StringIO()
            qr.print_ascii(out=buf, invert=True)
            self.stdout.write(buf.getvalue())
        except Exception:  # qrcode missing / terminal can't draw it -> the URL still works
            self.stdout.write("(install `qrcode` to see a QR here)")
        self.stdout.write(f"\notpauth URL: {device.config_url}")

    @staticmethod
    def _make_backup_codes(user):
        StaticDevice.objects.filter(user=user).delete()
        dev = StaticDevice.objects.create(user=user, name="backup", confirmed=True)
        codes = []
        for _ in range(BACKUP_CODES):
            tok = StaticToken.random_token()
            StaticToken.objects.create(device=dev, token=tok)
            codes.append(tok)
        return codes
