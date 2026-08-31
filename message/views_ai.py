# message/views_ai.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import UserRateThrottle
from .ai_service import generate_summary, generate_quiz, transcribe_audio, generate_reply_suggestions, AI_ENABLED
from .models import Message, ConversationParticipant

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
            suggestions = generate_reply_suggestions(context_text)
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