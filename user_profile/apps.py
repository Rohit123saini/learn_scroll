from django.apps import AppConfig


class UserProfileConfig(AppConfig):
    name = 'user_profile'

    def ready(self):
        # Registers the Follow post_save/post_delete receivers that keep
        # User.followers_count / following_count exact (see signals.py).
        from . import signals  # noqa: F401
