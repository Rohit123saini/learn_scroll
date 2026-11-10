from django.apps import AppConfig


class UserProfileConfig(AppConfig):
    name = 'user_profile'

    def ready(self):
        # Registers the Follow post_save/post_delete receivers that keep
        # User.followers_count / following_count exact (see signals.py).
        from . import signals  # noqa: F401
        # Auto-moderation receivers (flag-only review queue) — see automod.py.
        from . import automod
        automod.connect()
        # S12 - a new classroom takes its teacher's attendance defaults.
        from . import role_settings
        role_settings.connect()
