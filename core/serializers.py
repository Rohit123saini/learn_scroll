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
from .notification_routes import route_for  # N2

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
        # N2 — live follow status of the viewer towards every single-actor
        # follow row of the page, in ONE query (not one per notification).
        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        follow_ids = set()
        for n in rows:
            actor_id = NotificationSerializer.single_follow_actor_id(n)
            if actor_id is not None:
                follow_ids.add(actor_id)
        self.child._follow_map = (
            NotificationSerializer.follow_status_map(viewer, follow_ids)
        )
        # N9 - which classes / chats / posts of this page the viewer muted: ONE query for the whole page.
        self.child._scope_mute_set = NotificationSerializer.scope_mute_set(viewer, rows)
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
    # N2 — LIVE follow state of the viewer towards the (single) actor of a
    # follow notification. Computed from the Follow table on every request,
    # never read from the row's `data` JSON (that was a creation-time snapshot).
    #   is_following_actor: True (ACCEPTED) | False | None (not a single-actor follow row)
    #   follow_requested:   True when the viewer's follow request is still PENDING
    is_following_actor = serializers.SerializerMethodField()
    follow_requested = serializers.SerializerMethodField()
    # N2 - {route, target_type, target_id}: where a tap on this row goes (core/notification_routes.py). Computed per
    # row from notif_type + data + classroom_id, never stored (no migration).
    route = serializers.SerializerMethodField()
    # N9 - "Why am I seeing this?": {code, en, hi, group, locked, control, scope}. Pure (no query) - core/notification_center.py.
    why = serializers.SerializerMethodField()
    # N9 - True when the viewer currently muted the class / chat / post this row belongs to (push is silenced, the row
    # stays). None = the row has no mutable scope.
    scope_muted = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        list_serializer_class = NotificationListSerializer
        fields = [
            "id", "notif_type", "title", "message",
            "classroom_id", "session_id", "data",
            "is_read", "read_at", "created_at", "source",
            "category", "actors", "actor_count", "thumbnail_url",
            "is_following_actor", "follow_requested", "route",
            "why", "scope_muted",
        ]
        read_only_fields = fields

    # ---- N9: why + scope mute ---------------------------------------
    @staticmethod
    def scope_mute_set(viewer, rows):
        """{(scope_type, scope_id)} the viewer muted among the scopes of `rows`. One query; empty for anonymous."""
        if viewer is None or not getattr(viewer, "is_authenticated", False):
            return set()
        from .notification_center import muted_scope_set, scope_mutable, scopes_for

        cands = set()
        for n in rows:
            if scope_mutable(n.notif_type):
                cands.update(scopes_for(n.notif_type, n.data, classroom_id=n.classroom_id))
        return muted_scope_set(viewer.pk, sorted(cands))

    def get_why(self, obj):
        from .notification_center import why_for

        return why_for(obj)

    def get_scope_muted(self, obj):
        from .notification_center import scope_mutable, scopes_for

        if not scope_mutable(obj.notif_type):
            return None
        cands = scopes_for(obj.notif_type, obj.data, classroom_id=obj.classroom_id)
        if not cands:
            return None
        muted = getattr(self, "_scope_mute_set", None)
        if muted is None:  # single-object path
            request = self.context.get("request")
            muted = self.scope_mute_set(getattr(request, "user", None), [obj])
        return any(c in muted for c in cands)

    # ---- N2: live follow state ---------------------------------------
    @staticmethod
    def single_follow_actor_id(obj):
        """The one actor a follow-back button could target, else None.
        Only follow-type rows with exactly one actor qualify — a merged
        row ("X, Y and 12 others started following you") has no single
        actor to follow back."""
        if obj.notif_type not in (
            Notification.NotifType.FOLLOW_REQUEST_ACCEPTED,
            Notification.NotifType.FOLLOW_REQUEST_RECEIVED,
        ):
            return None
        data = obj.data if isinstance(obj.data, dict) else {}
        ids = extract_actor_ids(data)
        stored = _as_int(data.get("actor_count")) or 0
        if len(ids) != 1 or stored > 1:
            return None
        return ids[0]

    @staticmethod
    def follow_status_map(viewer, actor_ids):
        """{actor_id: 'ACCEPTED' | 'PENDING'} for the viewer -> actor edge.
        Missing key = viewer doesn't follow that actor."""
        if viewer is None or not getattr(viewer, "is_authenticated", False) or not actor_ids:
            return {}
        from user_profile.models import Follow  # lazy: user_profile imports core lazily too

        return dict(
            Follow.objects.filter(follower_id=viewer.pk, following_id__in=list(actor_ids))
            .values_list("following_id", "status")
        )

    def _follow_status(self, obj):
        """(has_single_actor, status|None) — uses the page-level map the list
        serializer prepared, or one direct query on the single-object path."""
        actor_id = self.single_follow_actor_id(obj)
        if actor_id is None:
            return False, None
        fmap = getattr(self, "_follow_map", None)
        if fmap is None:
            request = self.context.get("request")
            fmap = self.follow_status_map(getattr(request, "user", None), {actor_id})
        return True, fmap.get(actor_id)

    def get_is_following_actor(self, obj):
        single, status = self._follow_status(obj)
        if not single:
            return None
        return status == "ACCEPTED"

    def get_follow_requested(self, obj) -> bool:
        _, status = self._follow_status(obj)
        return status == "PENDING"

    def get_source(self, obj) -> str:
        return "message" if obj.notif_type in Notification.MESSAGE_APP_TYPES else "tuitionclass"

    def get_category(self, obj) -> str:
        return Notification.category_for(obj.notif_type)

    def get_route(self, obj) -> dict:
        """N2 - {route, target_type, target_id}. Push-tap flavour (in_app=False); the app adds its own in-list nuance."""
        return route_for(obj.notif_type, obj.data, classroom_id=obj.classroom_id, session_id=obj.session_id).as_dict()

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
    # N5 - presets for the pause chips. "1h" | "8h" | "24h" | "tomorrow" (until the quiet-hours end / 07:00 local) |
    # "custom" (+ dnd_custom_until, at most 7 days ahead) | "off". Deadline always from the SERVER clock.
    dnd_preset = serializers.ChoiceField(
        choices=("1h", "8h", "24h", "tomorrow", "custom", "off"), write_only=True, required=False,
    )
    dnd_custom_until = serializers.DateTimeField(write_only=True, required=False)
    # N5 - READ-ONLY: is Focus / exam mode (UserPreference.exam_mode) filtering pushes right now?
    focus_active = serializers.SerializerMethodField()

    class Meta:
        model = NotificationPreference
        fields = [
            "push_enabled", "email_enabled", "sms_enabled", "whatsapp_enabled",
            "muted_types", "digest_frequency", "last_digest_sent_at", "updated_at",
            "quiet_start", "quiet_end", "timezone", "dnd_until", "dnd_for_minutes",
            # N5 smart delivery
            "dnd_preset", "dnd_custom_until", "timezone_mode", "timezone_synced_at",
            "focus_priority_only", "focus_active", "low_priority_rollup", "last_rollup_at",
            # H18 study digest
            "content_digest_frequency", "content_digest_hour", "content_digest_notes", "content_digest_topics",
            "last_content_digest_at",
            # N1 per-type x per-channel matrix
            "channel_overrides", "group_overrides", "reset_channel_overrides", "groups", "locked_types", "type_labels",
            # N8
            "email_verified", "email_verified_at",
            # N9 sound / vibration per category + how long history is kept
            "sound_prefs", "sound_channels", "history_days",
        ]
        read_only_fields = [
            "last_digest_sent_at", "updated_at", "dnd_until", "last_content_digest_at",
            "timezone_synced_at", "last_rollup_at", "email_verified_at",
        ]

    # N8 - READ-ONLY: is the account's CURRENT e-mail one the user proved (code)? Per-event / digest mail needs it.
    email_verified = serializers.SerializerMethodField()

    # N9 - READ: sound_prefs (deviations only), sound_channels [{channel, can_silence}], history_days (30).
    #      WRITE: sound_prefs {channel: {sound: default|gentle|silent|null, vibrate: bool|null} | null} merged into the stored
    #      value (null / defaults remove the entry). calls + notifications (safety) can never be "silent".
    sound_prefs = serializers.JSONField(required=False)
    sound_channels = serializers.SerializerMethodField()
    history_days = serializers.SerializerMethodField()

    def get_sound_channels(self, obj):
        from .notification_center import NEVER_SILENT_CHANNELS, sound_channels

        return [{"channel": c, "can_silence": c not in NEVER_SILENT_CHANNELS} for c in sound_channels()]

    def get_history_days(self, obj):
        from .notification_center import history_days

        return history_days()

    def validate_sound_prefs(self, value):
        from .notification_center import clean_sound_prefs

        current = getattr(self.instance, "sound_prefs", None) if self.instance is not None else None
        try:
            return clean_sound_prefs(value, current)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))

    def get_email_verified(self, obj):
        from .channel_policy import email_verified

        return bool(email_verified(obj.user, obj))

    def get_focus_active(self, obj):
        return bool(obj.focus_active())

    # --- N1 -----------------------------------------------------------------------------------------------
    # READ : channel_overrides (only deviations), groups [{key, types, locked_types}], locked_types [...]
    # WRITE: channel_overrides  {type: {channel: bool|null}}  merged into the stored value (null = back to default)
    #        group_overrides    {group: {channel: bool|null}} expanded to every type of that group (locked types skip
    #                           push / in_app)   -> the easy call for the settings matrix
    #        reset_channel_overrides true          wipes every override
    group_overrides = serializers.DictField(
        child=serializers.DictField(child=serializers.BooleanField(allow_null=True)),
        write_only=True, required=False,
    )
    reset_channel_overrides = serializers.BooleanField(write_only=True, required=False)
    groups = serializers.SerializerMethodField()
    locked_types = serializers.SerializerMethodField()
    type_labels = serializers.SerializerMethodField()

    def get_groups(self, obj):
        locked = Notification.locked_types()
        return [
            {"key": key, "types": types, "locked_types": [t for t in types if t in locked]}
            for key, types in Notification.preference_groups().items()
            if types
        ]

    def get_locked_types(self, obj):
        return sorted(Notification.locked_types())

    def get_type_labels(self, obj):
        """N1b - {notif_type: human label} so the per-type rows of the matrix have a readable name."""
        return {value: str(label) for value, label in Notification.NotifType.choices}

    @staticmethod
    def _clean_channels(channels, notif_type=None):
        """Validate one {channel: bool|null} dict. Locked types: push / in_app = false is dropped (cannot be set)."""
        allowed = NotificationPreference.OVERRIDE_CHANNELS
        locked = Notification.locked_types()
        out = {}
        for channel, value in (channels or {}).items():
            if channel not in allowed:
                raise serializers.ValidationError(f"Unknown channel '{channel}' (use: {', '.join(allowed)}).")
            if value is not None and not isinstance(value, bool):
                raise serializers.ValidationError(f"Channel '{channel}' must be true, false or null.")
            if notif_type in locked and channel in ("push", "in_app") and value is False:
                continue
            out[channel] = value
        return out

    def validate_channel_overrides(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("channel_overrides must be an object {notif_type: {channel: bool}}.")
        valid_types = set(Notification.NotifType.values)
        unknown = [t for t in value if t not in valid_types]
        if unknown:
            raise serializers.ValidationError(f"Unknown notification type(s): {', '.join(unknown[:5])}.")
        return {t: self._clean_channels(ch if isinstance(ch, dict) else {}, t) for t, ch in value.items()}

    def validate_group_overrides(self, value):
        groups = Notification.preference_groups()
        unknown = [g for g in value if g not in groups]
        if unknown:
            raise serializers.ValidationError(f"Unknown group(s): {', '.join(unknown)}.")
        for g, ch in value.items():
            self._clean_channels(ch)  # shape check only; per-type lock filtering happens on expansion
        return value

    @staticmethod
    def _merge_overrides(current, patch):
        """patch: {type: {channel: bool|None}} -> new stored dict (None / all-default entries are removed)."""
        merged = {t: dict(ch) for t, ch in (current or {}).items() if isinstance(ch, dict)}
        for t, channels in patch.items():
            entry = merged.setdefault(t, {})
            for channel, v in channels.items():
                if v is None or v is True:
                    entry.pop(channel, None)  # True == default, only deviations are stored
                else:
                    entry[channel] = False
            if not entry:
                merged.pop(t, None)
        return merged

    #: N1b - channels a legacy `muted_types` entry silenced (the bell row was always kept = in_app stays on).
    LEGACY_MUTE_CHANNELS = ("push", "email", "sms", "whatsapp")
    #: Not a real per-type switch: the Study Digest on/off lives in the content-digest section, never in the matrix.
    LEGACY_MUTE_KEEP = frozenset({Notification.NotifType.CONTENT_DIGEST.value})

    @classmethod
    def _fold_legacy_mutes(cls, muted, merged, patch, reset):
        """N1b - ONE source of truth. A type in the old `muted_types` list means "push / email / sms / whatsapp off, bell
        row stays". The moment the matrix touches a type (or resets), that mute is written out as explicit
        `channel_overrides` entries and removed from `muted_types`, so nothing changes for the user except that the
        matrix can now show and edit it.
        muted  : current muted_types list      merged : channel_overrides AFTER this request's patch was merged
        patch  : {type: {channel: bool|None}}  reset  : wipe everything (content_digest keeps its own switch)
        -> (new_muted, new_merged)"""
        locked = Notification.locked_types()
        if reset:
            return [t for t in muted if t in cls.LEGACY_MUTE_KEEP], merged
        new_muted, merged = [], {t: dict(ch) for t, ch in merged.items()}
        for t in muted:
            if t not in patch or t in locked or t in cls.LEGACY_MUTE_KEEP:
                new_muted.append(t)
                continue
            entry = merged.setdefault(t, {})
            for channel in cls.LEGACY_MUTE_CHANNELS:
                if channel not in patch[t]:  # a channel the request set explicitly keeps the request's value
                    entry[channel] = False
            if not entry:
                merged.pop(t, None)
        return new_muted, merged

    def validate_content_digest_hour(self, value):
        if not 0 <= int(value) <= 23:
            raise serializers.ValidationError("content_digest_hour must be 0-23.")
        return value

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
        # N8 - sms / whatsapp can only be switched ON through a verified phone + a recorded consent
        # (POST core/notification-preferences/channels/consent/). Switching OFF is always allowed. A direct PATCH
        # {"sms_enabled": true} without that is a 400 with a machine-readable code, never a silent opt-in.
        if inst is not None:
            from .channel_policy import enable_blockers

            for ch, flag in (("sms", "sms_enabled"), ("whatsapp", "whatsapp_enabled")):
                if attrs.get(flag) is True and not getattr(inst, flag):
                    blockers = enable_blockers(inst.user, ch)
                    if blockers:
                        raise serializers.ValidationError({flag: {"code": blockers[0], "blockers": blockers,
                                                                 "detail": "Verify your phone and give consent first."}})
        # N1 - fold channel_overrides / group_overrides / reset into one stored value
        reset = attrs.pop("reset_channel_overrides", False)
        group_patch = attrs.pop("group_overrides", None)
        if reset or group_patch is not None or "channel_overrides" in attrs:
            current = {} if reset else dict(getattr(inst, "channel_overrides", None) or {})
            patch = dict(attrs.pop("channel_overrides", {}) or {})
            if group_patch:
                groups = Notification.preference_groups()
                for g, channels in group_patch.items():
                    for t in groups.get(g, []):
                        cleaned = self._clean_channels(channels, t)
                        if cleaned:
                            patch.setdefault(t, {}).update(cleaned)
            merged = self._merge_overrides(current, patch)
            base_muted = list(attrs["muted_types"] if "muted_types" in attrs else (getattr(inst, "muted_types", None) or []))
            new_muted, merged = self._fold_legacy_mutes(base_muted, merged, patch, reset)
            attrs["channel_overrides"] = merged
            if new_muted != base_muted:
                attrs["muted_types"] = new_muted
        # N5 - pause presets (1h / 8h / 24h / till tomorrow / custom / off)
        preset = attrs.pop("dnd_preset", None)
        custom_until = attrs.pop("dnd_custom_until", None)
        if preset is not None:
            if "dnd_for_minutes" in attrs:
                raise serializers.ValidationError({"dnd_preset": "Send dnd_preset OR dnd_for_minutes, not both."})
            from .notification_delivery import dnd_until_for_preset

            try:
                attrs["dnd_until"] = dnd_until_for_preset(
                    inst if inst is not None else NotificationPreference(), preset, custom_until=custom_until,
                )
            except ValueError as exc:
                raise serializers.ValidationError({"dnd_preset": str(exc)})
        elif custom_until is not None:
            raise serializers.ValidationError({"dnd_custom_until": "Use it together with dnd_preset=custom."})
        minutes = attrs.pop("dnd_for_minutes", None)
        if minutes is not None:
            attrs["dnd_until"] = timezone.now() + timedelta(minutes=minutes) if minutes > 0 else None
        return attrs
