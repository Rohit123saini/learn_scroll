"""
core/serializers.py

The project already has an established ModelSerializer convention (see
tuitionclass/serializers.py), so — per core_app_documentation.md section 7's
own suggestion — this uses real DRF ModelSerializers rather than the
plain `to_dict` staticmethod the original design doc sketched out.
Behavior matches that doc: same field list, same read-only-everything
posture (rows are only ever created server-side), plus the `source`
field for task 46's message-vs-tuitionclass split.

N3-BE — NotificationSerializer additionally exposes `category`, `actors`
(max 3), `actor_count` and `thumbnail_url`. All four are ADDITIVE: no
existing field was renamed, removed or re-typed, so old clients that don't
read them keep working unchanged.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from rest_framework import serializers

from login.models import User

from .models import Notification, NotificationPreference

MAX_ACTORS = 3

# Where an actor id can live inside Notification.data (first match wins).
# Batched rows ("A, B and 5 others liked your post") carry a list; single
# rows carry one id. Adjust here if the services use a different key.
_ACTOR_LIST_KEYS = ("actor_ids", "actors")
_ACTOR_SINGLE_KEYS = ("actor_id", "sender_id", "from_user_id")

# User attribute that holds the profile photo (first one that exists and is
# non-empty wins). Adjust to the real login.User field name.
_PHOTO_ATTRS = ("profile_picture", "profile_photo", "photo", "avatar", "image")


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_actor_ids(data) -> list:
    """Ordered, de-duplicated actor ids from `Notification.data`. Never
    raises — bad/missing data just means "no actors"."""
    if not isinstance(data, dict):
        return []
    raw = []
    for key in _ACTOR_LIST_KEYS:
        value = data.get(key)
        if isinstance(value, (list, tuple)):
            for item in value:
                # tolerate [1, 2] as well as [{"id": 1, ...}, ...]
                raw.append(item.get("id") if isinstance(item, dict) else item)
            break
    else:
        for key in _ACTOR_SINGLE_KEYS:
            if data.get(key) is not None:
                raw.append(data[key])
                break
    seen, ids = set(), []
    for item in raw:
        uid = _as_int(item)
        if uid is not None and uid not in seen:
            seen.add(uid)
            ids.append(uid)
    return ids


def _photo_url(user, request):
    for attr in _PHOTO_ATTRS:
        f = getattr(user, attr, None)
        if not f:
            continue
        url = getattr(f, "url", f)  # ImageField/FileField or plain str URL
        if not isinstance(url, str) or not url:
            continue
        try:
            return request.build_absolute_uri(url) if request is not None else url
        except Exception:
            return url
    return None


class NotificationListSerializer(serializers.ListSerializer):
    """Resolves the actors of the WHOLE page in one query (instead of one
    User query per notification) before the child serializer runs."""

    def to_representation(self, data):
        rows = data.all() if hasattr(data, "all") else data
        rows = list(rows)
        ids = set()
        for n in rows:
            ids.update(extract_actor_ids(n.data)[:MAX_ACTORS])
        self.child._actor_cache = (
            {u.id: u for u in User.objects.filter(id__in=ids)} if ids else {}
        )
        return super().to_representation(rows)


class NotificationSerializer(serializers.ModelSerializer):
    """Read-only — rows are only ever created server-side (see
    core/services.py::create_notification and
    core/notification_batching.py::create_batched_notification). The
    "mark read" actions on the viewset don't need a request body at all,
    let alone a writable serializer here."""

    classroom_id = serializers.PrimaryKeyRelatedField(source="classroom", read_only=True)
    session_id = serializers.PrimaryKeyRelatedField(source="session", read_only=True)
    source = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    actors = serializers.SerializerMethodField()
    actor_count = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        list_serializer_class = NotificationListSerializer
        fields = [
            "id", "notif_type", "title", "message",
            "classroom_id", "session_id", "data",
            "is_read", "read_at", "created_at", "source",
            "category", "actors", "actor_count", "thumbnail_url",
        ]
        read_only_fields = fields

    def get_source(self, obj) -> str:
        return "message" if obj.notif_type in Notification.MESSAGE_APP_TYPES else "tuitionclass"

    def get_category(self, obj) -> str:
        return Notification.category_for(obj.notif_type)

    # ---- actors -------------------------------------------------------
    def _users_for(self, ids):
        cache = getattr(self, "_actor_cache", None)
        if cache is None:  # single-object path (retrieve / mark-read)
            cache = {u.id: u for u in User.objects.filter(id__in=ids)} if ids else {}
        return cache

    def get_actors(self, obj) -> list:
        ids = extract_actor_ids(obj.data)[:MAX_ACTORS]
        users = self._users_for(ids)
        request = self.context.get("request")
        out = []
        for uid in ids:
            user = users.get(uid)
            if user is None:  # deleted user — just skip, don't break the row
                continue
            out.append({
                "id": user.id,
                "username": user.username,
                "photo": _photo_url(user, request),
            })
        return out

    def get_actor_count(self, obj) -> int:
        """Total actors, not just the 3 shown ("A, B and 5 others").
        A batching service may store the real total as data.actor_count;
        otherwise fall back to the number of ids we can see."""
        data = obj.data if isinstance(obj.data, dict) else {}
        stored = _as_int(data.get("actor_count"))
        seen = len(extract_actor_ids(data))
        return max(stored or 0, seen)

    def get_thumbnail_url(self, obj):
        data = obj.data if isinstance(obj.data, dict) else {}
        url = data.get("media_url")
        return url if isinstance(url, str) and url else None


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    """Used both to READ the caller's own settings and to PATCH them
    (NotificationPreferenceView in views.py) — every field is writable
    except last_digest_sent_at/updated_at (only ever advanced
    server-side, never by the client).

    N9-BE — quiet hours / DND:
      * quiet_start / quiet_end ("HH:MM" in, "HH:MM:SS" out) + timezone
        (IANA name). Both times null = off; setting only one is a 400.
      * dnd_until is READ-ONLY. The client sets it through the write-only
        `dnd_for_minutes` (0 clears), so the deadline comes from the
        server clock and a wrong phone clock can't pause alerts for days.
    """

    dnd_for_minutes = serializers.IntegerField(
        write_only=True, required=False, min_value=0, max_value=7 * 24 * 60
    )

    class Meta:
        model = NotificationPreference
        fields = [
            "push_enabled", "email_enabled", "sms_enabled", "whatsapp_enabled",
            "muted_types", "digest_frequency", "last_digest_sent_at", "updated_at",
            "quiet_start", "quiet_end", "timezone", "dnd_until", "dnd_for_minutes",
        ]
        read_only_fields = ["last_digest_sent_at", "updated_at", "dnd_until"]

    def validate_muted_types(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("muted_types must be a list of notification type strings.")
        valid_types = set(Notification.NotifType.values)
        invalid = [v for v in value if v not in valid_types]
        if invalid:
            raise serializers.ValidationError(f"Unknown notification type(s): {', '.join(invalid)}.")
        return value

    def validate_timezone(self, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise serializers.ValidationError("Unknown timezone (expected an IANA name like 'Asia/Kolkata').")
        return value

    def validate(self, attrs):
        inst = self.instance
        start = attrs["quiet_start"] if "quiet_start" in attrs else getattr(inst, "quiet_start", None)
        end = attrs["quiet_end"] if "quiet_end" in attrs else getattr(inst, "quiet_end", None)
        if (start is None) != (end is None):
            raise serializers.ValidationError(
                {"quiet_start": "quiet_start and quiet_end must be set together (or both cleared)."}
            )
        if start is not None and start == end:
            raise serializers.ValidationError(
                {"quiet_end": "quiet_end must differ from quiet_start."}
            )
        minutes = attrs.pop("dnd_for_minutes", None)
        if minutes is not None:
            attrs["dnd_until"] = timezone.now() + timedelta(minutes=minutes) if minutes > 0 else None
        return attrs
