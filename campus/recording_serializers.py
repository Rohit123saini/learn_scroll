# campus/recording_serializers.py
"""
Serializers for recorded classes + notes — endpoints in campus/recording_views.py,
rules in campus/recordings.py and campus/models.py (`ClassNote`, `StudentNote`).

Scope fields (`campus`, `section`, and `subject` when a class is given) are
derived server-side and never trusted from the client.
"""
from rest_framework import serializers

from . import recordings
from .models import CampusLiveSession, ClassNote, Section, StudentNote, Subject
from .serializers import MinimalUserSerializer

MAX_NOTE_BODY = 20000
MAX_STUDENT_NOTE = 2000
MAX_NOTE_SECONDS = 24 * 3600


def _name(user):
    if user is None:
        return ""
    full = (f"{user.first_name} {user.last_name}").strip()
    return full or user.username


# ---------------------------------------------------------------------------
# class notes (teacher)
# ---------------------------------------------------------------------------
class ClassNoteSerializer(serializers.ModelSerializer):
    """Create: pass `live_session` (then section + subject come from it) OR `section`
    + `subject`. `section`, `subject` and `live_session` can't be changed afterwards.
    `remove_file=true` on an edit drops the attachment (a note must keep text or a file).
    """

    posted_by_detail = MinimalUserSerializer(source="posted_by", read_only=True)
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    can_manage = serializers.SerializerMethodField()
    remove_file = serializers.BooleanField(write_only=True, required=False, default=False)
    section = serializers.PrimaryKeyRelatedField(queryset=Section.objects.all(), required=False)
    subject = serializers.PrimaryKeyRelatedField(queryset=Subject.objects.all(), required=False)
    live_session = serializers.PrimaryKeyRelatedField(
        queryset=CampusLiveSession.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = ClassNote
        fields = [
            "id", "campus", "section", "subject", "subject_name", "live_session",
            "title", "body", "file", "original_name", "file_size",
            "is_published", "is_pinned", "posted_by", "posted_by_detail", "can_manage",
            "remove_file", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "campus", "posted_by", "original_name", "file_size", "created_at", "updated_at"]

    def get_can_manage(self, obj):
        return bool(getattr(obj, "can_manage_flag", False))

    def validate_title(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Give the note a title.")
        return value

    def validate_body(self, value):
        value = (value or "").strip()
        if len(value) > MAX_NOTE_BODY:
            raise serializers.ValidationError(f"A note can have at most {MAX_NOTE_BODY} characters.")
        return value

    def validate(self, attrs):
        editing = self.instance is not None
        if editing:
            # scope is fixed at creation
            for key in ("section", "subject", "live_session"):
                attrs.pop(key, None)
        else:
            live = attrs.get("live_session")
            if live is None:
                section, subject = attrs.get("section"), attrs.get("subject")
                if section is None or subject is None:
                    raise serializers.ValidationError(
                        {"live_session": "Pick the class this note is for, or give a section and a subject."}
                    )
                if subject.campus_id != section.school_class.campus_id:
                    raise serializers.ValidationError({"subject": "Doesn't belong to this section's campus."})
            else:
                attrs.pop("section", None)
                attrs.pop("subject", None)
        remove = attrs.pop("remove_file", False)
        body = attrs.get("body", self.instance.body if editing else "")
        has_new_file = bool(attrs.get("file"))
        had_file = bool(editing and self.instance.file)
        will_have_file = has_new_file or (had_file and not remove)
        if not body and not will_have_file:
            raise serializers.ValidationError({"body": "Write something or attach a file."})
        if remove and not has_new_file:
            attrs["file"] = ""
        return attrs


# ---------------------------------------------------------------------------
# private student notes
# ---------------------------------------------------------------------------
class StudentNoteSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    subject = serializers.PrimaryKeyRelatedField(queryset=Subject.objects.all(), required=False)
    live_session = serializers.PrimaryKeyRelatedField(
        queryset=CampusLiveSession.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = StudentNote
        fields = [
            "id", "section", "subject", "subject_name", "live_session", "at_seconds", "text",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "section", "created_at", "updated_at"]

    def validate_text(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("A note can't be empty.")
        if len(value) > MAX_STUDENT_NOTE:
            raise serializers.ValidationError(f"A note can have at most {MAX_STUDENT_NOTE} characters.")
        return value

    def validate_at_seconds(self, value):
        if value is not None and value > MAX_NOTE_SECONDS:
            raise serializers.ValidationError("That time is outside the class.")
        return value

    def validate(self, attrs):
        if self.instance is not None:
            # a note stays on the class / subject it was written for; only text + time move
            attrs.pop("subject", None)
            attrs.pop("live_session", None)
        elif attrs.get("live_session") is None and attrs.get("subject") is None:
            raise serializers.ValidationError({"subject": "Pick the class or the subject this note is for."})
        return attrs


# ---------------------------------------------------------------------------
# recordings (a CampusLiveSession seen as "a recorded class")
# ---------------------------------------------------------------------------
class RecordingSerializer(serializers.ModelSerializer):
    """List row. Never carries the playable URL — the player screen opens the detail,
    which hands the URL only to somebody `recordings.can_watch` allows.

    Annotations (see the viewset): `notes_count` (published teacher notes of the
    class) and `my_notes_count` (the caller's own private notes)."""

    section_label = serializers.SerializerMethodField()
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    teacher_name = serializers.SerializerMethodField()
    date = serializers.SerializerMethodField()
    notes_count = serializers.IntegerField(read_only=True, default=0)
    my_notes_count = serializers.IntegerField(read_only=True, default=0)
    can_watch = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()
    recording_error = serializers.SerializerMethodField()

    class Meta:
        model = CampusLiveSession
        fields = [
            "id", "section", "section_label", "subject", "subject_name", "teacher_name", "date",
            "started_at", "ended_at", "recording_status", "recording_hidden",
            "recording_duration_seconds", "recording_error", "notes_count", "my_notes_count",
            "can_watch", "can_manage",
        ]
        read_only_fields = fields

    def get_section_label(self, obj):
        return str(obj.section)

    def get_teacher_name(self, obj):
        return _name(obj.teacher.user) if obj.teacher_id else ""

    def get_date(self, obj):
        day = obj.occurrence_date or (obj.started_at or obj.scheduled_at).date()
        return day.isoformat()

    def get_can_watch(self, obj):
        return obj.recording_status == CampusLiveSession.RecordingStatus.READY and bool(obj.recording_url)

    def get_can_manage(self, obj):
        user = getattr(self.context.get("request"), "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        if obj.teacher_id and obj.teacher.user_id == user.id:
            return True
        if obj.started_by_id and obj.started_by_id == user.id:
            return True
        scope = self.context.get("manage_scope")
        return bool(scope and scope.can(obj.section.school_class.campus_id, obj.section_id, obj.subject_id))

    def get_recording_error(self, obj):
        # a failure code is for the people who run the class, not for students
        return obj.recording_error if self.get_can_manage(obj) else ""


class RecordingDetailSerializer(RecordingSerializer):
    """Detail: + the playable `url` (only when the caller may watch), the teacher's
    notes for this class and the caller's OWN private notes on it."""

    url = serializers.SerializerMethodField()
    class_notes = serializers.SerializerMethodField()
    my_notes = serializers.SerializerMethodField()

    class Meta(RecordingSerializer.Meta):
        fields = RecordingSerializer.Meta.fields + ["url", "class_notes", "my_notes"]
        read_only_fields = fields

    def get_url(self, obj):
        request = self.context.get("request")
        if request is not None and recordings.can_watch(request.user, obj, request):
            return recordings.playback_url(obj)
        return None

    def get_class_notes(self, obj):
        request = self.context.get("request")
        qs = ClassNote.objects.filter(live_session=obj, is_active=True).select_related("posted_by", "subject")
        if not self.get_can_manage(obj):
            qs = qs.filter(is_published=True)
        data = ClassNoteSerializer(qs[:100], many=True, context=self.context).data
        manage = self.get_can_manage(obj)
        for row in data:
            row["can_manage"] = manage
        return data

    def get_my_notes(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return []
        qs = StudentNote.objects.filter(author=user, live_session=obj, is_active=True).select_related("subject")
        return StudentNoteSerializer(qs[:200], many=True, context=self.context).data
