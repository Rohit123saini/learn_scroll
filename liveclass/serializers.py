"""
liveclass/serializers.py

DRF serializers for every model in liveclass/models.py.

Conventions used throughout:
    - Server-controlled fields (teacher, student, sender, created_by, uploaded_by,
      purchased_at, joined_at, etc.) are read_only here and set in the view
      (perform_create) from `request.user` — never trust the client for these.
    - Computed helper methods on the model (is_valid, is_joinable, is_late,
      has_access) are exposed as read-only SerializerMethodFields so the
      frontend doesn't have to re-implement that logic.
"""

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
    Assignment,
    AssignmentSubmission,
    BreakoutRoom,
    Certificate,
    ChatMessage,
    ChatReaction,
    ClassHoliday,
    ClassJoinRequest,
    ClassMaterial,
    ClassPass,
    ClassQuery,
    ClassReminder,
    ClassSchedule,
    ClassSession,
    Classroom,
    ClassroomReport,
    ClassroomBan,
    ClassroomReview,
    ClassroomShare,
    ClassroomStaff,
    ClassroomWishlist,
    CoinPurchase,
    CoinTransaction,
    CoinWithdrawal,
    Coupon,
    LivePoll,
    Notice,
    Notification,
    PassPurchase,
    PollResponse,
    PollTemplate,
    Referral,
    SessionParticipant,
    SessionReadState,
    SessionWaitlist,
)


# ---------------------------------------------------------------------------
# 0. USER (MINI) — shared, read-only, nested representation of a user.
#
# Used EVERYWHERE a user is exposed (teacher/student/sender/uploaded_by/
# posted_by/asked_by/created_by/etc.) instead of a bare PrimaryKeyRelatedField
# + a separate "<field>_name" CharField. That old pattern is why usernames
# and profile pictures never showed up on the Flutter side: the app's
# fromJson() methods already expect the user field to be a nested object
# (`{"id": 5, "username": "...", "profile_picture": "..."}`) with a fallback
# to flat keys — but the backend was only ever sending the ID + "_name",
# never "username" or "profile_picture" at all. This serializer fixes that
# in one place instead of patching every model's serializer separately.
#
# NOTE: `profile_picture` tries a few common attribute names since the
# actual User model wasn't in the files I was given — confirm the real
# field name on your custom User model and trim get_profile_picture() down
# to just that one attribute.
# ---------------------------------------------------------------------------
class UserMiniSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    profile_picture = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = ["id", "username", "full_name", "profile_picture"]

    def get_full_name(self, obj):
        name = obj.get_full_name()
        return name if name else obj.username

    def get_profile_picture(self, obj):
        for attr in ("profile_picture", "avatar", "photo", "profile_pic", "image"):
            value = getattr(obj, attr, None)
            if value:
                try:
                    url = value.url  # ImageField/FileField
                except AttributeError:
                    url = str(value)  # plain URLField/CharField
                request = self.context.get("request")
                return request.build_absolute_uri(url) if request else url
        return None


# ---------------------------------------------------------------------------
# 1. CLASSROOM
# ---------------------------------------------------------------------------
class ClassroomMiniSerializer(serializers.ModelSerializer):
    """Lightweight nested classroom summary — for lists that reference a
    classroom in passing (e.g. ClassroomMyShareSerializer, where the full
    ClassroomSerializer's field set would be unnecessary weight repeated
    on every row of someone's share history)."""

    teacher = UserMiniSerializer(read_only=True)

    class Meta:
        model = Classroom
        fields = ["id", "title", "subject", "cover_image", "teacher"]


class ClassroomSerializer(serializers.ModelSerializer):
    teacher = UserMiniSerializer(read_only=True)

    class Meta:
        model = Classroom
        fields = [
            "id", "teacher", "classroom_type", "organisation_name",
            "title", "subject", "description",
            "language", "cover_image", "whiteboard_enabled", "screen_share_enabled",
            "chat_enabled", "recording_enabled", "max_participants",
            # rating_avg/rating_count are cached on the model (kept in sync by
            # _sync_classroom_rating) — exposed here so the Explore/Search list
            # can show a star rating per card without an extra request per
            # classroom (that's what classrooms/{id}/stats/ is for instead).
            "rating_avg", "rating_count", "share_count",
            # FEATURE (refer & earn): both writable by the classroom's own
            # teacher, same as every other setting in this Meta.fields list
            # — ClassroomViewSet.perform_update already restricts writes on
            # this serializer to the teacher, so no extra gating is needed
            # here. referral_commission_percent's 0-100 bound comes for
            # free from the model field's MinValueValidator/MaxValueValidator
            # (DRF derives it automatically for a ModelSerializer).
            "referral_enabled", "referral_commission_percent",
            "is_active", "is_flagged", "created_at", "updated_at",
        ]
        # is_flagged is set automatically once enough reports come in (see
        # _auto_flag_classroom in models.py); share_count only moves via
        # Classroom.record_share() (see ClassroomViewSet.share) — neither
        # is ever client-writable.
        read_only_fields = [
            "id", "rating_avg", "rating_count", "share_count", "is_flagged", "created_at", "updated_at",
        ]

    def validate(self, attrs):
        classroom_type = attrs.get(
            "classroom_type", getattr(self.instance, "classroom_type", Classroom.ClassroomType.INDIVIDUAL)
        )
        organisation_name = attrs.get(
            "organisation_name", getattr(self.instance, "organisation_name", "")
        )
        if classroom_type == Classroom.ClassroomType.ORGANISATION and not organisation_name:
            raise serializers.ValidationError(
                {"organisation_name": "Required when classroom_type is 'organisation'."}
            )
        return attrs


# ---------------------------------------------------------------------------
# 2. SCHEDULE
# ---------------------------------------------------------------------------
class ClassScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassSchedule
        fields = [
            "id", "classroom", "recurrence_type", "days_of_week", "day_of_month",
            "start_date", "end_date", "start_time", "duration_minutes",
            "timezone", "is_active", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        recurrence_type = attrs.get("recurrence_type", getattr(self.instance, "recurrence_type", None))
        days_of_week = attrs.get("days_of_week", getattr(self.instance, "days_of_week", None))
        day_of_month = attrs.get("day_of_month", getattr(self.instance, "day_of_month", None))

        if recurrence_type == ClassSchedule.RecurrenceType.WEEKLY and not days_of_week:
            raise serializers.ValidationError(
                {"days_of_week": "Required when recurrence_type is 'weekly'."}
            )
        # NOTE (fix — CRITICAL): days_of_week was stored verbatim, whatever
        # case the client sent ("Mon", "MON", "mon"...). tasks.py's
        # generate_upcoming_sessions matches it against lowercase 3-letter
        # codes (ClassSchedule.WEEKDAY_CODES) with a plain `in` check — any
        # casing mismatch means _dates_for_schedule() never fires for that
        # schedule, so NO session ever gets auto-created for it, silently,
        # with no error anywhere. Normalize to the canonical lowercase form
        # here (the one place all input funnels through) and reject
        # anything that isn't a real weekday code, instead of discovering
        # the typo weeks later as "why did the class never show up".
        if recurrence_type == ClassSchedule.RecurrenceType.WEEKLY and days_of_week:
            normalized = [str(d).strip().lower()[:3] for d in days_of_week]
            invalid = sorted(set(normalized) - set(ClassSchedule.WEEKDAY_CODES))
            if invalid:
                raise serializers.ValidationError(
                    {"days_of_week": f"Invalid day code(s): {invalid}. Must be one of {list(ClassSchedule.WEEKDAY_CODES)}."}
                )
            attrs["days_of_week"] = normalized
        if recurrence_type == ClassSchedule.RecurrenceType.MONTHLY and not day_of_month:
            raise serializers.ValidationError(
                {"day_of_month": "Required when recurrence_type is 'monthly'."}
            )

        # NOTE (fix): start_date/end_date had no cross-field check — a
        # schedule could be saved with end_date before start_date, which
        # would then generate exactly zero sessions with no error surfaced
        # to the teacher setting it up, and duration_minutes had no upper
        # sanity bound (a typo like 6000 instead of 60 sailed straight
        # through).
        start_date = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start_date and end_date and end_date < start_date:
            raise serializers.ValidationError({"end_date": "Must be on or after start_date."})

        duration_minutes = attrs.get("duration_minutes", getattr(self.instance, "duration_minutes", None))
        if duration_minutes is not None and duration_minutes > 720:
            raise serializers.ValidationError(
                {"duration_minutes": "A single class can't be longer than 720 minutes (12 hours)."}
            )
        return attrs


# ---------------------------------------------------------------------------
# 3. SESSION
# ---------------------------------------------------------------------------
class ClassSessionSerializer(serializers.ModelSerializer):
    is_joinable = serializers.SerializerMethodField()
    classroom_title = serializers.CharField(source="classroom.title", read_only=True)
    # FEATURE (recording): derived, not stored directly — true exactly while
    # egress_id is set (see ClassSession.egress_id / start_recording /
    # stop_recording in views.py). Lets the client show a live "REC"
    # indicator without exposing the raw LiveKit egress_id itself.
    is_recording = serializers.SerializerMethodField()

    class Meta:
        model = ClassSession
        fields = [
            "id", "classroom", "classroom_title", "schedule", "room_id",
            "scheduled_start", "scheduled_end", "actual_start", "actual_end",
            "status", "recording_url", "is_recording", "whiteboard_snapshot", "is_joinable",
            "created_at",
        ]
        read_only_fields = ["id", "room_id", "created_at"]

    def get_is_joinable(self, obj):
        # NOTE (fix — CRITICAL): was `obj.is_joinable()` with no `is_host`,
        # which always evaluates the tightest (student) window — the
        # host/co-teacher/moderator/org-staff time-window exemption that
        # join()/token() in views.py actually grant (see
        # _resolve_session_roles + is_host there) never reached this field.
        # Every screen currently papers over it with an explicit
        # `s.isJoinable || widget.canManage` OR-check before deciding
        # whether to show an enter/start button — meaning any current or
        # future screen that reads this field WITHOUT that same OR-check
        # would incorrectly hide the button for a host outside the join
        # buffer window, even though the actual POST /join/ would have
        # let them in. Compute it correctly here instead of relying on
        # every call site to compensate for it.
        request = self.context.get("request")
        is_host = False
        if request is not None and getattr(request, "user", None) and request.user.is_authenticated:
            # Local import: views.py imports from this module at load time,
            # so importing views at module scope here would be circular.
            from .views import _can_manage_classroom

            is_host = _can_manage_classroom(obj.classroom, request.user)
        return obj.is_joinable(is_host=is_host)

    def get_is_recording(self, obj):
        return bool(obj.egress_id)

    def validate(self, attrs):
        # NOTE (fix): scheduled_start/scheduled_end had no cross-field
        # check — a session could be created or edited with scheduled_end
        # before (or equal to) scheduled_start, which silently breaks
        # is_joinable() (the window becomes empty or inverted) with no
        # error surfaced to whoever set it up.
        start = attrs.get("scheduled_start", getattr(self.instance, "scheduled_start", None))
        end = attrs.get("scheduled_end", getattr(self.instance, "scheduled_end", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"scheduled_end": "Must be after scheduled_start."})
        return attrs


# ---------------------------------------------------------------------------
# 4. PASS
# ---------------------------------------------------------------------------
class ClassPassSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassPass
        fields = [
            "id", "classroom", "pass_type", "title", "price", "validity_days",
            "max_classes", "is_active", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


# ---------------------------------------------------------------------------
# 5. PASS PURCHASE
# ---------------------------------------------------------------------------
class PassPurchaseSerializer(serializers.ModelSerializer):
    student = UserMiniSerializer(read_only=True)
    is_valid = serializers.SerializerMethodField()
    coupon_code = serializers.CharField(source="coupon.code", read_only=True, default=None)
    # NOTE (fix): the purchase-history list (My Passes) has nothing to show
    # per card besides raw ids without these — PassPurchaseViewSet.get_queryset
    # already select_related("class_pass__classroom") in anticipation of
    # exactly this, but the serializer never actually exposed the fields it
    # was optimizing for. Same dotted-source pattern as
    # ClassSessionSerializer.classroom_title above.
    classroom_title = serializers.CharField(source="class_pass.classroom.title", read_only=True)
    class_pass_title = serializers.CharField(source="class_pass.title", read_only=True)
    class_pass_type = serializers.CharField(source="class_pass.pass_type", read_only=True)
    # NOTE (fix): under the per-day escrow design (see PassPurchase's NOTE
    # (fix — "pay only for classes actually held") in models.py) coins_spent
    # alone no longer tells a student how much of their pass is actually
    # "at risk" vs. already paid out for classes held — remaining_balance
    # is exactly the amount reverse()/cancel() would hand back right now,
    # so it needs to be visible on every purchase card, not just derivable
    # by the backend.
    remaining_balance = serializers.IntegerField(read_only=True)
    # FEATURE (refer & earn): visible on the purchase card so a student can
    # see who they were referred by (if anyone), and so that same referrer
    # can see, on their own "purchases I referred" view (see
    # PassPurchaseViewSet.referral_earnings), exactly how much of this
    # purchase's commission has been released vs. still outstanding —
    # same remaining/released pairing already given for the teacher's side.
    referred_by = UserMiniSerializer(read_only=True)
    referral_remaining_balance = serializers.IntegerField(read_only=True)

    class Meta:
        model = PassPurchase
        fields = [
            "id", "student", "class_pass", "classroom_title", "class_pass_title",
            "class_pass_type", "coupon", "coupon_code",
            "payment_method", "amount_paid", "coins_spent", "transaction_id",
            "status", "purchased_at", "expires_at", "classes_attended",
            "is_active", "is_valid",
            "per_day_rate", "coins_released", "remaining_balance", "last_charge_date",
            "referred_by", "referral_commission_percent", "referral_coins_released",
            "referral_remaining_balance",
        ]
        read_only_fields = [
            "id", "purchased_at", "expires_at", "status", "classes_attended",
            "amount_paid", "coins_spent", "payment_method",
            "per_day_rate", "coins_released", "last_charge_date",
            "referred_by", "referral_commission_percent", "referral_coins_released",
        ]
        # amount_paid / coins_spent / expires_at / status / payment_method are
        # all computed server-side in ClassJoinRequestViewSet.accept() (based
        # on ClassPass.price, Coupon discount, and User.coin balance) — never
        # accepted directly from the client. There is no plain create/update
        # endpoint for PassPurchase at all, and no direct "buy" endpoint
        # either: a PassPurchase is created ONLY as the side effect of a
        # teacher/co-teacher/moderator accepting a ClassJoinRequest, and it
        # writes directly via the model, not this serializer's create() —
        # this Meta config is a safety net in case that changes.

    def get_is_valid(self, obj):
        return obj.is_valid()


# ---------------------------------------------------------------------------
# 5B. CLASS JOIN REQUEST
# ---------------------------------------------------------------------------
class ClassJoinRequestSerializer(serializers.ModelSerializer):
    """Read/create serializer. `accept`/`reject`/`cancel` are separate
    @action endpoints (see views.py) and don't go through this serializer's
    update() at all — status, decided_by, decided_at, decision_note, and
    pass_purchase are only ever written server-side from those actions."""

    student = UserMiniSerializer(read_only=True)
    classroom_title = serializers.CharField(source="classroom.title", read_only=True)
    class_pass_title = serializers.CharField(source="class_pass.title", read_only=True)
    class_pass_price = serializers.DecimalField(
        source="class_pass.price", max_digits=8, decimal_places=2, read_only=True
    )
    # FEATURE (refer & earn): write-only input — the ?ref= code from a
    # classrooms/{id}/refer-link/ link, e.g. copied out of the query string
    # by the client before POSTing here. Never stored verbatim; decoded to
    # a user in ClassJoinRequestViewSet.perform_create and stored as
    # referred_by below. Optional — omit entirely for a normal, unreferred
    # join request.
    referral_code = serializers.CharField(write_only=True, required=False, allow_blank=True)
    referred_by = UserMiniSerializer(read_only=True)

    class Meta:
        model = ClassJoinRequest
        fields = [
            "id", "classroom", "classroom_title", "class_pass", "class_pass_title",
            "class_pass_price", "student", "coupon_code", "message",
            "referral_code", "referred_by",
            "status", "decision_note", "decided_by", "decided_at", "pass_purchase",
            "requested_at",
        ]
        read_only_fields = [
            "id", "status", "decision_note", "decided_by", "decided_at",
            "pass_purchase", "requested_at",
        ]

    def validate(self, attrs):
        classroom = attrs.get("classroom")
        class_pass = attrs.get("class_pass")
        if classroom and class_pass and class_pass.classroom_id != classroom.id:
            raise serializers.ValidationError(
                {"class_pass": "This pass does not belong to the given classroom."}
            )
        return attrs


class ClassJoinRequestDecisionSerializer(serializers.Serializer):
    """Narrow serializer used only by the accept/reject actions — the only
    thing a teacher/co-teacher/moderator can attach to a decision is an
    optional short note (e.g. a rejection reason)."""

    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


# ---------------------------------------------------------------------------
# 6. SESSION PARTICIPANT
# ---------------------------------------------------------------------------
class SessionParticipantSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)
    # FEATURE (hand-raise): derived boolean for the client — the raw
    # timestamp (hand_raised_at) is still exposed too since the host UI
    # sorts the raised-hand queue oldest-first by it.
    hand_raised = serializers.SerializerMethodField()

    class Meta:
        model = SessionParticipant
        fields = ["id", "session", "user", "role", "joined_at", "left_at", "hand_raised", "hand_raised_at"]
        read_only_fields = ["id", "joined_at", "hand_raised_at"]

    def get_hand_raised(self, obj):
        return obj.hand_raised_at is not None


# ---------------------------------------------------------------------------
# 6B. BREAKOUT ROOM
#
# Shape matches the Flutter client's `BreakoutRoom.fromJson` exactly (see
# liveclass_models.dart / live_session_screen.dart): `room` + a flat list of
# participant identity strings. `participant_ids` are user-id strings
# (str(user_id)) — the same identity convention LiveKit tokens use
# elsewhere in this app — NOT SessionParticipant row ids, so the client can
# compare them directly against `Room.localParticipant.identity` with no
# extra lookup. `assign()`'s request body still takes a participant_id (row
# id, same convention as kick/mute/lower-hand) — the conversion to the
# identity string happens here, server-side, on the way out.
# ---------------------------------------------------------------------------
class BreakoutRoomSerializer(serializers.ModelSerializer):
    room = serializers.IntegerField(source="room_number")
    participant_ids = serializers.SerializerMethodField()

    class Meta:
        model = BreakoutRoom
        fields = ["room", "participant_ids"]

    def get_participant_ids(self, obj):
        return [str(p.user_id) for p in obj.participants.filter(left_at__isnull=True)]


# ---------------------------------------------------------------------------
# 7. CLASS MATERIAL
# ---------------------------------------------------------------------------
class ClassMaterialSerializer(serializers.ModelSerializer):
    uploaded_by = UserMiniSerializer(read_only=True)

    class Meta:
        model = ClassMaterial
        fields = [
            "id", "classroom", "session", "uploaded_by", "title",
            "material_type", "file", "external_link", "uploaded_at",
        ]
        read_only_fields = ["id", "uploaded_at"]

    def validate(self, attrs):
        # NOTE (fix — broke PATCH): these three previously used a bare
        # attrs.get(...) with no fallback to the existing instance, unlike
        # classroom/session a few lines below which correctly do
        # attrs.get(..., getattr(self.instance, ..., None)). On a PATCH that
        # only changes e.g. `title` (not resending material_type/file), this
        # would see material_type=None, file=None and wrongly raise "file
        # required" against a material that already has one — a real
        # partial-update client (e.g. an "edit title" screen) would get a
        # false validation error every time. Falls back to the existing
        # instance's values the same way the checks below already do.
        material_type = attrs.get("material_type", getattr(self.instance, "material_type", None))
        file = attrs.get("file", getattr(self.instance, "file", None))
        external_link = attrs.get("external_link", getattr(self.instance, "external_link", None))
        if material_type == ClassMaterial.MaterialType.LINK and not external_link:
            raise serializers.ValidationError({"external_link": "Required for material_type='link'."})
        if material_type != ClassMaterial.MaterialType.LINK and not file:
            raise serializers.ValidationError({"file": "Required for this material_type."})

        # NOTE (fix): `session` is an OPTIONAL FK independent of `classroom`
        # — nothing checked that a given session actually belongs to the
        # classroom the material is being filed under. A teacher managing
        # classroom A (who passes _can_manage_classroom in the view) could
        # set classroom=A, session=<some session that's actually classroom
        # B's>, silently cross-linking material to the wrong classroom's
        # session with no error. Same class of bug fixed below in
        # AssignmentSerializer/ClassQuerySerializer.
        classroom = attrs.get("classroom", getattr(self.instance, "classroom", None))
        session = attrs.get("session", getattr(self.instance, "session", None))
        if session and classroom and session.classroom_id != classroom.id:
            raise serializers.ValidationError({"session": "This session does not belong to the given classroom."})
        return attrs


# ---------------------------------------------------------------------------
# 8. CHAT MESSAGE
# ---------------------------------------------------------------------------
class ChatMessageSerializer(serializers.ModelSerializer):
    sender = UserMiniSerializer(read_only=True)
    # NEW (Pass 12 — chat reactions): {"heart": 3, "thumbs_up": 1} — only
    # reaction types with at least one reaction are included, so the
    # Flutter client doesn't have to filter zero-counts out itself.
    # my_reaction is this request's own user's current reaction (or None),
    # so the client can render an already-tapped emoji as "active" without
    # a second lookup. Both are cheap: reactions is prefetched by
    # ChatMessageViewSet.get_queryset() (see views.py), so this never adds
    # a query per message in a list response.
    reaction_counts = serializers.SerializerMethodField()
    my_reaction = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = [
            "id", "session", "sender", "message", "sent_at", "is_deleted",
            "reaction_counts", "my_reaction", "is_pinned", "pinned_by", "pinned_at",
        ]
        read_only_fields = ["id", "sent_at", "is_deleted", "is_pinned", "pinned_by", "pinned_at"]

    def get_reaction_counts(self, obj):
        counts: dict[str, int] = {}
        for r in obj.reactions.all():  # .all() so the prefetch_related on
            # ChatMessageViewSet.get_queryset() is actually reused instead
            # of firing a fresh filtered query per message.
            counts[r.reaction] = counts.get(r.reaction, 0) + 1
        return counts

    def get_my_reaction(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return None
        for r in obj.reactions.all():
            if r.user_id == user.id:
                return r.reaction
        return None


class ChatReactionSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)

    class Meta:
        model = ChatReaction
        fields = ["id", "message", "user", "reaction", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_reaction(self, value):
        if value not in ChatReaction.Reaction.values:
            raise serializers.ValidationError(
                f"Unsupported reaction. Choose one of: {', '.join(ChatReaction.Reaction.values)}."
            )
        return value


# ---------------------------------------------------------------------------
# 9. LIVE POLL + RESPONSE
# ---------------------------------------------------------------------------
class LivePollSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    result_counts = serializers.SerializerMethodField()

    class Meta:
        model = LivePoll
        fields = [
            "id", "session", "created_by", "question", "options",
            "is_active", "created_at", "closed_at", "result_counts",
        ]
        read_only_fields = ["id", "created_at", "closed_at"]

    def get_result_counts(self, obj):
        """{option_index: number_of_votes} — cheap live tally for the UI."""
        counts = {i: 0 for i in range(len(obj.options))}
        for idx in obj.responses.values_list("selected_option_index", flat=True):
            counts[idx] = counts.get(idx, 0) + 1
        return counts

    def validate_options(self, value):
        # NOTE (fix): `options` was an unvalidated JSONField — a poll could
        # be created with 0 or 1 options (nothing to vote between), or with
        # blank/non-string entries that would render as empty buttons on
        # the frontend and make PollResponseSerializer's own out-of-range
        # check meaningless (0 <= idx < 1 always passes for a single-option
        # poll). Require at least 2 non-empty string options.
        if not isinstance(value, list) or len(value) < 2:
            raise serializers.ValidationError("A poll needs at least 2 options.")
        if any(not isinstance(opt, str) or not opt.strip() for opt in value):
            raise serializers.ValidationError("Every option must be a non-empty string.")
        return value


class PollResponseSerializer(serializers.ModelSerializer):
    student = UserMiniSerializer(read_only=True)

    class Meta:
        model = PollResponse
        fields = ["id", "poll", "student", "selected_option_index", "answered_at"]
        read_only_fields = ["id", "answered_at"]

    def validate(self, attrs):
        poll = attrs.get("poll", getattr(self.instance, "poll", None))
        idx = attrs.get("selected_option_index", getattr(self.instance, "selected_option_index", None))
        if poll and idx is not None and idx >= len(poll.options):
            raise serializers.ValidationError({"selected_option_index": "Out of range for this poll's options."})
        return attrs


class PollTemplateSerializer(serializers.ModelSerializer):
    """NEW (Pass 13) — see PollTemplate's own docstring in models.py.
    created_by is read-only/server-set, same pattern as LivePollSerializer.
    """

    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = PollTemplate
        fields = ["id", "classroom", "created_by", "question", "options", "created_at"]
        read_only_fields = ["id", "created_at"]

    # Reuses LivePollSerializer.validate_options' rule verbatim (at least 2
    # non-empty string options) — a template with 0/1 options would just
    # produce an equally-broken LivePoll the moment it's fired.
    def validate_options(self, value):
        if not isinstance(value, list) or len(value) < 2:
            raise serializers.ValidationError("A poll template needs at least 2 options.")
        if any(not isinstance(opt, str) or not opt.strip() for opt in value):
            raise serializers.ValidationError("Every option must be a non-empty string.")
        return value


# ---------------------------------------------------------------------------
# 10. ASSIGNMENT + SUBMISSION
# ---------------------------------------------------------------------------
class AssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assignment
        fields = [
            "id", "classroom", "session", "title", "description",
            "attachment", "due_date", "max_score", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        # NOTE (fix): same gap as ClassMaterialSerializer above — `session`
        # is optional and independent of `classroom`; nothing stopped an
        # assignment from being filed under classroom A while pointing at
        # a session that actually belongs to classroom B.
        classroom = attrs.get("classroom", getattr(self.instance, "classroom", None))
        session = attrs.get("session", getattr(self.instance, "session", None))
        if session and classroom and session.classroom_id != classroom.id:
            raise serializers.ValidationError({"session": "This session does not belong to the given classroom."})
        return attrs


class AssignmentSubmissionSerializer(serializers.ModelSerializer):
    student = UserMiniSerializer(read_only=True)
    is_late = serializers.SerializerMethodField()

    class Meta:
        model = AssignmentSubmission
        fields = [
            "id", "assignment", "student", "file", "submitted_at",
            "score", "feedback", "graded_at", "is_late",
        ]
        read_only_fields = ["id", "submitted_at", "score", "feedback", "graded_at"]
        # score/feedback are set separately via a "grade" action restricted to
        # the teacher — not writable by the student on create/update.

    def get_is_late(self, obj):
        return obj.is_late()


class AssignmentGradeSerializer(serializers.ModelSerializer):
    """Narrow serializer used only by the teacher-only 'grade' action.

    NOTE (fix): `score` was a bare PositiveIntegerField with no upper
    bound, so a grade could be entered above the assignment's own
    `max_score` (e.g. 150/100) with no error — validated here against the
    parent assignment instead.
    """

    class Meta:
        model = AssignmentSubmission
        fields = ["score", "feedback"]

    def validate_score(self, value):
        max_score = self.instance.assignment.max_score if self.instance else None
        if max_score is not None and value > max_score:
            raise serializers.ValidationError(f"Score can't exceed this assignment's max_score ({max_score}).")
        return value


# ---------------------------------------------------------------------------
# 11. CLASSROOM REVIEW
# ---------------------------------------------------------------------------
class ClassroomReviewSerializer(serializers.ModelSerializer):
    student = UserMiniSerializer(read_only=True)

    class Meta:
        model = ClassroomReview
        fields = ["id", "classroom", "student", "rating", "comment", "created_at"]
        read_only_fields = ["id", "created_at"]


# ---------------------------------------------------------------------------
# 11C. WISHLIST
# ---------------------------------------------------------------------------
class ClassroomWishlistSerializer(serializers.ModelSerializer):
    # Full nested classroom card on read (title/teacher/cover_image/rating —
    # everything a "My Wishlist" screen needs without a second request per
    # item) — but writes only take a plain classroom id, same
    # read-vs-write split pattern as PassPurchaseSerializer.coupon_code.
    classroom = ClassroomSerializer(read_only=True)
    classroom_id = serializers.PrimaryKeyRelatedField(
        queryset=Classroom.objects.filter(is_active=True, is_deleted=False),
        source="classroom",
        write_only=True,
    )

    class Meta:
        model = ClassroomWishlist
        fields = ["id", "classroom", "classroom_id", "created_at"]
        read_only_fields = ["id", "created_at"]


# ---------------------------------------------------------------------------
# 11B. CLASSROOM REPORT
# ---------------------------------------------------------------------------
class ClassroomReportSerializer(serializers.ModelSerializer):
    reported_by = UserMiniSerializer(read_only=True)
    classroom_title = serializers.CharField(source="classroom.title", read_only=True)

    class Meta:
        model = ClassroomReport
        fields = [
            "id", "classroom", "classroom_title", "reported_by", "reason", "description",
            "status", "reviewed_by", "admin_note", "reviewed_at", "created_at",
        ]
        # status/reviewed_by/admin_note/reviewed_at are only ever written
        # server-side from ClassroomReportViewSet.review() (staff-only) —
        # never accepted directly from the reporting user's create() call.
        read_only_fields = [
            "id", "status", "reviewed_by", "admin_note", "reviewed_at", "created_at",
        ]


# ---------------------------------------------------------------------------
# 11D. CLASSROOM SHARE — request/response shapes for ClassroomViewSet.share.
# Not a plain ModelSerializer for the input side: `to_user_id` is the only
# thing a client actually sends (channel is optional metadata about where
# the link is headed, not something this endpoint enforces), everything
# else on ClassroomShare (classroom/shared_by/shared_with) is set
# server-side in the view from the URL pk / request.user / the resolved
# to_user_id — same "server controls the owner fields" convention as every
# other perform_create in this file.
# ---------------------------------------------------------------------------
class ClassroomShareSerializer(serializers.Serializer):
    # In-app share target — omit entirely for an outside-the-app share
    # (WhatsApp/SMS/email/copy-link), where there's no platform user to
    # notify and this endpoint's job is just to hand back the link.
    to_user_id = serializers.PrimaryKeyRelatedField(
        source="to_user",
        queryset=get_user_model().objects.all(),
        required=False,
        allow_null=True,
        default=None,
    )
    channel = serializers.ChoiceField(
        choices=ClassroomShare.Channel.choices, default=ClassroomShare.Channel.OTHER
    )

    def validate(self, attrs):
        # Sharing TO a specific platform user is what "in_app" means —
        # force it regardless of whatever (or nothing) the client passed
        # for channel, so a stray channel="copy_link" alongside a real
        # to_user_id can't produce a misleading analytics row. Going the
        # other way — channel="in_app" with no to_user_id — is a client
        # bug (there's no one to notify), so that's a 400 instead of a
        # silently half-meaningful row.
        to_user = attrs.get("to_user")
        if to_user is not None:
            attrs["channel"] = ClassroomShare.Channel.IN_APP
        elif attrs.get("channel") == ClassroomShare.Channel.IN_APP:
            raise serializers.ValidationError({"to_user_id": "Required when channel is 'in_app'."})
        return attrs


class ClassroomShareResultSerializer(serializers.Serializer):
    """Response shape for ClassroomViewSet.share — everything the client
    needs to either notify (already done server-side, for in_app) or hand
    off to the OS's native share sheet for every outside-the-app channel."""

    share_id = serializers.IntegerField()
    web_url = serializers.URLField()
    deep_link = serializers.CharField()
    share_text = serializers.CharField()
    shared_with = UserMiniSerializer(allow_null=True)
    share_count = serializers.IntegerField()


class ReferLinkResultSerializer(serializers.Serializer):
    """Response shape for ClassroomViewSet.refer_link — the caller's own
    referral link for this classroom, plus the commission rate they're
    referring at right now (so the client can show "earn X%" without a
    second call to fetch the classroom)."""

    referral_code = serializers.CharField()
    web_url = serializers.URLField()
    deep_link = serializers.CharField()
    share_text = serializers.CharField()
    commission_percent = serializers.DecimalField(max_digits=5, decimal_places=2)


class ClassroomShareLogSerializer(serializers.ModelSerializer):
    """Read-only history row — used by ClassroomViewSet.share-stats so a
    teacher can see who's sharing their classroom and how, not just the
    running total on share_count."""

    shared_by = UserMiniSerializer(read_only=True)
    shared_with = UserMiniSerializer(read_only=True)

    class Meta:
        model = ClassroomShare
        fields = ["id", "shared_by", "shared_with", "channel", "created_at"]


class ClassroomMyShareSerializer(serializers.ModelSerializer):
    """Read-only history row for ClassroomViewSet.my_shares — the sharer's
    own view, across every classroom they've shared (share-stats above is
    the opposite direction: one classroom's teacher looking at everyone
    who shared IT). Includes a classroom mini-summary since, unlike
    share-stats which is already scoped to one classroom, this list spans
    many."""

    classroom = ClassroomMiniSerializer(read_only=True)
    shared_with = UserMiniSerializer(read_only=True)

    class Meta:
        model = ClassroomShare
        fields = ["id", "classroom", "shared_with", "channel", "created_at"]


# ---------------------------------------------------------------------------
# 12. COUPON
# ---------------------------------------------------------------------------
class CouponSerializer(serializers.ModelSerializer):
    created_by = UserMiniSerializer(read_only=True)
    is_valid = serializers.SerializerMethodField()

    class Meta:
        model = Coupon
        fields = [
            "id", "classroom", "created_by", "code", "discount_percent",
            "discount_amount", "valid_from", "valid_until", "max_uses",
            "used_count", "is_active", "is_valid",
        ]
        read_only_fields = ["id", "used_count"]

    def get_is_valid(self, obj):
        return obj.is_valid()

    def validate(self, attrs):
        # NOTE (fix): valid_from/valid_until had no cross-field check —
        # a coupon could be created with valid_until before valid_from,
        # making Coupon.is_valid() permanently False with no clear reason
        # surfaced to the teacher creating it.
        valid_from = attrs.get("valid_from", getattr(self.instance, "valid_from", None))
        valid_until = attrs.get("valid_until", getattr(self.instance, "valid_until", None))
        if valid_from and valid_until and valid_until <= valid_from:
            raise serializers.ValidationError({"valid_until": "Must be after valid_from."})
        discount_percent = attrs.get("discount_percent", getattr(self.instance, "discount_percent", None))
        discount_amount = attrs.get("discount_amount", getattr(self.instance, "discount_amount", None))
        if not discount_percent and not discount_amount:
            raise serializers.ValidationError("Set at least one of discount_percent or discount_amount.")
        return attrs


# ---------------------------------------------------------------------------
# 13. COIN TRANSACTION (read-only ledger — created only by server-side logic)
# ---------------------------------------------------------------------------
class CoinTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoinTransaction
        fields = [
            "id", "user", "txn_type", "reason", "amount",
            "balance_after", "reference_id", "created_at",
        ]
        read_only_fields = fields  # entirely system-generated, never client-writable


# ---------------------------------------------------------------------------
# 13A2. COIN PURCHASE (real-money -> coin top-up). See CoinPurchase in
# models.py and CoinPurchaseViewSet in views.py.
#
# NOTE (fix — CRITICAL, app failed to import at all): views.py has always
# imported CoinPurchaseSerializer/CoinPurchaseInitiateSerializer/
# CoinPurchaseVerifySerializer from this module, and CoinPurchaseViewSet
# uses all three — but none of the three classes (nor the CoinPurchase
# model itself) were ever actually defined/imported here. That's not a
# "feature missing" gap, it's an `ImportError` the very first time
# liveclass.views is imported (i.e. the moment urls.py is loaded) — the
# entire app, not just coin purchases, failed to boot. Added below,
# following the same shape as CoinWithdrawalSerializer just below.
# ---------------------------------------------------------------------------
class CoinPurchaseSerializer(serializers.ModelSerializer):
    """Output-only — a CoinPurchase row is always created server-side
    (CoinPurchaseViewSet.initiate/retry build it directly via
    CoinPurchase.objects.create()), so every field here is just reporting
    state back to the client, never accepting client-supplied values."""

    class Meta:
        model = CoinPurchase
        fields = [
            "id", "coins", "amount_inr", "status", "order_id",
            "gateway_payment_id", "retry_of", "failure_reason",
            "created_at", "verified_at",
        ]
        read_only_fields = fields


class CoinPurchaseInitiateSerializer(serializers.Serializer):
    """Input for `POST coin-purchases/initiate/`. `coins` is the only
    client-supplied value in the whole purchase flow — amount_inr is
    always derived server-side from CoinWithdrawal.COIN_TO_INR_RATE (see
    CoinPurchaseViewSet.initiate), never trusted from the request."""

    coins = serializers.IntegerField(min_value=1)


class CoinPurchaseVerifySerializer(serializers.Serializer):
    """Input for `POST coin-purchases/{id}/verify/` — the gateway
    checkout callback payload, in Razorpay's field-naming shape (see
    _verify_gateway_signature in views.py)."""

    razorpay_order_id = serializers.CharField()
    razorpay_payment_id = serializers.CharField()
    razorpay_signature = serializers.CharField()


# ---------------------------------------------------------------------------
# 13C. COIN WITHDRAWAL — payout of a user's real, earned coin balance.
# `payout_details` shape depends on `payout_method`; validated here so a
# malformed/incomplete payload never reaches the model layer:
#   bank_transfer -> {"account_holder", "account_number", "ifsc"}
#   upi           -> {"upi_id"}
# ---------------------------------------------------------------------------
class CoinWithdrawalSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)

    class Meta:
        model = CoinWithdrawal
        fields = [
            "id", "user", "coins", "amount_inr", "payout_method", "payout_details",
            "status", "admin_note", "external_reference",
            "reviewed_by", "requested_at", "reviewed_at", "paid_at",
        ]
        read_only_fields = [
            "id", "user", "amount_inr", "status", "admin_note", "external_reference",
            "reviewed_by", "requested_at", "reviewed_at", "paid_at",
        ]

    def validate_coins(self, value):
        if value < CoinWithdrawal.MIN_WITHDRAWAL_COINS:
            raise serializers.ValidationError(
                f"Minimum withdrawal is {CoinWithdrawal.MIN_WITHDRAWAL_COINS} coins."
            )
        return value

    def validate(self, attrs):
        method = attrs.get("payout_method")
        details = attrs.get("payout_details") or {}
        if method == CoinWithdrawal.PayoutMethod.BANK_TRANSFER:
            required = {"account_holder", "account_number", "ifsc"}
        elif method == CoinWithdrawal.PayoutMethod.UPI:
            required = {"upi_id"}
        else:
            raise serializers.ValidationError({"payout_method": "Unsupported payout method."})
        missing = required - details.keys()
        if missing:
            raise serializers.ValidationError(
                {"payout_details": f"Missing required field(s) for {method}: {', '.join(sorted(missing))}."}
            )
        return attrs


# ---------------------------------------------------------------------------
# 14. CLASSROOM STAFF
# ---------------------------------------------------------------------------
class ClassroomStaffSerializer(serializers.ModelSerializer):
    """NOTE (fix): `user` was ONLY exposed as a read-only nested
    UserMiniSerializer, with no writable counterpart anywhere in this
    serializer. Since ClassroomStaffViewSet.perform_create() just calls
    serializer.save() (it has no request.user substitute here — staff are
    added FOR someone else, not for the caller), `user` never ended up in
    validated_data at all. Every POST to staff/ therefore attempted
    ClassroomStaff.objects.create(classroom=..., role=...) with no user,
    which fails against the model's required (non-nullable) FK — the
    "add staff" feature could never actually work. Added `user_id`
    (write-only, source="user") as the field clients set; `user` stays the
    read-only nested representation for responses."""

    user = UserMiniSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        source="user", queryset=get_user_model().objects.all(), write_only=True
    )

    class Meta:
        model = ClassroomStaff
        fields = ["id", "classroom", "user", "user_id", "role", "added_at"]
        read_only_fields = ["id", "added_at"]


# ---------------------------------------------------------------------------
# 15. WAITLIST
# ---------------------------------------------------------------------------
class SessionWaitlistSerializer(serializers.ModelSerializer):
    student = UserMiniSerializer(read_only=True)

    class Meta:
        model = SessionWaitlist
        fields = ["id", "session", "student", "joined_at", "notified"]
        read_only_fields = ["id", "joined_at", "notified"]


# ---------------------------------------------------------------------------
# 16. CERTIFICATE (list/retrieve are read-only; issuing is a teacher-only
#     write via CertificateIssueSerializer, kept separate from the display
#     serializer above so a client can never set certificate_id/issued_at.)
# ---------------------------------------------------------------------------
class CertificateSerializer(serializers.ModelSerializer):
    student = UserMiniSerializer(read_only=True)
    classroom_title = serializers.CharField(source="classroom.title", read_only=True)

    class Meta:
        model = Certificate
        fields = [
            "id", "classroom", "classroom_title", "student",
            "certificate_id", "certificate_file", "issued_at",
        ]
        read_only_fields = fields


class CertificateIssueSerializer(serializers.ModelSerializer):
    """Teacher/co-teacher/moderator-only write path (CertificateViewSet.create).
    certificate_id / issued_at are set server-side (see perform_create), not
    accepted from the client — same pattern as AssignmentGradeSerializer."""

    class Meta:
        model = Certificate
        fields = ["classroom", "student", "certificate_file"]


# ---------------------------------------------------------------------------
# 17. CLASS REMINDER
# ---------------------------------------------------------------------------
class ClassReminderSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)

    class Meta:
        model = ClassReminder
        fields = ["id", "session", "user", "remind_at", "channel", "is_sent"]
        read_only_fields = ["id", "is_sent"]


# ---------------------------------------------------------------------------
# 18. CLASS HOLIDAY / OFF-DAY
# ---------------------------------------------------------------------------
class ClassHolidaySerializer(serializers.ModelSerializer):
    created_by = UserMiniSerializer(read_only=True)

    class Meta:
        model = ClassHoliday
        fields = [
            "id", "classroom", "schedule", "date", "reason",
            "created_by", "created_at",
        ]
        read_only_fields = ["id", "created_by", "created_at"]
        # `created_by` is set server-side in ClassHolidayViewSet.perform_create
        # from request.user — never accepted from the client.


# ---------------------------------------------------------------------------
# 19. NOTICE BOARD
# ---------------------------------------------------------------------------
class NoticeSerializer(serializers.ModelSerializer):
    posted_by = UserMiniSerializer(read_only=True)
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = Notice
        fields = [
            "id", "classroom", "posted_by", "title", "message",
            "priority", "is_pinned", "created_at", "expires_at", "is_expired",
        ]
        read_only_fields = ["id", "posted_by", "created_at"]
        # `posted_by` is set server-side in NoticeViewSet.perform_create from
        # request.user — never accepted from the client.

    def get_is_expired(self, obj):
        return obj.is_expired()


# ---------------------------------------------------------------------------
# 20. CLASS QUERY / DOUBT
# ---------------------------------------------------------------------------
class ClassQuerySerializer(serializers.ModelSerializer):
    asked_by = UserMiniSerializer(read_only=True)
    answered_by = UserMiniSerializer(read_only=True)

    class Meta:
        model = ClassQuery
        fields = [
            "id", "classroom", "session", "asked_by", "question",
            "status", "answer", "answered_by", "answered_at",
            "created_at",
        ]
        read_only_fields = [
            "id", "asked_by", "status", "answer", "answered_by", "answered_at", "created_at",
        ]
        # `asked_by` is set server-side from request.user on create. `answer`/
        # `status`/`answered_by`/`answered_at` are only ever set through the
        # teacher-only "answer" action below — never directly writable here.

    def validate(self, attrs):
        # NOTE (fix): same gap as ClassMaterialSerializer/AssignmentSerializer
        # above — `session` is optional and independent of `classroom`.
        classroom = attrs.get("classroom", getattr(self.instance, "classroom", None))
        session = attrs.get("session", getattr(self.instance, "session", None))
        if session and classroom and session.classroom_id != classroom.id:
            raise serializers.ValidationError({"session": "This session does not belong to the given classroom."})
        return attrs


class ClassQueryAnswerSerializer(serializers.ModelSerializer):
    """Narrow serializer used only by the teacher/co-teacher/moderator-only 'answer' action."""

    class Meta:
        model = ClassQuery
        fields = ["answer"]

    def validate_answer(self, value):
        if not value.strip():
            raise serializers.ValidationError("Answer can't be empty.")
        return value


# ---------------------------------------------------------------------------
# CLASSROOM STATS — composite read-only payload for Classroom.stats action.
# Not a ModelSerializer: it wraps a plain dict assembled in the view from
# several different sources (cached counters + a computed string + a
# queryset), not a single model instance.
# ---------------------------------------------------------------------------
class ClassroomStatsSerializer(serializers.Serializer):
    rating_avg = serializers.DecimalField(max_digits=3, decimal_places=2)
    rating_count = serializers.IntegerField()
    enrolled_count = serializers.IntegerField()
    share_count = serializers.IntegerField()
    weekly_timing = serializers.CharField()
    upcoming_holidays = ClassHolidaySerializer(many=True)


# ---------------------------------------------------------------------------
# 21. NOTIFICATION — read-only. Rows are only ever created server-side (see
# create_notification()/create_bulk_notifications() in models.py), so there's
# no writable ModelSerializer counterpart the way ClassQuery/Assignment have
# one; the "mark read" actions on the viewset don't need a request body at
# all, let alone this serializer.
# ---------------------------------------------------------------------------
class NotificationSerializer(serializers.ModelSerializer):
    classroom_title = serializers.CharField(source="classroom.title", read_only=True, default=None)

    class Meta:
        model = Notification
        fields = [
            "id", "notif_type", "title", "message",
            "classroom", "classroom_title", "session",
            "is_read", "created_at", "read_at",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# CLASSROOM BAN — same user_id-write / user-read split as
# ClassroomStaffSerializer above.
# ---------------------------------------------------------------------------
class ClassroomBanSerializer(serializers.ModelSerializer):
    student = UserMiniSerializer(read_only=True)
    student_id = serializers.PrimaryKeyRelatedField(
        source="student", queryset=get_user_model().objects.all(), write_only=True
    )
    banned_by = UserMiniSerializer(read_only=True)

    class Meta:
        model = ClassroomBan
        fields = ["id", "classroom", "student", "student_id", "banned_by", "reason", "created_at"]
        read_only_fields = ["id", "classroom", "banned_by", "created_at"]


# ---------------------------------------------------------------------------
# REFERRAL PROGRAM
# ---------------------------------------------------------------------------
class MyReferralCodeSerializer(serializers.Serializer):
    """Response shape for GET /liveclass/referrals/my-code/ — not a
    ModelSerializer, since 'code' is computed (see referral_code_for_user
    in models.py) rather than a stored field."""

    code = serializers.CharField()
    referral_count = serializers.IntegerField()
    total_bonus_earned = serializers.IntegerField()
    bonus_per_referral = serializers.IntegerField()


class ReferralRedeemSerializer(serializers.Serializer):
    """Input for POST /liveclass/referrals/redeem/ — all the actual
    validation (code decodes to a real user, not self-referral, account is
    still inside the redeem window, hasn't already redeemed one) happens in
    ReferralViewSet.redeem, not here, since it needs request.user and
    can't be expressed as a single-field validator."""

    code = serializers.CharField(max_length=20)


class ReferralSerializer(serializers.ModelSerializer):
    """Read-only — used by GET /liveclass/referrals/ to show a user the
    list of people they've successfully referred."""

    referred = UserMiniSerializer(read_only=True)

    class Meta:
        model = Referral
        fields = ["id", "referred", "bonus_amount", "created_at"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# TEACHER EARNINGS DASHBOARD — composite read-only payload, same pattern as
# ClassroomStatsSerializer above (wraps a dict assembled in the view from
# several aggregate queries over PassDailyCharge, not a single instance).
# ---------------------------------------------------------------------------
class EarningsByClassroomSerializer(serializers.Serializer):
    classroom_id = serializers.IntegerField()
    classroom_title = serializers.CharField()
    total_earned = serializers.IntegerField()
    sessions_charged = serializers.IntegerField()


class EarningsByDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    amount = serializers.IntegerField()


class TeacherEarningsSerializer(serializers.Serializer):
    total_earned = serializers.IntegerField()
    total_sessions_charged = serializers.IntegerField()
    this_month_earned = serializers.IntegerField()
    last_30_days = EarningsByDaySerializer(many=True)
    by_classroom = EarningsByClassroomSerializer(many=True)


# ---------------------------------------------------------------------------
# RECORDINGS LIBRARY — read-only list of a classroom's past recorded
# sessions. Deliberately narrow (not the full ClassSessionSerializer): the
# recordings tab only needs enough to render a browsable list + play link.
# ---------------------------------------------------------------------------
class SessionRecordingSerializer(serializers.ModelSerializer):
    classroom_title = serializers.CharField(source="classroom.title", read_only=True)

    class Meta:
        model = ClassSession
        fields = [
            "id", "classroom", "classroom_title",
            "scheduled_start", "actual_end", "recording_url",
        ]
        read_only_fields = fields