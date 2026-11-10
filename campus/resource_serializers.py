# campus/resource_serializers.py
"""
Serializers for class resources (content share) — see campus/resources.py for
the endpoints and campus/models.py (`ResourceFolder`, `ClassResource`) for the
data rules.

Everything scope-like (`campus`, `session`, and for a resource also `section`
and `subject`) is derived server-side from the section / folder and is
read-only here — a client can never aim a resource at another section by
editing ids.
"""
from django.conf import settings
from rest_framework import serializers

from . import resource_rules
from .models import ClassResource, ResourceFolder
from .serializers import MinimalUserSerializer

# Abuse guard, not a product limit: nobody browses 300 files in one folder anyway.
MAX_RESOURCES_PER_FOLDER = getattr(settings, "CAMPUS_RESOURCE_MAX_PER_FOLDER", 300)
MAX_FOLDERS_PER_SUBJECT = getattr(settings, "CAMPUS_RESOURCE_MAX_FOLDERS", 100)


class ResourceFolderSerializer(serializers.ModelSerializer):
    """`section` + `subject` are required for a ROOT folder and ignored for a
    sub-folder (it inherits both from `parent`). `section`, `subject` and
    `parent` can't be changed afterwards — to "move" a folder, create it again.
    Counts (`resource_count`, `total_resource_count`, `child_count`) come from
    queryset annotations; they only count PUBLISHED resources.
    """

    section_label = serializers.SerializerMethodField()
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    created_by_detail = MinimalUserSerializer(source="created_by", read_only=True)
    resource_count = serializers.IntegerField(read_only=True, default=0)
    total_resource_count = serializers.IntegerField(read_only=True, default=0)
    child_count = serializers.IntegerField(read_only=True, default=0)
    can_manage = serializers.SerializerMethodField()

    class Meta:
        model = ResourceFolder
        fields = [
            "id", "campus", "session", "section", "section_label", "subject", "subject_name",
            "parent", "name", "order", "created_by", "created_by_detail", "is_active",
            "resource_count", "total_resource_count", "child_count", "can_manage",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "campus", "session", "created_by", "is_active", "created_at", "updated_at"]
        extra_kwargs = {
            "section": {"required": False},
            "subject": {"required": False},
            "parent": {"required": False, "allow_null": True},
            "order": {"required": False},
        }
        # Name uniqueness (among ACTIVE siblings) is checked in validate() — the
        # constraints are conditional, so DRF's auto validators would misfire.
        validators = []

    def get_section_label(self, obj):
        section = obj.section
        return f"{section.school_class.name} - {section.name}"

    def get_can_manage(self, obj):
        return bool(getattr(obj, "can_manage_flag", False))

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Folder name can't be empty.")
        return value

    def validate(self, attrs):
        instance = self.instance

        if instance is not None:
            for key in ("section", "subject", "parent"):
                if key in attrs:
                    new = attrs[key]
                    old_id = getattr(instance, f"{key}_id")
                    if (new.pk if new is not None else None) != old_id:
                        raise serializers.ValidationError(
                            {key: "This can't be changed — create a new folder instead."}
                        )
            section, subject, parent = instance.section, instance.subject, instance.parent
        else:
            parent = attrs.get("parent")
            if parent is not None:
                if not parent.is_active:
                    raise serializers.ValidationError({"parent": "That folder was removed."})
                if parent.parent_id:
                    raise serializers.ValidationError({"parent": "Folders can be nested only one level deep."})
                section, subject = parent.section, parent.subject
                for key, inherited in (("section", section), ("subject", subject)):
                    given = attrs.get(key)
                    if given is not None and given.pk != inherited.pk:
                        raise serializers.ValidationError(
                            {key: "A sub-folder always uses its parent folder's section and subject."}
                        )
                attrs["section"], attrs["subject"] = section, subject
            else:
                section, subject = attrs.get("section"), attrs.get("subject")
                if section is None or subject is None:
                    raise serializers.ValidationError(
                        {"section": "Section and subject are required for a folder."}
                        if section is None else {"subject": "Section and subject are required for a folder."}
                    )
            if subject.campus_id != section.school_class.campus_id:
                raise serializers.ValidationError({"subject": "This subject doesn't belong to the section's campus."})

        name = attrs.get("name", getattr(instance, "name", None))
        if name is not None:
            siblings = ResourceFolder.objects.filter(is_active=True, name__iexact=name)
            if parent is not None:
                siblings = siblings.filter(parent=parent)
            else:
                siblings = siblings.filter(parent__isnull=True, section=section, subject=subject)
            if instance is not None:
                siblings = siblings.exclude(pk=instance.pk)
            if siblings.exists():
                raise serializers.ValidationError({"name": "A folder with this name already exists here."})

        if instance is None and parent is None:
            existing = ResourceFolder.objects.filter(
                is_active=True, section=section, subject=subject, parent__isnull=True
            ).count()
            if existing >= MAX_FOLDERS_PER_SUBJECT:
                raise serializers.ValidationError({"name": "Too many folders for this subject."})
        return attrs


class ClassResourceSerializer(serializers.ModelSerializer):
    """Create: `folder`, `title` and EXACTLY ONE of `file` / `external_url`.
    `kind` is optional — it is guessed from the file extension, or `link` for a
    URL with no kind given (send `kind=video` for a YouTube link).

    Edit (PATCH): send only what changes. Sending a new `file` replaces the
    upload (and clears any link), sending a non-empty `external_url` replaces
    the source with that link (and drops the stored file). `folder` may move the
    resource to another folder of the SAME section + subject.
    `remove_thumbnail=true` drops the thumbnail without uploading a new one.

    Annotations (see the viewset): `can_manage`, `viewed_by_me`, and
    `opened_by_count` which is only filled for people who can manage it.
    """

    subject_name = serializers.CharField(source="subject.name", read_only=True)
    folder_name = serializers.CharField(source="folder.name", read_only=True)
    folder_parent = serializers.UUIDField(source="folder.parent_id", read_only=True, allow_null=True)
    posted_by_detail = MinimalUserSerializer(source="posted_by", read_only=True)
    can_manage = serializers.SerializerMethodField()
    viewed_by_me = serializers.SerializerMethodField()
    opened_by_count = serializers.SerializerMethodField()
    remove_thumbnail = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = ClassResource
        fields = [
            "id", "campus", "session", "section", "subject", "subject_name",
            "folder", "folder_name", "folder_parent", "posted_by", "posted_by_detail",
            "kind", "title", "description", "file", "external_url", "thumbnail",
            "original_name", "file_size", "duration_seconds",
            "is_published", "is_pinned", "order",
            "can_manage", "viewed_by_me", "opened_by_count", "remove_thumbnail",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "campus", "session", "section", "subject", "posted_by",
            "original_name", "file_size", "created_at", "updated_at",
        ]
        extra_kwargs = {
            "kind": {"required": False},
            "description": {"required": False, "allow_blank": True},
            "file": {"required": False},
            "external_url": {"required": False, "allow_blank": True},
            "thumbnail": {"required": False},
            "duration_seconds": {"required": False, "allow_null": True},
        }

    # ---- computed (annotations, with safe defaults) ---------------------------
    def get_can_manage(self, obj):
        return bool(getattr(obj, "can_manage_flag", False))

    def get_viewed_by_me(self, obj):
        return bool(getattr(obj, "viewed_by_me_flag", False))

    def get_opened_by_count(self, obj):
        if not getattr(obj, "can_manage_flag", False):
            return None  # students / parents never see how many others opened it
        return getattr(obj, "opened_by_count_ann", 0)

    # ---- validation ---------------------------------------------------------------
    def validate_title(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Title can't be empty.")
        return value

    def validate_folder(self, folder):
        if not folder.is_active or (folder.parent_id and not folder.parent.is_active):
            raise serializers.ValidationError("That folder was removed.")
        return folder

    def validate(self, attrs):
        instance = self.instance
        folder = attrs.get("folder", getattr(instance, "folder", None))

        if instance is not None and "folder" in attrs and attrs["folder"].pk != instance.folder_id:
            old = instance.folder
            if folder.section_id != old.section_id or folder.subject_id != old.subject_id:
                raise serializers.ValidationError(
                    {"folder": "A resource can only move to a folder of the same section and subject."}
                )

        # ---- the resulting source: exactly one of file / link ----
        new_file = attrs.get("file")
        new_url = (attrs.get("external_url") or "").strip() if "external_url" in attrs else None
        if "external_url" in attrs:
            attrs["external_url"] = new_url

        if instance is None:
            has_file, has_url = bool(new_file), bool(new_url)
        elif new_file:
            has_file, has_url = True, False
            attrs["external_url"] = ""
        elif new_url:
            has_file, has_url = False, True
            attrs["file"] = ""  # drop the stored upload; update() deletes it from storage
        else:
            has_file, has_url = bool(instance.file), bool(instance.external_url)
        if has_file == has_url:
            raise serializers.ValidationError("Add either a file or a link — exactly one.")

        # ---- kind ----
        kind = attrs.get("kind", getattr(instance, "kind", None))
        if new_file:
            guessed = resource_rules.kind_for_filename(new_file.name)
            if guessed is None:
                raise serializers.ValidationError({"file": "This file type isn't allowed."})
            if kind is None or ("kind" not in attrs and instance is not None):
                kind = guessed
            if kind != guessed:
                raise serializers.ValidationError({"kind": f"This file is a {guessed}, not a {kind}."})
        elif kind is None:
            kind = ClassResource.Kind.LINK
        attrs["kind"] = kind

        if kind == ClassResource.Kind.LINK and not has_url:
            raise serializers.ValidationError({"external_url": "A link resource needs a URL."})

        # ---- per-folder cap (create only) ----
        if instance is None and folder is not None:
            count = ClassResource.objects.filter(folder=folder, is_active=True).count()
            if count >= MAX_RESOURCES_PER_FOLDER:
                raise serializers.ValidationError({"folder": "This folder is full."})
        return attrs

    # ---- writes ---------------------------------------------------------------------
    def create(self, validated_data):
        validated_data.pop("remove_thumbnail", None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        remove_thumb = validated_data.pop("remove_thumbnail", False)
        old_file = instance.file.name if instance.file else None
        old_thumb = instance.thumbnail.name if instance.thumbnail else None
        file_storage = instance.file.storage
        thumb_storage = instance.thumbnail.storage
        thumb_replaced = bool(validated_data.get("thumbnail"))
        if remove_thumb and not thumb_replaced:
            validated_data["thumbnail"] = ""

        instance = super().update(instance, validated_data)

        new_file = instance.file.name if instance.file else None
        new_thumb = instance.thumbnail.name if instance.thumbnail else None
        for old, new, storage in ((old_file, new_file, file_storage), (old_thumb, new_thumb, thumb_storage)):
            if old and old != new:
                try:  # best-effort: an orphaned file must never fail the edit
                    storage.delete(old)
                except Exception:  # pragma: no cover
                    pass
        return instance
