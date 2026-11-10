from django.contrib.admin.apps import AdminConfig


class SecureAdminConfig(AdminConfig):
    """Use in INSTALLED_APPS INSTEAD of 'django.contrib.admin' (same app label: 'admin')."""

    default_site = "common.admin_site.SecureAdminSite"
