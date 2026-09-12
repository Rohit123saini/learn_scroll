from django.apps import AppConfig


class CoreConfig(AppConfig):
    """
    Neutral, app-agnostic layer (task 42-48). Centralizes:
      1. The notification system (Notification / NotificationPreference
         models, create_notification(), create_batched_notification(),
         and the REST endpoints that expose them).
      2. The liveclass <-> message classroom/chat bridge
         (classroom_chat_bridge.py).

    `liveclass`, `message`, and future Phase 5 apps (Posts/Follow/Like)
    all depend on `core` — `core` never imports from them at module load
    time (only local imports inside functions, see
    classroom_chat_bridge.py), so it stays the neutral bottom layer of
    the dependency graph. See core_app_documentation.md for the full
    design writeup.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"