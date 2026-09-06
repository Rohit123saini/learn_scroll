# message/models_focus.py
#
# 🔥 NAYA — Feature 12 (Smart DND during focus/exam windows) ke models.
#
# MERGE INSTRUCTIONS: is file ka pura content apne `message/models.py`
# ke END me paste kar do (ya `models.py` me `from .models_focus import *`
# add kar do agar alag file rakhni hai — Django isse allow karta hai jab
# tak `apps.py` ka default `models` module in classes ko discover kar le,
# isliye simplest hai seedha models.py me paste karna).
#
# Phir:
#   python manage.py makemigrations message
#   python manage.py migrate
#
# ---------------------------------------------------------------------
# Feature 11 (Announcements) ke liye Message model me sirf EK field add
# karna hai — models.py me apne existing `Message` class ke andar:
#
#   is_announcement = models.BooleanField(default=False, db_index=True)
#
# Ye field VIEWS.PY me set hoga (message create ke waqt), model me sirf
# storage hai. is_index=True isliye taaki "sirf announcements dikhao"
# query fast rahe (conversations list / global filter).
# ---------------------------------------------------------------------

from django.db import models
from django.conf import settings
from .models import BaseModel  # existing abstract base (id/created_at/updated_at)


class FocusSession(BaseModel):
    """
    Ek active "focus window" — student ne khud set kiya hai ki agle N
    minutes/hours sirf teacher/staff ke pings aayenge, baaki sab mute.

    Ek time pe user ka sirf EK active session hota hai — naya start
    purane ko replace kar deta hai (extend/overwrite), stack nahi hote.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="focus_sessions",
    )
    starts_at = models.DateTimeField(auto_now_add=True)
    ends_at = models.DateTimeField(db_index=True)

    # 🔥 Exception rule — kaun ke messages phir bhi through aayenge.
    # 'teachers_only' (default) = sirf un groups ke admin/moderator jinme
    # user member hai. 'nobody' = poora silence, koi bhi exception nahi
    # (hard focus mode, exam jaisa). Future-proof rakha hai enum se taaki
    # aage "specific people" jaisa option bhi add ho sake bina migration ke.
    class ExceptionRule(models.TextChoices):
        TEACHERS_ONLY = "teachers_only", "Only teachers/staff"
        NOBODY = "nobody", "Nobody — full silence"

    exception_rule = models.CharField(
        max_length=20,
        choices=ExceptionRule.choices,
        default=ExceptionRule.TEACHERS_ONLY,
    )

    # user ne khud jab band kiya (auto-expire se pehle) — analytics/UI ke
    # liye useful ("cancelled early" vs "ran full duration").
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-starts_at"]
        indexes = [
            models.Index(fields=["user", "ends_at"]),
        ]

    def __str__(self):
        return f"FocusSession(user={self.user_id}, ends_at={self.ends_at}, rule={self.exception_rule})"

    @property
    def is_active(self):
        from django.utils import timezone
        return self.cancelled_at is None and self.ends_at > timezone.now()

    @classmethod
    def get_active_for_user(cls, user_id):
        """Returns the active FocusSession for a user, or None."""
        from django.utils import timezone
        return (
            cls.objects.filter(
                user_id=user_id,
                ends_at__gt=timezone.now(),
                cancelled_at__isnull=True,
            )
            .order_by("-starts_at")
            .first()
        )

    @classmethod
    def start_for_user(cls, user, duration_minutes, exception_rule=ExceptionRule.TEACHERS_ONLY):
        """
        Race-safe-ish start: purana active session (agar hai) cancel kar
        ke naya bana do — ek user ka ek hi session zinda rehta hai.
        """
        from django.utils import timezone
        cls.objects.filter(
            user=user, ends_at__gt=timezone.now(), cancelled_at__isnull=True
        ).update(cancelled_at=timezone.now())
        return cls.objects.create(
            user=user,
            ends_at=timezone.now() + timezone.timedelta(minutes=duration_minutes),
            exception_rule=exception_rule,
        )