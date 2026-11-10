from django.apps import AppConfig


class CopyrightsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "copyrights"
    verbose_name = "Copyright & takedowns"

    def ready(self):
        from . import checks  # noqa: F401  (registers the system checks)
        from . import signals

        signals.connect()  # hold exact re-uploads of removed files
