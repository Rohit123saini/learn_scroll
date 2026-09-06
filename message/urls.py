# message/urls.py
"""
🔧 GAP FIX — pehle is file ki jagah galti se `Middleware.py` ka duplicate
upload ho gaya tha, isliye real routing kabhi exist hi nahi karti thi
(saare naye endpoints — search, pin, star, schedule, media — resolve
hi nahi hote). Ye ab `views.py` ke actual confirmed class/action names se
poori tarah reconstruct ki gayi hai.

Router-based ViewSets (list/detail + @action sub-routes automatically
banti hain):
    - ConversationViewSet -> /conversations/...
    - MessageViewSet      -> /messages/...
    - GroupViewSet        -> /groups/...
    - BlockedUserViewSet  -> /blocked-users/...
    - CallHistoryViewSet  -> /calls/history/...

Plain APIViews (@action-based nahi, isliye manual path()):
    - UserPresenceView, CallInitiateView, CallActionView,
      StudyRoomJoinView, StudyRoomStateView, DeviceTokenView,
      AiStudyRoomView, VoiceTranscribeView, SmartReplySuggestionsView,
      ClassTranscriptChunkUploadView, ClassTranscriptSearchView,
      ClassroomCopilotView (all in views_ai.py — last 3 are NAYA, see
      §Feature 3/4 below), MessageUploadAPIView (upload_view.py)

NOTE: `views_ai.py` aur `upload_view.py` is review me upload nahi hui
thi — sirf documentation aur `views.py`'s existing import se naam
confirm hote hain. Agar unke andar class/function names alag hain to
sirf neeche wale 2 import lines badalne padenge, baaki sab already
`views.py` se verified hai.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    BlockedUserViewSet,
    CallActionView,
    CallHistoryViewSet,
    CallInitiateView,
    ConversationViewSet,
    DeviceTokenView,
    # 🔥 NAYA — Doubt Queue ("persistent, upvotable question board per
    # classroom" + "Ask Anonymously")
    DoubtQuestionViewSet,
    GroupViewSet,
    MessageViewSet,
    ReadReceiptSettingsView,
    StudyRoomJoinView,
    StudyRoomStateView,
    # 🔥 NAYA — Feature 6: attendance/consistency streak
    StudyRoomStreakView,
    UserPresenceView,
)
# 🔥 FIX — `VoiceTranscribeView` (views_ai.py) is fully implemented, has
# its own throttle class, and its docstring even documents its intended
# route (`POST /message/ai/transcribe/`) — but only `AiStudyRoomView` was
# ever imported/routed here, so the transcribe endpoint was unreachable
# (404) despite being complete. Wiring it in below.
from .views_ai import (
    AiStudyRoomView, VoiceTranscribeView, SmartReplySuggestionsView,
    # 🔥 NAYA — class transcript (chunk upload + search) + classroom copilot
    ClassTranscriptChunkUploadView, ClassTranscriptSearchView, ClassroomCopilotView,
    # 🔥 NAYA — Feature 5: revision deck (flashcards + quiz from class materials)
    RevisionDeckView,
)
from .upload_view import MessageUploadAPIView
# 🔥 NAYA — Parent/Guardian Mode (Feature 8): student-side code
# management + parent-side (no-login) verify/dashboard.
from .views_parent import (
    ParentAccessCodeView,
    # 🔧 NAYA — renew an expiring/expired code without re-sharing it
    ParentAccessCodeRenewView,
    # 🔧 NAYA — reveal-once: explicit re-reveal of the full plaintext code
    ParentAccessCodeRevealView,
    ParentCodeTokenDetailView,
    ParentCodeTokensView,
    ParentDashboardView,
    ParentVerifyCodeView,
)
# 🔧 GAP FIX — Feature 12 (Focus Mode / Smart DND): `FocusSessionView`
# was fully coded in `views_focus.py` (its own header comment even gives
# this exact import + path snippet) but was never actually imported/
# routed here, so the endpoint was unreachable (404) end-to-end despite
# `focus_mode_screen.dart` already calling it. Wiring it in below.
from .views_focus import FocusSessionHistoryView, FocusSessionView

# 🔧 GAP FIX (this session) — `PROJECT_ARCHITECTURE.md` §"API surface" and
# `message_api_service.dart` BOTH describe the scheduled-message contract as
#     GET/POST /message/conversations/<id>/scheduled/
#     PATCH/DELETE /message/scheduled/<id>/
# but the actual `@action`s in `views.py` are named `schedule-message` /
# `scheduled-messages` (on ConversationViewSet) and `schedule` nested under
# `/messages/<id>/` (on MessageViewSet) — none of those paths match what the
# frontend calls, so all 4 scheduled-message calls were 404ing end-to-end.
#
# Fix is additive and non-breaking: register the documented URLs below as
# extra paths pointing at the SAME existing ViewSet actions imported above
# (no business-logic duplication, no risk to whatever already calls the
# old `schedule-message`/`scheduled-messages`/`messages/<id>/schedule/`
# paths — those keep working unchanged).

router = DefaultRouter()
router.register(r'conversations', ConversationViewSet, basename='conversation')
router.register(r'messages', MessageViewSet, basename='message')
router.register(r'groups', GroupViewSet, basename='group')
router.register(r'blocked-users', BlockedUserViewSet, basename='blocked-user')
router.register(r'calls/history', CallHistoryViewSet, basename='call-history')

urlpatterns = [
    path('', include(router.urls)),

    # --- Presence ---
    path('presence/<int:user_id>/', UserPresenceView.as_view(), name='user-presence'),
    # User model ka PK integer hai (custom `login.User`), UUID nahi —
    # isliye `<int:user_id>` (Conversation/Message/Group/Call sab UUID
    # PK hain, wo router se auto-wire hote hain).

    # --- Read-receipt privacy toggle (NAYA) ---
    path('presence/read-receipts/', ReadReceiptSettingsView.as_view(), name='read-receipt-settings'),

    # --- Calls ---
    path('calls/initiate/', CallInitiateView.as_view(), name='call-initiate'),
    path('calls/<uuid:call_id>/action/', CallActionView.as_view(), name='call-action'),

    # --- Study Room ---
    path('study-room/<uuid:conversation_id>/join/', StudyRoomJoinView.as_view(), name='study-room-join'),
    path('study-room/<uuid:conversation_id>/state/', StudyRoomStateView.as_view(), name='study-room-state'),

    # --- Attendance / consistency streak (NAYA, Feature 6) ---
    path('study-room/<uuid:conversation_id>/streak/', StudyRoomStreakView.as_view(), name='study-room-streak'),

    # --- Revision Deck: flashcards + quiz from class materials (NAYA, Feature 5) ---
    path('study-room/<uuid:conversation_id>/revision-deck/', RevisionDeckView.as_view(), name='study-room-revision-deck'),

    # --- Device tokens (push notifications) ---
    path('device-token/', DeviceTokenView.as_view(), name='device-token'),

    # --- AI Study Room (Gemini summary/quiz) ---
    path('ai-study-room/', AiStudyRoomView.as_view(), name='ai-study-room'),

    # --- AI Voice-message transcription (🔥 FIX — was implemented but unrouted) ---
    path('ai/transcribe/', VoiceTranscribeView.as_view(), name='ai-transcribe'),

    # --- AI Smart-reply suggestions (NAYA) ---
    path('ai/smart-replies/', SmartReplySuggestionsView.as_view(), name='ai-smart-replies'),

    # --- Class transcript: chunk upload + timestamped search (NAYA) ---
    path(
        'study-room/<uuid:conversation_id>/transcript-chunk/',
        ClassTranscriptChunkUploadView.as_view(),
        name='class-transcript-chunk-upload',
    ),
    path(
        'study-room/<uuid:conversation_id>/transcript/',
        ClassTranscriptSearchView.as_view(),
        name='class-transcript-search',
    ),

    # --- Classroom Copilot: AI grounded in full classroom context (NAYA) ---
    path('ai/classroom-copilot/', ClassroomCopilotView.as_view(), name='ai-classroom-copilot'),

    # --- Generic file upload (returns a URL to attach to a message) ---
    path('upload/', MessageUploadAPIView.as_view(), name='message-upload'),

    # --- Scheduled messages (🔧 GAP FIX — frontend/doc-contract aliases) ---
    # Same handlers as the `schedule-message` / `scheduled-messages` /
    # `messages/<id>/schedule/` actions already registered via the router
    # above — just exposed at the URLs `message_api_service.dart` and
    # `PROJECT_ARCHITECTURE.md` actually expect.
    path(
        'conversations/<uuid:pk>/scheduled/',
        ConversationViewSet.as_view({'get': 'scheduled_messages_list', 'post': 'schedule_message'}),
        name='conversation-scheduled-alias',
    ),
    path(
        'scheduled/<uuid:pk>/',
        MessageViewSet.as_view({'patch': 'manage_schedule', 'delete': 'manage_schedule'}),
        name='scheduled-message-alias',
    ),

    # --- Group photo removal (🔧 GAP FIX — see views.py GroupViewSet.remove_photo) ---
    path(
        'groups/<uuid:pk>/photo/',
        GroupViewSet.as_view({'delete': 'remove_photo'}),
        name='group-photo-alias',
    ),

    # --- Doubt Queue (NAYA) — persistent, upvotable question board per
    # classroom/group, + "Ask Anonymously" (`Group.allow_anonymous_doubts`,
    # toggled via the existing `PATCH /groups/<id>/` GroupSerializer field —
    # no separate toggle endpoint needed). Nested under the group, not
    # routed via DefaultRouter (see views.py DoubtQuestionViewSet docstring
    # for why: group-membership is enforced once in `get_group()`, shared
    # by every action below). ---
    path(
        'groups/<uuid:group_id>/doubts/',
        DoubtQuestionViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='group-doubts',
    ),
    path(
        'groups/<uuid:group_id>/doubts/<uuid:pk>/upvote/',
        DoubtQuestionViewSet.as_view({'post': 'upvote', 'delete': 'upvote'}),
        name='group-doubt-upvote',
    ),
    path(
        'groups/<uuid:group_id>/doubts/<uuid:pk>/answer/',
        DoubtQuestionViewSet.as_view({'post': 'answer'}),
        name='group-doubt-answer',
    ),
    path(
        'groups/<uuid:group_id>/doubts/<uuid:pk>/reveal/',
        DoubtQuestionViewSet.as_view({'post': 'reveal'}),
        name='group-doubt-reveal',
    ),

    # --- Parent/Guardian Mode (NAYA, Feature 8) ---
    # Student-side (own login): generate/list/revoke parent codes.
    path('parent/codes/', ParentAccessCodeView.as_view(), name='parent-codes'),
    # 🔧 NAYA — extend an existing code's TTL in place (see views_parent.py)
    path(
        'parent/codes/<uuid:code_id>/renew/',
        ParentAccessCodeRenewView.as_view(),
        name='parent-code-renew',
    ),
    # 🔧 NAYA — reveal-once: re-show the full plaintext code on demand
    path(
        'parent/codes/<uuid:code_id>/reveal/',
        ParentAccessCodeRevealView.as_view(),
        name='parent-code-reveal',
    ),
    # 🔧 NEW — per-device management within one code (revoke a single lost
    # device without killing every other device on the same shared code).
    path(
        'parent/codes/<uuid:code_id>/tokens/',
        ParentCodeTokensView.as_view(),
        name='parent-code-tokens',
    ),
    path(
        'parent/codes/<uuid:code_id>/tokens/<uuid:token_id>/',
        ParentCodeTokenDetailView.as_view(),
        name='parent-code-token-detail',
    ),
    # Parent-side (no login): redeem a code, then hit the dashboard with
    # the returned `parent_token` in an `X-Parent-Token` header.
    path('parent/verify/', ParentVerifyCodeView.as_view(), name='parent-verify'),
    path('parent/dashboard/', ParentDashboardView.as_view(), name='parent-dashboard'),

    # --- Focus Mode / Smart DND (🔧 GAP FIX — Feature 12) ---
    # Single path, method-differentiated: POST=start, GET=status, DELETE=cancel.
    # See `views_focus.py`'s own header comment for the original snippet.
    path('focus-session/', FocusSessionView.as_view(), name='focus-session'),
    # 🔧 NAYA — past sessions (start/end/duration/exception-rule), so a
    # student can see how much they've actually used Focus Mode.
    path('focus-session/history/', FocusSessionHistoryView.as_view(), name='focus-session-history'),
]

# ==============================================================================
# Router se auto-generate hone waale (isliye yahan manually likhne ki zaroorat
# nahi) — sirf reference/documentation ke liye, taaki naya kaam karne wala
# turant dekh sake kya already available hai:
#
#   GET/POST   /conversations/
#   GET        /conversations/<id>/
#   POST       /conversations/start_private/
#   PATCH      /conversations/<id>/settings/
#   PATCH      /conversations/<id>/disappearing_messages/
#   PATCH      /conversations/<id>/label/
#   POST       /conversations/bulk_delete/
#   POST       /conversations/<id>/participants/
#   GET/POST   /conversations/<id>/messages/
#   POST       /conversations/<id>/read_all/
#   GET        /conversations/<id>/search/?q=...
#   GET        /conversations/search_all/?q=...
#   GET        /conversations/<id>/pinned/
#   POST       /conversations/<id>/schedule-message/        (NAYA)
#   GET        /conversations/<id>/scheduled-messages/       (NAYA)
#
#   GET        /messages/<id>/
#   PATCH      /messages/<id>/
#   DELETE     /messages/<id>/?for_everyone=true|false
#   POST/DELETE /messages/<id>/react/
#   GET        /messages/<id>/read-status/                       (NAYA)
#   POST       /messages/<id>/read/
#   POST       /messages/forward/
#   POST/DELETE /messages/<id>/pin/
#   POST/DELETE /messages/<id>/star/                          (NAYA)
#   GET        /messages/starred/                             (NAYA)
#   PATCH/DELETE /messages/<id>/schedule/                     (NAYA)
#
#   POST       /groups/
#   GET        /groups/, /groups/<id>/
#   PATCH/DELETE /groups/<id>/
#   POST       /groups/<id>/members/
#   POST       /groups/join/
#   GET        /groups/<id>/join-requests/
#   POST       /groups/<id>/join-requests/<request_id>/approve/
#   POST       /groups/<id>/join-requests/<request_id>/reject/
#   PATCH/DELETE /groups/<id>/members/<user_id>/
#   GET        /groups/<id>/media/
#
#   GET/POST   /groups/<id>/doubts/?status=answered|unanswered           (NAYA)
#   POST/DELETE /groups/<id>/doubts/<doubt_id>/upvote/                   (NAYA)
#   POST       /groups/<id>/doubts/<doubt_id>/answer/                    (NAYA, admin/mod)
#   POST       /groups/<id>/doubts/<doubt_id>/reveal/                    (NAYA, admin/mod)
#
#   GET/POST/DELETE /blocked-users/, /blocked-users/<lookup>/
#
#   GET        /calls/history/
#   GET        /calls/history/missed/?since=<iso>
#   GET        /calls/history/<call_id>/addable-participants/
#   POST       /calls/history/<call_id>/add-participant/
#
#   GET/POST/DELETE /parent/codes/        (NAYA, student login required)
#   POST       /parent/codes/<id>/renew/  (NAYA, extend TTL in place)
#   GET        /parent/codes/<id>/tokens/           (NAYA, per-device list)
#   DELETE     /parent/codes/<id>/tokens/<token_id>/ (NAYA, per-device revoke)
#   POST       /parent/verify/            (NAYA, no login — parent side; 410 if code expired)
#   GET        /parent/dashboard/         (NAYA, X-Parent-Token header)
# ==============================================================================