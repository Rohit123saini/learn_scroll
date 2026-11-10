from django.apps import AppConfig


class AssignmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    # T3.10 — the PYTHON package is spelled correctly now (`assignments`), but the app LABEL deliberately
    # stays the historical "assigments": the label decides the DB table names (assigments_*), the
    # `assigments.<Model>` FK strings, every migration dependency and the content types. Changing it would
    # need a table-rename migration on live data — see user_profile/WALLET_CONSOLIDATION.md's sibling doc
    # `assignments/SPELLING_CLEANUP.md` for the staged plan.
    name = "assignments"
    label = "assigments"
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
