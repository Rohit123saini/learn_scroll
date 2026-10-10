# campus/audit.py
"""
[T4 §B] Audit trail. `log_action()` writes one append-only `CampusAuditLog`
row. It NEVER raises: an audit failure must not break the admin action that
triggered it (it is logged instead).

`AuditedModelViewSetMixin` hooks create/update/destroy of the structural
ViewSets so every admin action is recorded without touching each view body.
"""
import logging

from .models import CampusAuditLog

logger = logging.getLogger(__name__)


def log_action(campus_id, actor, action, *, target=None, target_type="", target_id="", summary="", **metadata):
    try:
        if target is not None:
            target_type = target_type or target.__class__.__name__
            target_id = target_id or str(getattr(target, "pk", ""))
        CampusAuditLog.objects.create(
            campus_id=campus_id,
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            action=action[:60],
            target_type=target_type[:40],
            target_id=str(target_id)[:64],
            summary=(summary or "")[:300],
            metadata={k: (str(v) if not isinstance(v, (int, float, bool, list, dict, type(None), str)) else v) for k, v in metadata.items()},
        )
    except Exception:  # pragma: no cover - audit must never break the request
        logger.exception("Failed to write campus audit log (%s)", action)


def _campus_id_of(instance):
    """Resolve the campus id of any campus-scoped row (direct FK or through
    section / school_class)."""
    for path in ("campus_id", "school_class.campus_id", "section.school_class.campus_id"):
        obj = instance
        try:
            for part in path.split("."):
                obj = getattr(obj, part)
            if obj:
                return obj
        except AttributeError:
            continue
    return None


class AuditedModelViewSetMixin:
    """Put BEFORE the ModelViewSet in the MRO. `audit_prefix` e.g. "section"."""

    audit_prefix = ""

    def _audit(self, verb, instance, **extra):
        campus_id = _campus_id_of(instance)
        if campus_id and self.audit_prefix:
            log_action(
                campus_id, self.request.user, f"{self.audit_prefix}.{verb}",
                target=instance, summary=f"{verb} {self.audit_prefix}: {instance}"[:300], **extra,
            )

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._audit("create", serializer.instance)

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self._audit("update", serializer.instance, fields=sorted(serializer.validated_data.keys()))

    def perform_destroy(self, instance):
        campus_id = _campus_id_of(instance)
        label = str(instance)
        pk = instance.pk
        super().perform_destroy(instance)
        if campus_id and self.audit_prefix:
            log_action(
                campus_id, self.request.user, f"{self.audit_prefix}.delete",
                target_type=instance.__class__.__name__, target_id=pk, summary=f"delete {self.audit_prefix}: {label}"[:300],
            )
