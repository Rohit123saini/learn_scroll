# campus/soft_delete.py
"""
SOFT-DELETE for campus records (see STATUS_SOFT_DELETE_AUDIT.md).

Rule: an admin "delete" in the campus app NEVER erases a row any more. It hides it
(`is_deleted=True`, who/when/batch recorded), writes an audit row, and can be undone from
the trash (`GET <resource>/trash/?campus=`, `POST <resource>/<id>/restore/`). Real erasure
only happens through `manage.py purge_soft_deleted` (retention window, dry-run by default).

How it behaves
- `Model.objects`      -> alive rows only (what every existing view / task / serializer sees).
- `Model.all_objects`  -> everything, incl. trash (admin, restore, purge, audits).
- Forward FKs (`section.school_class`) and Django's cascade collector use the *base* manager, so a
  record that points at a deleted parent still resolves it (history never breaks).
- Reverse managers (`section.enrollments`) use the default manager -> deleted children are hidden.
- Soft-deleting a parent also soft-deletes its alive soft-deletable CASCADE children in the same
  `delete_batch`; restoring one row restores exactly that batch (never a child the admin deleted
  separately, earlier).
- Unique constraints on these models are conditional on `is_deleted=False`, so a name can be
  re-used after the old one went to the trash.

This module must stay import-light (models.py imports it): no imports from campus.models here.
"""
import uuid
from collections import Counter

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


class SoftDeleteError(Exception):
    """Raised for a refused soft-delete / restore. `code` is machine readable, `status` an HTTP hint."""

    def __init__(self, message, code="soft_delete_refused", status=409):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(is_deleted=False)

    def dead(self):
        return self.filter(is_deleted=True)

    def soft_delete_all(self, user=None, cascade=True):
        """Soft-delete every alive row of this queryset; returns how many were hidden.

        Use this instead of `qs.delete()` (a queryset `.delete()` is a REAL bulk delete and skips the trash,
        the audit batch and the safety checks). Each row gets its own `delete_batch`, so each one can be
        restored separately. Raises SoftDeleteError for the first row the model refuses; wrap the call in
        `transaction.atomic()` when it must be all-or-nothing.
        """
        n = 0
        for obj in list(self.filter(is_deleted=False)):
            obj.soft_delete(user=user, cascade=cascade)
            n += 1
        return n


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Default manager: alive rows only."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class SoftDeleteModel(models.Model):
    """Abstract mixin. Put it AFTER CampusBaseModel: `class Section(CampusBaseModel, SoftDeleteModel)`."""

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    # No index on purpose: adding an index to a big live table is the risky part of a migration,
    # and the trash is always read together with a campus filter.
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", db_index=False,
    )
    # Rows hidden by one admin action (the row itself + its cascaded children) share one batch id.
    delete_batch = models.UUIDField(null=True, blank=True)

    objects = SoftDeleteManager()      # first declared == default manager
    all_objects = models.Manager()

    class Meta:
        abstract = True

    # ---- hooks models may override -------------------------------------------------------------
    def soft_delete_check(self):
        """Raise SoftDeleteError to refuse deleting THIS row (cascaded children are not asked)."""

    def restore_check(self):
        """Raise SoftDeleteError if restoring this row now would break an invariant."""

    # ---- internals -----------------------------------------------------------------------------
    @classmethod
    def _soft_children(cls_or_obj):
        """(related model, fk name) for every reverse CASCADE FK whose model is soft-deletable."""
        out = []
        for rel in cls_or_obj._meta.related_objects:
            if not (rel.one_to_many or rel.one_to_one):
                continue
            related = rel.related_model
            if issubclass(related, SoftDeleteModel) and rel.on_delete is models.CASCADE:
                out.append((related, rel.field.name))
        return out

    def _soft_parents(self):
        """Forward FK values pointing at soft-deletable rows (read through the base manager)."""
        out = []
        for f in self._meta.concrete_fields:
            if f.is_relation and f.many_to_one and f.related_model and issubclass(f.related_model, SoftDeleteModel):
                parent = getattr(self, f.name, None)
                if parent is not None:
                    out.append(parent)
        return out

    def _mark_deleted(self, user, when, batch):
        n = type(self).all_objects.filter(pk=self.pk, is_deleted=False).update(
            is_deleted=True, deleted_at=when, deleted_by=user, delete_batch=batch,
        )
        self.is_deleted, self.deleted_at, self.deleted_by, self.delete_batch = True, when, user, batch
        return n

    def _cascade_delete(self, user, when, batch, counter, seen):
        for related, fk in self._soft_children():
            for child in related.all_objects.filter(**{fk: self.pk, "is_deleted": False}):
                key = (related, child.pk)
                if key in seen:
                    continue
                seen.add(key)
                if child._mark_deleted(user, when, batch):
                    counter[related.__name__] += 1
                child._cascade_delete(user, when, batch, counter, seen)

    def _cascade_restore(self, batch, counter, seen):
        for related, fk in self._soft_children():
            for child in related.all_objects.filter(**{fk: self.pk, "is_deleted": True, "delete_batch": batch}):
                key = (related, child.pk)
                if key in seen:
                    continue
                seen.add(key)
                type(child).all_objects.filter(pk=child.pk).update(
                    is_deleted=False, deleted_at=None, deleted_by=None, delete_batch=None,
                )
                counter[related.__name__] += 1
                child._cascade_restore(batch, counter, seen)

    # ---- public API ----------------------------------------------------------------------------
    def soft_delete(self, user=None, cascade=True):
        """Hide this row (+ alive soft-deletable cascade children). Returns (batch_id, Counter)."""
        if self.is_deleted:
            raise SoftDeleteError("This record is already in the trash.", code="already_deleted", status=409)
        self.soft_delete_check()
        user = user if getattr(user, "is_authenticated", False) else None
        batch, when, counter = uuid.uuid4(), timezone.now(), Counter()
        with transaction.atomic():
            self._mark_deleted(user, when, batch)
            if cascade:
                self._cascade_delete(user, when, batch, counter, {(type(self), self.pk)})
        return batch, counter

    def restore(self, user=None, cascade=True):
        """Bring this row back (+ the children hidden by the same action). Returns Counter."""
        if not self.is_deleted:
            raise SoftDeleteError("This record is not in the trash.", code="not_deleted", status=409)
        for parent in self._soft_parents():
            if getattr(parent, "is_deleted", False):
                raise SoftDeleteError(
                    f"Restore its parent first: {parent}.", code="parent_deleted", status=409,
                )
        self.restore_check()
        batch, counter = self.delete_batch, Counter()
        from django.db import IntegrityError

        try:
            with transaction.atomic():
                type(self).all_objects.filter(pk=self.pk).update(
                    is_deleted=False, deleted_at=None, deleted_by=None, delete_batch=None,
                )
                self.is_deleted, self.deleted_at, self.deleted_by, self.delete_batch = False, None, None, None
                if cascade and batch:
                    self._cascade_restore(batch, counter, {(type(self), self.pk)})
        except IntegrityError:
            raise SoftDeleteError(
                "Cannot restore: a newer record with the same unique details already exists.",
                code="restore_conflict", status=409,
            )
        return counter
