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

H17 (cold start): the same call takes an optional "subjects" list (<= MAX_SUBJECTS, e.g.
["physics", "maths"]); it is mirrored into UserPreference.focus_subjects so the very first feed
can lean on them. The FIRST successful save is also recorded in the growth funnel
(`interests_saved`, with seconds since signup) so product can see how many new users reach a
personalised feed inside 60 seconds. The response carries `first_feed_ready: true` - the app may
go straight to Home (follows / sample test are optional extras, never a gate).

T2.3 - ROLE-BASED: the same call takes an optional "role" ("student" default | "teacher" |
"parent"). Students send class / exam / interests exactly as before. A teacher only needs
interests (what they teach / post about) and a parent needs nothing but the role: for them
"study_class" and "interests" become optional. The response always carries "role" and a
short "next_steps" list (what to do first as that role) - the app renders these on the last
onboarding step and on Home. The role is a personalisation hint ONLY: it grants nothing.

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
MAX_SUBJECTS = 5

# H17: subject chips. Keys follow post/feed_chips.SUBJECT_ALIASES (canonical spellings) so they match
# `subcategory` / hashtags the same way the Home chips do; anything else is used as typed there.
SUBJECTS = [
    ("maths", "Maths"),
    ("physics", "Physics"),
    ("chemistry", "Chemistry"),
    ("biology", "Biology"),
    ("english", "English"),
    ("hindi", "Hindi"),
    ("science", "Science"),
    ("social science", "Social Science"),
    ("computer science", "Computer Science"),
    ("economics", "Economics"),
    ("accountancy", "Accountancy"),
    ("reasoning", "Reasoning"),
]
_SUBJECT_KEYS = [k for k, _ in SUBJECTS]

ROLES = [
    ("student", "Student"),
    ("teacher", "Teacher"),
    ("parent", "Parent"),
]
_ROLE_KEYS = [k for k, _ in ROLES]

# First things to do per role. `action` is a stable key the Flutter app maps to a screen
# (unknown keys are ignored by the app, so adding one here is always safe).
NEXT_STEPS = {
    "student": [
        {"key": "daily_challenge", "title": "Take today's daily challenge", "subtitle": "One free test a day keeps your streak alive", "action": "daily_challenge"},
        {"key": "join_class", "title": "Join a live class", "subtitle": "Ask your teacher for the class link, or browse Explore", "action": "explore_classes"},
        {"key": "follow", "title": "Follow people who post what you study", "subtitle": "Your feed gets better with every follow", "action": "search"},
    ],
    "teacher": [
        {"key": "create_class", "title": "Create your first class", "subtitle": "Set a schedule, price and a free demo", "action": "create_class"},
        {"key": "invite_students", "title": "Invite your students", "subtitle": "WhatsApp message, QR code and poster - ready in one tap", "action": "invite_students"},
        {"key": "profile", "title": "Complete your profile", "subtitle": "Students check who they learn from", "action": "edit_profile"},
    ],
    "parent": [
        {"key": "link_child", "title": "Link your child", "subtitle": "Enter the parent code your child's teacher or campus gave you", "action": "parent_code"},
        {"key": "see_progress", "title": "Follow attendance & progress", "subtitle": "Updates reach you once a child is linked", "action": "parent_code"},
    ],
}


def next_steps_for(role: str):
    return NEXT_STEPS.get(role or "student", NEXT_STEPS["student"])

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
    role = serializers.ChoiceField(choices=_ROLE_KEYS, required=False, default="student")
    # Required for students only (see validate()); teachers / parents may omit it.
    study_class = serializers.ChoiceField(choices=_CLASS_KEYS, required=False, allow_blank=True, default="")
    target_exam = serializers.ChoiceField(choices=_EXAM_KEYS, required=False, allow_blank=True, default="")
    interests = serializers.ListField(
        child=serializers.CharField(), required=False, min_length=0, max_length=MAX_INTERESTS,
        error_messages={
            "max_length": f"Pick at most {MAX_INTERESTS} interests.",
        },
    )
    # H17: optional, students only. Sent => replaces the saved subjects; omitted => left untouched.
    subjects = serializers.ListField(
        child=serializers.ChoiceField(choices=_SUBJECT_KEYS), required=False, max_length=MAX_SUBJECTS,
        error_messages={"max_length": f"Pick at most {MAX_SUBJECTS} subjects."},
    )

    def validate(self, attrs):
        role = attrs.get("role") or "student"
        attrs["role"] = role
        interests = attrs.get("interests") or []
        attrs["interests"] = interests
        attrs["role_only"] = False
        if (
            role == "student"
            and "role" in getattr(self, "initial_data", {})
            and not attrs.get("study_class")
            and not interests
            and not attrs.get("target_exam")
        ):
            # A student who skipped the quick-start questions: remember ONLY the role.
            # (Sending class/interests/exam without the rest still hits the rules below.)
            attrs["role_only"] = True
            return attrs
        if role == "student":
            # Unchanged student rules: class + at least one interest.
            if not attrs.get("study_class"):
                raise serializers.ValidationError({"study_class": "This field is required."})
            if not interests:
                raise serializers.ValidationError({"interests": "Pick at least one interest."})
        else:
            # Class / exam are student-only; don't store them for a teacher / parent.
            attrs["study_class"] = ""
            attrs["target_exam"] = ""
        return attrs

    def validate_subjects(self, value):
        return list(dict.fromkeys(value))  # de-dupe, keep order

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


def sync_study_preference(user, study_class: str, target_exam: str, subjects=None) -> None:
    """Mirror the quick-start class/exam into UserPreference so the very first
    feed is already exam-aware. Best-effort: never blocks onboarding, never
    touches exam_mode / exam_date (those stay the user's own). H17: `subjects` (None = not sent) replaces
    focus_subjects - the user just picked them in the quick-start step."""
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
        if subjects is not None and list(pref.focus_subjects or []) != list(subjects):
            pref.focus_subjects = list(subjects)
            fields.append("focus_subjects")
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


def _saved_subjects(user):
    try:
        from user_profile.models import UserPreference

        pref = UserPreference.objects.filter(user=user).first()
        return [str(x) for x in (pref.focus_subjects if pref else []) or []]
    except Exception:  # pragma: no cover
        return []


class OnboardingOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from post.models import Post

        return Response({
            "max_interests": MAX_INTERESTS,
            "max_subjects": MAX_SUBJECTS,
            "subjects": [{"key": k, "label": l} for k, l in SUBJECTS],
            "roles": [{"key": k, "label": l} for k, l in ROLES],
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
            "role": u.onboarding_role,
            "study_class": u.study_class,
            "target_exam": u.target_exam,
            "interests": list(UserInterest.objects.filter(user=u).values_list("category", flat=True)),
            "subjects": _saved_subjects(u),
        })

    def post(self, request):
        from post import feed_cache
        from post.models import UserInterest

        from .views import OnboardingSuggestionsView

        ser = QuickStartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        user = request.user

        if data.get("role_only"):
            # Role only - never touch the user's class / exam / interests.
            user.onboarding_role = data["role"]
            user.save(update_fields=["onboarding_role"])
            from post.models import UserInterest as _UI

            suggestions = OnboardingSuggestionsView()
            return Response({
                "role": user.onboarding_role,
                "next_steps": next_steps_for(user.onboarding_role),
                "study_class": user.study_class,
                "target_exam": user.target_exam,
                "interests": list(_UI.objects.filter(user=user).values_list("category", flat=True)),
                "suggested_users": suggestions._suggested_users(user),
                "suggested_campuses": suggestions._suggested_campuses(),
                "sample_test_series": suggestions._sample_test_series(user=user),
            })

        with transaction.atomic():
            user.onboarding_role = data["role"]
            user.study_class = data["study_class"]
            user.target_exam = data.get("target_exam", "")
            user.save(update_fields=["onboarding_role", "study_class", "target_exam"])

            # A teacher / parent who picked no interests keeps whatever they had saved
            # (replace semantics only apply when they actually sent a set).
            if data["interests"] or data["role"] == "student":
                UserInterest.objects.filter(user=user).exclude(category__in=data["interests"]).delete()
                have = set(UserInterest.objects.filter(user=user).values_list("category", flat=True))
                UserInterest.objects.bulk_create(
                    [UserInterest(user=user, category=c) for c in data["interests"] if c not in have]
                )

        if data["role"] == "student":
            sync_study_preference(user, user.study_class, user.target_exam, data.get("subjects"))

        # Next home-feed request must see the new interests, not a cached set.
        feed_cache.invalidate(user.pk)

        # H17: growth funnel - first save only, never raises (analytics must not break onboarding).
        from . import funnel

        funnel.record_interests_saved(
            user, interests=len(data["interests"]), study_class=user.study_class,
            target_exam=user.target_exam, subjects=len(data.get("subjects") or []),
        )

        suggestions = OnboardingSuggestionsView()
        return Response({
            "role": user.onboarding_role,
            "next_steps": next_steps_for(user.onboarding_role),
            "study_class": user.study_class,
            "target_exam": user.target_exam,
            "interests": data["interests"],
            "subjects": _saved_subjects(user),
            "first_feed_ready": True,  # H17: Home is personalised from now on - the app may skip the extra steps
            "suggested_users": suggestions._suggested_users(user),
            "suggested_campuses": suggestions._suggested_campuses(),
            "sample_test_series": suggestions._sample_test_series(user=user),
        })
