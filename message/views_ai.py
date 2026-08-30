# message/views_ai.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import UserRateThrottle
from.ai_service import generate_summary, generate_quiz, transcribe_audio, AI_ENABLED

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