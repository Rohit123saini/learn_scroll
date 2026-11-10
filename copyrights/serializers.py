from rest_framework import serializers

from . import appeals as appeals_svc
from .models import (
    CopyrightAppeal, CopyrightClaim, CopyrightCounterNotice, CopyrightEvidence, CopyrightStanding, CopyrightStrike,
)


class ClaimCreateSerializer(serializers.Serializer):
    target_type = serializers.ChoiceField(choices=CopyrightClaim.TargetType.choices)
    target_id = serializers.CharField(max_length=64)
    claimant_name = serializers.CharField(max_length=120)
    claimant_email = serializers.EmailField()
    organisation = serializers.CharField(max_length=120, required=False, allow_blank=True)
    is_rights_owner = serializers.BooleanField(required=False, default=True)
    work_description = serializers.CharField(max_length=2000, min_length=10)
    original_work_url = serializers.URLField(max_length=500, required=False, allow_blank=True)
    infringement_details = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    good_faith_statement = serializers.BooleanField()
    accuracy_statement = serializers.BooleanField()
    signature = serializers.CharField(max_length=120, min_length=3)


class CounterNoticeCreateSerializer(serializers.Serializer):
    explanation = serializers.CharField(max_length=2000, min_length=10)
    good_faith_statement = serializers.BooleanField()
    jurisdiction_consent = serializers.BooleanField()
    signature = serializers.CharField(max_length=120, min_length=3)


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField()


class EmailCodeSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(max_length=10)


class EvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = CopyrightEvidence
        fields = ["id", "kind", "original_name", "size_bytes", "created_at"]


class TextSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=1000)


class CounterNoticeSerializer(serializers.ModelSerializer):
    class Meta:
        model = CopyrightCounterNotice
        fields = ["status", "filed_at", "restore_after", "court_action_reported_at"]


class ClaimantClaimSerializer(serializers.ModelSerializer):
    """What the rights holder sees of their own claim."""

    counter_notice = serializers.SerializerMethodField()
    content_hidden = serializers.SerializerMethodField()
    evidence = serializers.SerializerMethodField()

    class Meta:
        model = CopyrightClaim
        fields = [
            "id", "target_type", "target_id", "content_snapshot", "work_description", "status",
            "needs_info_message", "decision_note", "created_at", "decided_at", "counter_notice",
            "claimant_tier", "content_hidden", "evidence",
        ]

    def get_content_hidden(self, obj):
        td = getattr(obj, "takedown", None) if hasattr(obj, "takedown") else None
        return bool(td and td.state in ("held", "removed"))

    def get_evidence(self, obj):
        rows = obj.evidence.exclude(kind="content_snapshot")
        return EvidenceSerializer(rows, many=True).data

    def get_counter_notice(self, obj):
        c = getattr(obj, "counter_notice", None) if hasattr(obj, "counter_notice") else None
        return CounterNoticeSerializer(c).data if c else None


class OwnerNoticeSerializer(serializers.ModelSerializer):
    """What the content owner sees. The claimant's contact details are NOT included -
    only the name the rights holder signed with."""

    counter_notice = serializers.SerializerMethodField()
    can_counter = serializers.SerializerMethodField()
    strike_expires_at = serializers.SerializerMethodField()

    class Meta:
        model = CopyrightClaim
        fields = [
            "id", "target_type", "target_id", "content_snapshot", "claimant_name", "organisation",
            "work_description", "status", "created_at", "decided_at", "counter_notice", "can_counter",
            "strike_expires_at",
        ]

    def get_counter_notice(self, obj):
        c = getattr(obj, "counter_notice", None) if hasattr(obj, "counter_notice") else None
        return CounterNoticeSerializer(c).data if c else None

    def get_can_counter(self, obj):
        return obj.status in ("submitted", "under_review", "needs_info", "upheld") and not hasattr(obj, "counter_notice")

    def get_strike_expires_at(self, obj):
        s = getattr(obj, "strike", None) if hasattr(obj, "strike") else None
        return s.expires_at if s and s.is_active else None


class StrikeSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)
    can_appeal = serializers.SerializerMethodField()
    appeal_status = serializers.SerializerMethodField()
    claim_summary = serializers.SerializerMethodField()

    class Meta:
        model = CopyrightStrike
        fields = ["id", "claim", "claim_summary", "issued_at", "expires_at", "is_active", "revoke_reason",
                  "can_appeal", "appeal_status"]

    def get_can_appeal(self, obj):
        return appeals_svc.strike_can_appeal(obj)[0]

    def get_appeal_status(self, obj):
        a = obj.appeals.order_by("-created_at").first()
        return a.status if a else None

    def get_claim_summary(self, obj):
        c = obj.claim
        return {"claimant_name": c.claimant_name, "work_description": c.work_description[:200],
                "content_snapshot": c.content_snapshot}


class StandingSerializer(serializers.ModelSerializer):
    uploads_blocked = serializers.BooleanField(read_only=True)
    terminated = serializers.SerializerMethodField()

    class Meta:
        model = CopyrightStanding
        fields = ["level", "active_strikes", "uploads_blocked", "terminated"]

    def get_terminated(self, obj):
        return obj.level == CopyrightStanding.Level.TERMINATED


class StrikeAppealCreateSerializer(serializers.Serializer):
    message = serializers.CharField(min_length=10, max_length=2000)


class TerminationAppealCreateSerializer(serializers.Serializer):
    identifier = serializers.CharField(max_length=150)  # username or e-mail of the closed account
    contact_email = serializers.EmailField()
    message = serializers.CharField(min_length=10, max_length=2000)


class AppealSerializer(serializers.ModelSerializer):
    class Meta:
        model = CopyrightAppeal
        fields = ["id", "kind", "strike", "message", "status", "decision_note", "created_at", "decided_at"]
