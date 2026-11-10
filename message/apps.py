from django.apps import AppConfig


class MessageConfig(AppConfig):
    name = 'message'

    def ready(self):
        # N3 - login session (AuthToken) delete hone par (logout / logout-all / sign-out device / password change /
        # sliding expiry) us session ke FCM device tokens bhi hatao, taaki logged-out phone par push na aaye.
        from django.db.models.signals import post_delete

        from login.models import AuthToken

        post_delete.connect(
            _session_deleted_receiver, sender=AuthToken, dispatch_uid='message_n3_revoke_tokens_on_session_delete',
        )


def _session_deleted_receiver(sender, instance, **kwargs):
    from .push_reliability import on_session_deleted

    on_session_deleted(instance)
