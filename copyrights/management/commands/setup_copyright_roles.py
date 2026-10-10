"""Create the three copyright staff groups. Safe to run any number of times:

    python manage.py setup_copyright_roles

Then put each moderator into ONE group from Django admin -> Users.
(The user must also be `is_staff`.)
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from copyrights.permissions import ROLE_GROUPS


class Command(BaseCommand):
    help = "Create / refresh the Copyright L1 / L2 / L3 staff groups."

    def handle(self, *args, **opts):
        view_perms = list(Permission.objects.filter(
            content_type__app_label="copyrights", codename__startswith="view_"))
        for name, codenames in ROLE_GROUPS.items():
            group, _ = Group.objects.get_or_create(name=name)
            perms = list(Permission.objects.filter(content_type__app_label="copyrights", codename__in=codenames))
            group.permissions.set(perms + view_perms)
            self.stdout.write(self.style.SUCCESS(f"{name}: {len(perms)} action perms + {len(view_perms)} view perms"))
