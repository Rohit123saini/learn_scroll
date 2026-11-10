import re

from rest_framework import serializers

from user_profile.serializers import SafeProfilePhotoField

from .models import BugReport, FeatureRequest, SupportMessage, SupportTicket


def _clean(value: str) -> str:
    return (value or "").strip()


# S11b - the app already scrubs diagnostics; this is the second lock on the server so a modified client cannot
# smuggle e-mails / phone numbers / tokens into the staff-visible report.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_JWT_RE = re.compile(r"eyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}")
_BEARER_RE = re.compile(r"(?i)\b(bearer|token|authorization|password|passwd|secret)\b\s*[:=]?\s*\S+")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s-]{8,}\d)(?!\d)")
_URL_QUERY_RE = re.compile(r"(https?://[^\s?#]+)[?#]\S*")
DIAGNOSTICS_MAX = 8000


def scrub_diagnostics(text: str) -> str:
    t = (text or "").replace("\x00", "")
    t = _JWT_RE.sub("[jwt]", t)
    t = _BEARER_RE.sub(lambda m: f"{m.group(1)}: [hidden]", t)
    t = _EMAIL_RE.sub("[email]", t)
    t = _URL_QUERY_RE.sub(r"\1?[query]", t)
    t = _PHONE_RE.sub("[number]", t)
    return t[:DIAGNOSTICS_MAX]


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
    # Longer text is trimmed, not rejected - a report must never fail only because the log was long.
    diagnostics = serializers.CharField(required=False, allow_blank=True, default="", trim_whitespace=False)

    class Meta:
        model = BugReport
        fields = ["title", "description", "screen", "app_version", "platform", "device_info", "screenshot", "diagnostics"]
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

    def validate_diagnostics(self, v):
        return scrub_diagnostics(v)


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


# ------------------------------------------------------------ FAQ feedback
class FaqFeedbackSerializer(serializers.Serializer):
    faq_id = serializers.RegexField(r"^[a-z0-9_]{1,60}$", max_length=60)
    helpful = serializers.BooleanField()
    lang = serializers.CharField(max_length=8, required=False, allow_blank=True, default="")

    def validate_lang(self, v):
        v = _clean(v).lower().replace("_", "-")[:8]
        return v if re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,4})?", v or "") else ""
