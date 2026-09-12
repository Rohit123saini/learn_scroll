# message/views_ai.py
import hashlib
import logging
from datetime import timedelta

from django.db import models
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import UserRateThrottle
from .ai_service import (
    generate_summary, generate_quiz, transcribe_audio, generate_reply_suggestions,
    generate_classroom_answer, generate_revision_deck, AI_ENABLED,
)
from .models import (
    Message, ConversationParticipant, ClassTranscriptSegment,
    # 🔥 NAYA — Feature 5: revision deck
    RevisionDeck,
)

logger = logging.getLogger(__name__)

class AiStudyThrottle(UserRateThrottle):
    rate = '20/min' # 1 user 1 min me 20 se zyada AI call nahi maar sakta
    scope = 'ai_study'


# 🔥 NAYA — same throttle scope alag rakha hai transcription ke liye,
# kyunki audio download+Gemini-call summary/quiz se costlier hai (audio
# bytes fetch karna padta hai) — tighter default rate.
class AiTranscribeThrottle(UserRateThrottle):
    rate = '15/min'
    scope = 'ai_transcribe'


# 🔥 NAYA — smart-reply suggestions apna throttle scope. Summary/quiz se
# zyada frequent trigger ho sakta hai (har naya incoming message pe
# client suggestion maang sakta hai), isliye thoda loose rate rakha hai —
# par phir bhi bounded, taaki koi client bug/loop Gemini quota na uda de.
class SmartReplyThrottle(UserRateThrottle):
    rate = '30/min'
    scope = 'ai_smart_reply'


# 🔥 NAYA — class-transcript chunk upload throttle. Har participant apna
# mic har ~45s (see study_room_call_manager.dart CHUNK_DURATION) me ek
# chunk upload karega — ek 1hr class me ~80 chunks/user, isliye loose
# rakha hai, but bounded so a bug can't spam Gemini/storage.
class ClassTranscriptChunkThrottle(UserRateThrottle):
    rate = '30/min'
    scope = 'ai_class_transcript_chunk'


# 🔥 NAYA — transcript search apna throttle, halka rakha hai kyunki ye
# sirf DB query hai (Gemini call nahi).
class ClassTranscriptSearchThrottle(UserRateThrottle):
    rate = '60/min'
    scope = 'ai_class_transcript_search'


# 🔥 NAYA — classroom copilot Gemini-backed hai (summary/quiz jaisa
# costly), isliye tight rakha hai.
class ClassroomCopilotThrottle(UserRateThrottle):
    rate = '15/min'
    scope = 'ai_classroom_copilot'


# 🔥 NAYA — revision deck sabse "costly" AI call hai is file me (poore
# chat + transcript + board content ko ek saath process karta hai,
# summary/quiz se bada prompt) — tightest rate.
class RevisionDeckThrottle(UserRateThrottle):
    rate = '10/min'
    scope = 'ai_revision_deck'


class VoiceTranscribeView(APIView):
    """
    🔥 NAYA — POST /message/ai/transcribe/
    Body: {"file_url": "<voice message file_url>", "mime_type": "audio/ogg"}
    Response: {"transcript": "..."}

    `file_url` wahi URL hai jo `upload_view.py` (audio type) se milta hai,
    ya kisi already-sent voice `Message.file_url` se. Transcript client-side
    "View transcript" bubble me dikhaya ja sakta hai — server yahan
    `Message.meta` khud update nahi karta (jisse ye view stateless rahe);
    agar chaho to caller (views.py message-flow) `meta['transcript']` me
    result save kar sakta hai taaki dobara call na karni pade (24h Gemini
    cache to already hai, par ek DB round-trip bhi bach jaayega).
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [AiTranscribeThrottle]

    def post(self, request):
        if not AI_ENABLED:
            return Response({"error": "AI service not configured on server"}, status=503)

        file_url = (request.data.get("file_url") or "").strip()
        mime_type = (request.data.get("mime_type") or "audio/ogg").strip()

        if not file_url:
            return Response({"error": "file_url required hai"}, status=400)

        try:
            transcript = transcribe_audio(file_url, mime_type=mime_type)
            return Response({"transcript": transcript}, status=200)
        except Exception as e:
            logger.exception(f"Voice transcription failed user={request.user.id} err={e}")
            return Response({"error": "Transcription temporarily unavailable, try again"}, status=500)

class SmartReplySuggestionsView(APIView):
    """
    🔥 NAYA — POST /message/ai/smart-replies/
    Body: {"conversation_id": "<uuid>"}
    Response: {"suggestions": ["...", "...", "..."]}

    `ai_service.py` already Gemini-connected tha (summary/quiz ke liye) —
    yahi client reuse karke last few messages ke context se 3 short
    tap-to-send quick-reply chips generate karte hain (Gmail/WhatsApp
    Business "Smart Reply" jaisa). `generate_reply_suggestions()` khud
    24h content-hash cache use karta hai (same `ai_service.py` pattern
    jo summary/quiz/transcript follow karte hain), isliye same
    conversation-state ke liye repeat calls Gemini nahi maarte.

    🔥 FIX — `conversation_id` ab cache-key input me bhi jaata hai (see
    `ai_service.generate_reply_suggestions`), taaki do alag conversations
    me by-chance identical context-text ban jaaye to bhi suggestions
    cross-conversation leak na hon.

    Sirf conversation ka ACTIVE member hi call kar sakta hai — random
    `conversation_id` daal ke doosron ke messages leak nahi hone chahiye
    (isliye 404, 403 nahi — existence bhi leak nahi karte).
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [SmartReplyThrottle]

    CONTEXT_MESSAGE_COUNT = 10

    def post(self, request):
        if not AI_ENABLED:
            return Response({"error": "AI service not configured on server"}, status=503)

        conversation_id = (request.data.get("conversation_id") or "").strip()
        if not conversation_id:
            return Response({"error": "conversation_id required hai"}, status=400)

        is_member = ConversationParticipant.objects.filter(
            conversation_id=conversation_id, user=request.user, left_at__isnull=True,
        ).exists()
        if not is_member:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        # Sirf plain text messages context ke liye — media/system/poll
        # messages me koi "reply-able" text nahi hota is prompt ke liye.
        messages = list(
            Message.objects.filter(
                conversation_id=conversation_id, type='text',
            ).exclude(text__isnull=True).exclude(text='').exclude(
                deleted_for_everyone=True,
            ).exclude(
                deleted_for_users=request.user,
            ).exclude(
                is_scheduled=True,
            ).select_related('sender').order_by('-created_at')[:self.CONTEXT_MESSAGE_COUNT]
        )

        if not messages:
            return Response({"error": "Not enough conversation context yet"}, status=400)

        # Apna hi last message ho to "reply suggest" karna meaningless hai
        # — client ko ye tab hi call karna chahiye jab kisi AUR ka naya
        # message aaya ho, par server-side bhi defensively check karte hain.
        if messages[0].sender_id == request.user.id:
            return Response(
                {"error": "Last message is your own — nothing to reply to"}, status=400,
            )

        messages.reverse()  # oldest-first — prompt ke liye readable order
        context_lines = []
        for m in messages:
            who = "Me" if m.sender_id == request.user.id else (m.sender.first_name or "Them")
            context_lines.append(f"{who}: {m.text.strip()}")
        context_text = "\n".join(context_lines)

        try:
            suggestions = generate_reply_suggestions(context_text, conversation_id)
        except Exception as e:
            logger.exception(
                f"Smart reply generation failed user={request.user.id} "
                f"conv={conversation_id} err={e}"
            )
            return Response({"error": "Suggestions temporarily unavailable"}, status=500)

        return Response({"suggestions": suggestions}, status=200)


class AiStudyRoomView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [AiStudyThrottle]

    def post(self, request):
        if not AI_ENABLED:
            return Response({"error": "AI service not configured on server"}, status=503)

        mode = request.data.get("mode")
        content = (request.data.get("content") or "").strip()

        # --- VALIDATION ---
        if mode not in ["summary", "quiz"]:
            return Response({"error": "mode must be summary or quiz"}, status=400)
        if len(content) < 20:
            return Response({"error": "Board content too short"}, status=400)
        if len(content) > 10000: # Abuse rokne ke liye
            return Response({"error": "Content too large, max 10k chars"}, status=400)

        try:
            if mode == "summary":
                summary = generate_summary(content)
                return Response({"summary": summary}, status=200)
            else:
                questions = generate_quiz(content)
                return Response({"questions": questions}, status=200)

        except Exception as e:
            logger.exception(f"AI generation failed user={request.user.id} mode={mode} err={e}")
            # Frontend ko safe message
            return Response({"error": "AI temporarily unavailable, try again"}, status=500)


# ==========================================================================
# 🔥 NAYA — FEATURE 3: Class transcript + timestamped searchable recap
# --------------------------------------------------------------------------
# Design (see models_class_transcript_ADD_TO_models.py header for the "why"):
# LiveKit server-side room recording/egress abhi wired nahi hai is stack me,
# isliye poori class ki EK continuous recording nahi banti. Iske bajaye har
# participant apna khud ka mic locally chunk-record karta hai
# (study_room_call_manager.dart, ~45s chunks) aur yahan upload karta hai.
# Har chunk ek transcript segment banta hai, session-relative offset ke
# saath — combined, sab participants ke segments milke ek time-ordered,
# searchable class transcript ban jaate hain.
# ==========================================================================

class ClassTranscriptChunkUploadView(APIView):
    """
    POST /message/study-room/<conversation_id>/transcript-chunk/
    Body: {
      "session_id": "...", "audio_file_url": "...", "mime_type": "audio/mp4",
      "start_offset_seconds": 0.0, "end_offset_seconds": 45.0
    }
    Response: {"segment_id": "...", "status": "pending"}

    `audio_file_url` `upload_view.py` se already-uploaded chunk ka URL hai
    (client pehle `MessageUploadAPIView` pe upload karta hai, jaise voice
    note ke liye karta hai, phir uska URL yahan register karta hai).
    Transcription background me (Celery) hoti hai — response turant aata
    hai, client ko wait nahi karna padta agle chunk record karne ke liye.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ClassTranscriptChunkThrottle]

    def post(self, request, conversation_id):
        if not AI_ENABLED:
            return Response({"error": "AI service not configured on server"}, status=503)

        is_member = ConversationParticipant.objects.filter(
            conversation_id=conversation_id, user=request.user, left_at__isnull=True,
        ).exists()
        if not is_member:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        session_id = (request.data.get("session_id") or "").strip()
        audio_file_url = (request.data.get("audio_file_url") or "").strip()
        try:
            start_offset = float(request.data.get("start_offset_seconds"))
            end_offset = float(request.data.get("end_offset_seconds"))
        except (TypeError, ValueError):
            return Response({"error": "start_offset_seconds/end_offset_seconds must be numbers"}, status=400)

        if not session_id or not audio_file_url:
            return Response({"error": "session_id and audio_file_url required hain"}, status=400)
        if end_offset <= start_offset:
            return Response({"error": "end_offset_seconds must be > start_offset_seconds"}, status=400)

        segment = ClassTranscriptSegment.objects.create(
            conversation_id=conversation_id,
            session_id=session_id,
            speaker=request.user,
            start_offset_seconds=start_offset,
            end_offset_seconds=end_offset,
            audio_file_url=audio_file_url,
            status=ClassTranscriptSegment.STATUS_PENDING,
        )

        # local import — tasks.py ko views_ai.py se import cycle na bane
        from .tasks import transcribe_class_chunk_task
        transcribe_class_chunk_task.delay(str(segment.id))

        return Response({"segment_id": str(segment.id), "status": segment.status}, status=201)


class ClassTranscriptSearchView(APIView):
    """
    GET /message/study-room/<conversation_id>/transcript/?session_id=...&q=...
    Response: {"segments": [
      {"id": "...", "speaker_name": "...", "start_offset_seconds": 12.0,
       "end_offset_seconds": 57.0, "text": "..."}
    ]}

    `session_id` optional — na diya jaaye to us conversation ki SABSE
    RECENT session ke segments dikhaye jaate hain (revision ke liye
    usually last class hi chahiye hoti hai). `q` optional — diya jaaye to
    sirf matching segments (case-insensitive substring match — chhoti
    class transcripts ke liye kaafi hai, scale badhne par Postgres
    SearchVectorField pe upgrade karo jaisa Message pe already hai).
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ClassTranscriptSearchThrottle]

    def get(self, request, conversation_id):
        is_member = ConversationParticipant.objects.filter(
            conversation_id=conversation_id, user=request.user, left_at__isnull=True,
        ).exists()
        if not is_member:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        session_id = (request.GET.get("session_id") or "").strip()
        query = (request.GET.get("q") or "").strip()

        qs = ClassTranscriptSegment.objects.filter(
            conversation_id=conversation_id, status=ClassTranscriptSegment.STATUS_DONE,
        ).select_related('speaker')

        if session_id:
            qs = qs.filter(session_id=session_id)
        else:
            latest_session = (
                ClassTranscriptSegment.objects.filter(conversation_id=conversation_id)
                .order_by('-created_at').values_list('session_id', flat=True).first()
            )
            if not latest_session:
                return Response({"segments": []}, status=200)
            qs = qs.filter(session_id=latest_session)

        if query:
            qs = qs.filter(text__icontains=query)

        qs = qs.order_by('start_offset_seconds')[:500]  # ek session bhi itni lambi nahi hoti ki isse zyada segments bane

        segments = [
            {
                "id": str(s.id),
                "speaker_name": (s.speaker.first_name or s.speaker.username) if s.speaker else "Unknown",
                "start_offset_seconds": s.start_offset_seconds,
                "end_offset_seconds": s.end_offset_seconds,
                "text": s.text,
                "audio_file_url": s.audio_file_url,
            }
            for s in qs
        ]
        return Response({"segments": segments}, status=200)


# ==========================================================================
# 🔥 NAYA — FEATURE 4: AI copilot grounded in full classroom context
# --------------------------------------------------------------------------
# Context 3 sources se assemble hota hai:
#   1. Recent chat messages (same query pattern jo SmartReplySuggestionsView
#      use karta hai)
#   2. Whiteboard text (client bhejta hai — `_collectBoardTextContent()`
#      study_room_screen.dart me already exist karta hai, wahi content
#      yahan bhi bhej dete hain)
#   3. Transcript segments jinme se koi bhi word question se match karta
#      ho (naive keyword overlap — chhote classroom-scale text ke liye
#      kaafi hai; agar chaho to embeddings-based retrieval pe upgrade
#      karo, but us layer ke bina bhi ye already "generic AI" se kaafi
#      behtar hai kyunki asli class content use ho raha hai)
# Koi Assignment/StudyMaterial model abhi backend me exist nahi karta
# (confirmed against CHAT_APP_DOCUMENTATION.md) — isliye wo context source
# is version me shaamil NAHI hai. Jab wo model bane, yahan ek aur context
# section add kar dena.
# ==========================================================================

class ClassroomCopilotView(APIView):
    """
    POST /message/ai/classroom-copilot/
    Body: {"conversation_id": "...", "question": "...", "board_content": "..."}
    Response: {"answer": "..."}

    `board_content` optional — study room screen se current whiteboard
    text bhej sakte ho. Chat screen se copilot use ho (whiteboard na ho)
    to blank chhod do, sirf chat + transcript context use hoga.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ClassroomCopilotThrottle]

    CONTEXT_MESSAGE_COUNT = 30
    MAX_TRANSCRIPT_SEGMENTS = 40

    def post(self, request):
        if not AI_ENABLED:
            return Response({"error": "AI service not configured on server"}, status=503)

        conversation_id = (request.data.get("conversation_id") or "").strip()
        question = (request.data.get("question") or "").strip()
        board_content = (request.data.get("board_content") or "").strip()

        if not conversation_id or not question:
            return Response({"error": "conversation_id and question required hain"}, status=400)
        if len(question) > 500:
            return Response({"error": "Question too long, max 500 chars"}, status=400)

        is_member = ConversationParticipant.objects.filter(
            conversation_id=conversation_id, user=request.user, left_at__isnull=True,
        ).exists()
        if not is_member:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        context_sections = []

        # --- 1. Recent chat messages ---
        messages = list(
            Message.objects.filter(conversation_id=conversation_id, type='text')
            .exclude(text__isnull=True).exclude(text='')
            .exclude(deleted_for_everyone=True).exclude(deleted_for_users=request.user)
            .exclude(is_scheduled=True)
            .select_related('sender').order_by('-created_at')[:self.CONTEXT_MESSAGE_COUNT]
        )
        if messages:
            messages.reverse()
            lines = [f"{(m.sender.first_name or 'Someone')}: {m.text.strip()}" for m in messages]
            context_sections.append("Recent chat messages:\n" + "\n".join(lines))

        # --- 2. Whiteboard content (client-supplied, current session) ---
        if board_content:
            context_sections.append("Whiteboard notes:\n" + board_content[:3000])

        # --- 3. Transcript segments matching the question's keywords ---
        keywords = [w.lower() for w in question.split() if len(w) > 3][:8]
        transcript_qs = ClassTranscriptSegment.objects.filter(
            conversation_id=conversation_id, status=ClassTranscriptSegment.STATUS_DONE,
        )
        if keywords:
            keyword_filter = models.Q()
            for kw in keywords:
                keyword_filter |= models.Q(text__icontains=kw)
            matched = list(transcript_qs.filter(keyword_filter).order_by('-created_at')[:self.MAX_TRANSCRIPT_SEGMENTS])
        else:
            matched = []
        # Kuch match na mile to bhi sabse recent session ke kuch segments de
        # do — "last class me kya hua tha" jaise generic sawaalon ke liye.
        if not matched:
            matched = list(transcript_qs.order_by('-created_at')[:self.MAX_TRANSCRIPT_SEGMENTS])

        if matched:
            matched.sort(key=lambda s: s.start_offset_seconds)
            lines = [
                f"[{int(s.start_offset_seconds // 60)}:{int(s.start_offset_seconds % 60):02d}] {s.text.strip()}"
                for s in matched if s.text.strip()
            ]
            if lines:
                context_sections.append("Transcript excerpts (mm:ss = time into class):\n" + "\n".join(lines))

        if not context_sections:
            return Response(
                {"error": "Is classroom ke liye abhi enough context nahi hai (no messages/whiteboard/transcript)"},
                status=400,
            )

        context_text = "\n\n---\n\n".join(context_sections)

        try:
            answer = generate_classroom_answer(question, context_text, conversation_id)
        except Exception as e:
            logger.exception(f"Classroom copilot failed user={request.user.id} conv={conversation_id} err={e}")
            return Response({"error": "Copilot temporarily unavailable"}, status=500)

        return Response({"answer": answer}, status=200)


# ==========================================================================
# 🔥 NAYA — FEATURE 5: Revision Deck (auto flashcards + quiz for exam prep)
# --------------------------------------------------------------------------
# Context assembly is the SAME 3-source pattern as `ClassroomCopilotView`
# above (recent chat + whiteboard + transcript), just without the
# question-keyword filtering step — revision deck wants BROAD coverage of
# the class, not a targeted answer to one question. Result is persisted
# in `RevisionDeck` (unlike plain summary/quiz) so the student can revisit
# it before an exam without regenerating or losing it.
# ==========================================================================
class RevisionDeckView(APIView):
    """
    POST /message/study-room/<conversation_id>/revision-deck/
    Body: {"board_content": "...", "session_id": "..."}  (both optional)
      - `board_content`: current whiteboard text, client-collected via
        the same `_collectBoardTextContent()` study_room_screen.dart
        already uses for summary/quiz/copilot.
      - `session_id`: if given, only that session's transcript segments
        are used (revise THIS class). If omitted, the most recent
        session's segments are used (revise the LAST class — the common
        case, per `ai_study_service.dart`'s `searchTranscript` comment:
        "revision ke liye usually last class hi chahiye hota hai").
    Response: {"flashcards": [...], "quiz": [...], "created_at": "..."}

    GET /message/study-room/<conversation_id>/revision-deck/
    Returns the most recently generated deck for this conversation
    without calling Gemini again — lets the student open and revise
    anytime, offline-friendly (frontend can cache this response same as
    `MessageCacheService` does for messages).
    Response: same shape, or {"flashcards": [], "quiz": []} if none yet.

    GET /message/study-room/<conversation_id>/revision-deck/?history=true
    🔧 GAP FIX (this session) — `RevisionDeck` was already designed to
    keep history (its own model docstring: "har naya 'Generate' tap ek
    naya row banata hai ... purana deck bhi kaam aa sakta hai"), but the
    only read path ever exposed was "latest deck" — there was no way for
    a student to actually get back to an older deck once a newer one
    existed; it was written but permanently unreachable. `?history=true`
    lists every deck for this conversation (newest first), lightweight
    (no full flashcards/quiz payload — just enough to pick one), each
    with an `id` a client can use with `?deck_id=<id>` below to fetch or
    delete that specific one.
    Response: {"decks": [{"id", "session_id", "flashcard_count",
                           "quiz_count", "created_at", "generated_by"}, ...]}

    GET /message/study-room/<conversation_id>/revision-deck/?deck_id=<id>
    Fetch one specific (not necessarily latest) deck's full content —
    used after picking one from the `?history=true` list above.

    🔧 GAP FIX (G-7 — cache-hit still inserted a duplicate row) —
    `generate_revision_deck()` already has its own 24h content-hash
    cache (same pattern as summary/quiz/reply-suggestions, see
    `SmartReplySuggestionsView` docstring above), so a cache-hit means
    Gemini isn't re-called — but `post()` used to unconditionally
    `RevisionDeck.objects.create(...)` afterwards anyway, so tapping
    "Generate" twice in a row on an unchanged class (no new messages/
    board/transcript since the last deck) silently produced two
    identical `RevisionDeck` rows.

    The model's own "keep history" design (see its docstring — every
    *meaningfully new* Generate tap is meant to keep the old deck
    around too) is intentional and preserved here; what's fixed is
    specifically the *duplicate-of-unchanged-content* case. `post()`
    now hashes the same `content` string that's fed to Gemini
    (`content_hash`, sha256) and, if a `RevisionDeck` with that exact
    hash already exists for this `conversation_id` (+ `session_id`)
    within the last 24h (same TTL as the Gemini-side cache, so the two
    windows never disagree), that existing row is returned as-is
    (200, no new row, no Gemini call at all — content is byte-identical
    to what a cache-hit would've regenerated anyway). Only genuinely
    new content (new messages/board/transcript since last generate)
    produces a fresh row (201), same as before.

    `content_hash` (CharField, db_index=True) now exists on `RevisionDeck`
    in `models.py`, plus a composite index matching this exact lookup
    (`conversation`, `session_id`, `content_hash`, `-created_at`) — run
    `makemigrations`/`migrate` for `message` after pulling this. `content`
    itself is deliberately NOT persisted (same reasoning `ai_service.py`
    already applies to its own cache key), just its hash.

    DELETE /message/study-room/<conversation_id>/revision-deck/?deck_id=<id>
    🔧 GAP FIX (this session) — no way to remove a deck existed at all
    (e.g. one generated too early, before enough of the class had
    happened, that's now just clutter above the useful ones). Restricted
    to whoever generated that specific deck — a shared study room can
    have several members generating decks off the same class, and one
    member's "clean up my attempt" shouldn't be able to delete another
    member's.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [RevisionDeckThrottle]

    CONTEXT_MESSAGE_COUNT = 50
    MAX_TRANSCRIPT_SEGMENTS = 80

    def _check_member(self, request, conversation_id):
        return ConversationParticipant.objects.filter(
            conversation_id=conversation_id, user=request.user, left_at__isnull=True,
        ).exists()

    def get(self, request, conversation_id):
        if not self._check_member(request, conversation_id):
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        if str(request.query_params.get('history', '')).lower() == 'true':
            decks = (
                RevisionDeck.objects.filter(conversation_id=conversation_id)
                .select_related('generated_by')
                .order_by('-created_at')[:50]  # bounded — a revision-deck picker list, not an infinite archive
            )
            return Response({
                "decks": [
                    {
                        "id": str(deck.id),
                        "session_id": deck.session_id,
                        "flashcard_count": len(deck.flashcards or []),
                        "quiz_count": len(deck.quiz or []),
                        "created_at": deck.created_at.isoformat(),
                        "generated_by": str(deck.generated_by_id) if deck.generated_by_id else None,
                    }
                    for deck in decks
                ]
            })

        deck_id = request.query_params.get('deck_id')
        if deck_id:
            deck = RevisionDeck.objects.filter(id=deck_id, conversation_id=conversation_id).first()
            if not deck:
                return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        else:
            deck = (
                RevisionDeck.objects.filter(conversation_id=conversation_id)
                .order_by('-created_at')
                .first()
            )
        if not deck:
            return Response({"flashcards": [], "quiz": [], "created_at": None})

        return Response({
            "id": str(deck.id),
            "flashcards": deck.flashcards,
            "quiz": deck.quiz,
            "created_at": deck.created_at.isoformat(),
        })

    def delete(self, request, conversation_id):
        if not self._check_member(request, conversation_id):
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        deck_id = request.query_params.get('deck_id')
        if not deck_id:
            return Response({"error": "'deck_id' query param required hai"}, status=status.HTTP_400_BAD_REQUEST)

        deck = RevisionDeck.objects.filter(id=deck_id, conversation_id=conversation_id).first()
        if not deck:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        if deck.generated_by_id != request.user.id:
            raise PermissionDenied('Sirf jisne ye deck generate kiya tha wahi ise delete kar sakta hai.')

        deck.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def post(self, request, conversation_id):
        if not AI_ENABLED:
            return Response({"error": "AI service not configured on server"}, status=503)

        is_member = ConversationParticipant.objects.filter(
            conversation_id=conversation_id, user=request.user, left_at__isnull=True,
        ).exists()
        if not is_member:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        board_content = (request.data.get("board_content") or "").strip()
        session_id = (request.data.get("session_id") or "").strip()

        context_sections = []

        # --- 1. Recent chat messages ---
        messages = list(
            Message.objects.filter(conversation_id=conversation_id, type='text')
            .exclude(text__isnull=True).exclude(text='')
            .exclude(deleted_for_everyone=True).exclude(deleted_for_users=request.user)
            .exclude(is_scheduled=True)
            .select_related('sender').order_by('-created_at')[:self.CONTEXT_MESSAGE_COUNT]
        )
        if messages:
            messages.reverse()
            lines = [f"{(m.sender.first_name or 'Someone')}: {m.text.strip()}" for m in messages]
            context_sections.append("Recent chat messages:\n" + "\n".join(lines))

        # --- 2. Whiteboard content (client-supplied) ---
        if board_content:
            context_sections.append("Whiteboard notes:\n" + board_content[:3000])

        # --- 3. Transcript segments — specific session if given, else the
        # most recent session for this conversation ---
        transcript_qs = ClassTranscriptSegment.objects.filter(
            conversation_id=conversation_id, status=ClassTranscriptSegment.STATUS_DONE,
        )
        if not session_id:
            latest = transcript_qs.order_by('-created_at').first()
            session_id = latest.session_id if latest else ''
        segments = []
        if session_id:
            segments = list(
                transcript_qs.filter(session_id=session_id)
                .order_by('start_offset_seconds')[: self.MAX_TRANSCRIPT_SEGMENTS]
            )
        if segments:
            lines = [
                f"[{int(s.start_offset_seconds // 60)}:{int(s.start_offset_seconds % 60):02d}] {s.text.strip()}"
                for s in segments if s.text.strip()
            ]
            if lines:
                context_sections.append("Class transcript:\n" + "\n".join(lines))

        if not context_sections:
            return Response(
                {"error": "Is class ke liye abhi enough material nahi hai (no messages/whiteboard/transcript)"},
                status=400,
            )

        content = "\n\n---\n\n".join(context_sections)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # 🔧 GAP FIX (G-7) — same content (nothing new since last
        # generate) within the same 24h window `generate_revision_deck`
        # itself caches on -> return that existing deck instead of
        # inserting a duplicate row (or calling Gemini again).
        existing = (
            RevisionDeck.objects.filter(
                conversation_id=conversation_id,
                session_id=session_id,
                content_hash=content_hash,
                created_at__gte=timezone.now() - timedelta(hours=24),
            )
            .order_by('-created_at')
            .first()
        )
        if existing:
            return Response({
                "id": str(existing.id),
                "flashcards": existing.flashcards,
                "quiz": existing.quiz,
                "created_at": existing.created_at.isoformat(),
            }, status=200)

        try:
            deck = generate_revision_deck(content)
        except Exception as e:
            logger.exception(f"Revision deck failed user={request.user.id} conv={conversation_id} err={e}")
            return Response({"error": "AI temporarily unavailable, try again"}, status=500)

        saved = RevisionDeck.objects.create(
            conversation_id=conversation_id,
            session_id=session_id,
            content_hash=content_hash,
            flashcards=deck["flashcards"],
            quiz=deck["quiz"],
            generated_by=request.user,
        )

        return Response({
            "id": str(saved.id),
            "flashcards": saved.flashcards,
            "quiz": saved.quiz,
            "created_at": saved.created_at.isoformat(),
        }, status=201)