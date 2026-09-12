from django.apps import AppConfig


class AssignmentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "assignment"
    verbose_name = "Assignments"

    def ready(self):
        # No signal wiring needed here — every pre_save/post_save/
        # post_delete receiver in this app is declared directly in
        # models.py with @receiver, so they connect the moment Django's
        # app registry imports that module (which it always does before
        # ready() runs). Left as an explicit no-op + comment, not a
        # duplicate `import .models`, so a future reader isn't left
        # wondering whether signals are actually live.
        pass
