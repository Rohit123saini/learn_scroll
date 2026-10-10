"""
core/onboarding_quickstart.py

The 30-second first-run step: pick your class, your exam (optional) and up
to 3 interests, in ONE request, and get the first personalised suggestions
back in the same response.

    GET  /core/onboarding/options/      choices for the pickers (labels in English;
                                        the app localises its own copy)
    GET  /core/onboarding/quick-start/  what the user has saved so far
    POST /core/onboarding/quick-start/  {"study_class": "class_11",
                                         "target_exam": "jee",        # optional
                                         "interests": ["education", "tech", "sports"]}  # 1..3

POST is atomic and idempotent (re-running replaces the saved values, so
"change these anytime" is the same call). It:
  1. stores study_class / target_exam on the user,
  2. replaces the user's UserInterest rows (same effect as PUT /post/interests/,
     including dropping the cached feed candidates so the NEXT home-feed
     request is already personalised),
  3. returns the suggested people / campuses / sample tests, with sample
     tests preferring the chosen exam.

It does NOT mark onboarding completed — that stays with the explicit
Finish/Skip in OnboardingCompleteView, so a user who quits after this step
still sees the rest of the flow next time.
"""

from django.db import transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

MAX_INTERESTS = 3

STUDY_CLASSES = [
    ("class_6_8", "Class 6-8"),
    ("class_9", "Class 9"),
    ("class_10", "Class 10"),
    ("class_11", "Class 11"),
    ("class_12", "Class 12"),
    ("dropper", "Dropper / Gap year"),
    ("undergrad", "College (UG)"),
    ("postgrad", "College (PG)"),
    ("working", "Working professional"),
    ("other", "Other"),
]

# value, label, keywords used to match TestSeries.subject / title
TARGET_EXAMS = [
    ("jee", "JEE", ("jee", "iit")),
    ("neet", "NEET", ("neet", "medical")),
    ("boards", "Board exams", ("board", "cbse", "icse", "ncert")),
    ("cuet", "CUET", ("cuet",)),
    ("upsc", "UPSC", ("upsc", "ias", "civil services")),
    ("ssc", "SSC", ("ssc",)),
    ("banking", "Banking", ("bank", "ibps", "sbi")),
    ("gate", "GATE", ("gate",)),
    ("cat", "CAT / MBA", ("cat", "mba")),
    ("none", "No exam right now", ()),
]

_CLASS_KEYS = [k for k, _ in STUDY_CLASSES]
_EXAM_KEYS = [k for k, _, _ in TARGET_EXAMS]
_EXAM_KEYWORDS = {k: kw for k, _, kw in TARGET_EXAMS}


class QuickStartSerializer(serializers.Serializer):
    study_class = serializers.ChoiceField(choices=_CLASS_KEYS)
    target_exam = serializers.ChoiceField(choices=_EXAM_KEYS, required=False, allow_blank=True, default="")
    interests = serializers.ListField(
        child=serializers.CharField(), min_length=1, max_length=MAX_INTERESTS,
        error_messages={
            "min_length": "Pick at least one interest.",
            "max_length": f"Pick at most {MAX_INTERESTS} interests.",
        },
    )

    def validate_interests(self, value):
        from post.models import Post

        valid = {k for k, _ in Post.CATEGORY_CHOICES}
        seen = []
        for c in value:
            if c not in valid:
                raise serializers.ValidationError(f"'{c}' is not a valid interest.")
            if c not in seen:  # de-dupe, keep order
                seen.append(c)
        return seen


# Quick-start picker values -> UserPreference study-profile values (the feed's
# Exam Mode / exam personalisation reads UserPreference, see post/feed_exam.py).
# Values with no clean equivalent map to None and are left untouched.
_PREF_CLASS = {
    "class_9": "9", "class_10": "10", "class_11": "11", "class_12": "12",
    "dropper": "dropper", "undergrad": "graduate", "postgrad": "graduate",
    "working": "other", "other": "other",
}
_PREF_EXAM = {
    "jee": "jee", "neet": "neet", "boards": "board", "upsc": "upsc",
    "cuet": "other", "ssc": "other", "banking": "other", "gate": "other", "cat": "other",
    "none": "",
}


def sync_study_preference(user, study_class: str, target_exam: str) -> None:
    """Mirror the quick-start class/exam into UserPreference so the very first
    feed is already exam-aware. Best-effort: never blocks onboarding, never
    touches exam_mode / focus_subjects / exam_date (those stay the user's own)."""
    try:
        from user_profile.models import UserPreference

        pref = UserPreference.for_user(user)
        fields = []
        level = _PREF_CLASS.get(study_class)
        if level is not None and pref.class_level != level:
            pref.class_level = level
            fields.append("class_level")
        exam = _PREF_EXAM.get(target_exam or "none")
        if exam is not None and pref.exam_target != exam:
            pref.exam_target = exam
            fields.append("exam_target")
        if fields:
            pref.save(update_fields=fields)
    except Exception:  # pragma: no cover - onboarding must not fail on this
        import logging

        logging.getLogger(__name__).warning("quick-start study-profile sync failed", exc_info=True)


def exam_keywords(exam: str):
    return _EXAM_KEYWORDS.get(exam, ())


def prefer_exam(series_qs, exam: str):
    """Order a TestSeries queryset so ones matching `exam` come first.
    Returns (matching_ids_queryset). Caller fills the rest by rating."""
    kws = exam_keywords(exam)
    if not kws:
        return series_qs.none()
    q = Q()
    for kw in kws:
        q |= Q(subject__icontains=kw) | Q(title__icontains=kw)
    return series_qs.filter(q)


class OnboardingOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from post.models import Post

        return Response({
            "max_interests": MAX_INTERESTS,
            "study_classes": [{"key": k, "label": l} for k, l in STUDY_CLASSES],
            "target_exams": [{"key": k, "label": l} for k, l, _ in TARGET_EXAMS],
            # "general"/"other" make poor chips: they say nothing about taste.
            "interests": [
                {"key": k, "label": l} for k, l in Post.CATEGORY_CHOICES if k not in ("general", "other")
            ],
        })


class OnboardingQuickStartView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from post.models import UserInterest

        u = request.user
        return Response({
            "study_class": u.study_class,
            "target_exam": u.target_exam,
            "interests": list(UserInterest.objects.filter(user=u).values_list("category", flat=True)),
        })

    def post(self, request):
        from post import feed_cache
        from post.models import UserInterest

        from .views import OnboardingSuggestionsView

        ser = QuickStartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        user = request.user

        with transaction.atomic():
            user.study_class = data["study_class"]
            user.target_exam = data.get("target_exam", "")
            user.save(update_fields=["study_class", "target_exam"])

            UserInterest.objects.filter(user=user).exclude(category__in=data["interests"]).delete()
            have = set(UserInterest.objects.filter(user=user).values_list("category", flat=True))
            UserInterest.objects.bulk_create(
                [UserInterest(user=user, category=c) for c in data["interests"] if c not in have]
            )

        sync_study_preference(user, user.study_class, user.target_exam)

        # Next home-feed request must see the new interests, not a cached set.
        feed_cache.invalidate(user.pk)

        suggestions = OnboardingSuggestionsView()
        return Response({
            "study_class": user.study_class,
            "target_exam": user.target_exam,
            "interests": data["interests"],
            "suggested_users": suggestions._suggested_users(user),
            "suggested_campuses": suggestions._suggested_campuses(),
            "sample_test_series": suggestions._sample_test_series(user=user),
        })
