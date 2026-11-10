from django.apps import AppConfig


class LoginConfig(AppConfig):
    name = 'login'

    def ready(self):
        # T1.5 - .env me Google client id na ho to startup par warning (silent 500 se behtar).
        # `manage.py migrate/makemigrations/test` jaise commands me shor na ho, isliye sirf server-type processes me.
        import sys

        if any(cmd in sys.argv for cmd in ("migrate", "makemigrations", "collectstatic", "test", "shell", "check")):
            return
        try:
            from . import google_verify

            google_verify.check_configuration()
        except Exception:  # pragma: no cover - a warning helper must never stop the server from starting
            pass

