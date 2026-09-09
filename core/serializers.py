"""
core/serializers.py

The project already has an established ModelSerializer convention (see
liveclass/serializers.py), so — per core_app_documentation.md section 7's
own suggestion — this uses real DRF ModelSerializers rather than the
plain `to_dict` staticmethod the original design doc sketched out.
Behavior matches that doc: same field list, same read-only-everything
posture (rows are only ever created server-side), plus the `source`
field for task 46's message-vs-liveclass split.
"""
from rest_framework import serializers

from .models import Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    """Read-only — rows are only ever created server-side (see
    core/services.py::create_notification and
    core/notification_batching.py::create_batched_notification). The
    "mark read" actions on the viewset don't need a request body at all,
    let alone a writable serializer here."""

    classroom_id = serializers.PrimaryKeyRelatedField(source="classroom", read_only=True)
    session_id = serializers.PrimaryKeyRelatedField(source="session", read_only=True)
    source = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id", "notif_type", "title", "message",
            "classroom_id", "session_id", "data",
            "is_read", "read_at", "created_at", "source",
        ]
        read_only_fields = fields

    def get_source(self, obj) -> str:
        return "message" if obj.notif_type in Notification.MESSAGE_APP_TYPES else "liveclass"


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    """Used both to READ the caller's own settings and to PATCH them
    (NotificationPreferenceView in views.py) — every field is writable
    except last_digest_sent_at/updated_at (only ever advanced
    server-side, never by the client)."""

    class Meta:
        model = NotificationPreference
        fields = [
            "push_enabled", "email_enabled", "sms_enabled", "whatsapp_enabled",
            "muted_types", "digest_frequency", "last_digest_sent_at", "updated_at",
        ]
        read_only_fields = ["last_digest_sent_at", "updated_at"]

    def validate_muted_types(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("muted_types must be a list of notification type strings.")
        valid_types = set(Notification.NotifType.values)
        invalid = [v for v in value if v not in valid_types]
        if invalid:
            raise serializers.ValidationError(f"Unknown notification type(s): {', '.join(invalid)}.")
        return value