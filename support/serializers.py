from rest_framework import serializers

from user_profile.serializers import SafeProfilePhotoField

from .models import BugReport, FeatureRequest, SupportMessage, SupportTicket


def _clean(value: str) -> str:
    return (value or "").strip()


# ---------------------------------------------------------------- tickets
class SupportMessageSerializer(serializers.ModelSerializer):
    # Staff are shown as a team, never by personal account.
    sender_label = serializers.SerializerMethodField()

    class Meta:
        model = SupportMessage
        fields = ["id", "body", "is_staff", "sender_label", "created_at"]

    def get_sender_label(self, obj):
        return "LearnScroll Support" if obj.is_staff else "You"


class TicketListSerializer(serializers.ModelSerializer):
    last_message_preview = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = ["id", "subject", "category", "status", "has_unread_reply",
                  "created_at", "last_message_at", "last_message_preview"]

    def get_last_message_preview(self, obj):
        last = getattr(obj, "_last_body", None)
        if last is None:
            m = obj.messages.order_by("-created_at").first()
            last = m.body if m else ""
        return last[:100]


class TicketDetailSerializer(TicketListSerializer):
    messages = SupportMessageSerializer(many=True, read_only=True)

    class Meta(TicketListSerializer.Meta):
        fields = TicketListSerializer.Meta.fields + ["messages"]


class TicketCreateSerializer(serializers.Serializer):
    subject = serializers.CharField(max_length=120)
    category = serializers.ChoiceField(choices=SupportTicket.Category.choices, default=SupportTicket.Category.OTHER)
    message = serializers.CharField(max_length=2000)

    def validate_subject(self, v):
        v = _clean(v)
        if len(v) < 3:
            raise serializers.ValidationError("Please add a short subject (at least 3 characters).")
        return v

    def validate_message(self, v):
        v = _clean(v)
        if len(v) < 10:
            raise serializers.ValidationError("Please describe the problem (at least 10 characters).")
        return v


class MessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=2000)

    def validate_body(self, v):
        v = _clean(v)
        if not v:
            raise serializers.ValidationError("Message can't be empty.")
        return v


# ------------------------------------------------------------ bug reports
class BugReportCreateSerializer(serializers.ModelSerializer):
    # Same size / extension / real-format / pixel checks as profile photos.
    screenshot = SafeProfilePhotoField(required=False, allow_null=True)

    class Meta:
        model = BugReport
        fields = ["title", "description", "screen", "app_version", "platform", "device_info", "screenshot"]
        extra_kwargs = {
            "screen": {"required": False},
            "app_version": {"required": False},
            "platform": {"required": False},
            "device_info": {"required": False},
        }

    def validate_title(self, v):
        v = _clean(v)
        if len(v) < 5:
            raise serializers.ValidationError("Please add a short title (at least 5 characters).")
        return v

    def validate_description(self, v):
        v = _clean(v)
        if len(v) < 10:
            raise serializers.ValidationError("Please tell us what went wrong (at least 10 characters).")
        return v


class BugReportSerializer(serializers.ModelSerializer):
    screenshot = serializers.SerializerMethodField()

    class Meta:
        model = BugReport
        fields = ["id", "title", "status", "screenshot", "created_at"]

    def get_screenshot(self, obj):
        if not obj.screenshot:
            return None
        request = self.context.get("request")
        url = obj.screenshot.url
        return request.build_absolute_uri(url) if request else url


# ------------------------------------------------------- feature requests
class FeatureRequestSerializer(serializers.ModelSerializer):
    has_voted = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = FeatureRequest
        fields = ["id", "title", "description", "status", "votes_count", "has_voted",
                  "is_mine", "author_name", "created_at"]

    def get_has_voted(self, obj):
        return bool(getattr(obj, "has_voted", False))

    def get_is_mine(self, obj):
        user = self.context["request"].user
        return obj.author_id == user.id

    def get_author_name(self, obj):
        a = obj.author
        if a is None:
            return ""
        return (a.first_name or a.username or "").strip()


class FeatureRequestCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=120)
    description = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")

    def validate_title(self, v):
        v = _clean(v)
        if len(v) < 5:
            raise serializers.ValidationError("Please describe the feature in a few words (at least 5 characters).")
        return v

    def validate_description(self, v):
        return _clean(v)
