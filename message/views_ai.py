# message/views_ai.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import UserRateThrottle
from.ai_service import generate_summary, generate_quiz, AI_ENABLED

logger = logging.getLogger(__name__)

class AiStudyThrottle(UserRateThrottle):
    rate = '20/min' # 1 user 1 min me 20 se zyada AI call nahi maar sakta
    scope = 'ai_study'

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