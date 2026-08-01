# message/ai_service.py
import os, json, hashlib, logging
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

try:
    from google import genai
    _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    _MODEL = "gemini-2.0-flash"
    AI_ENABLED = True
except Exception as e:
    logger.error(f"Gemini init failed: {e}")
    _client = None
    AI_ENABLED = False

CACHE_TTL = 60*60*24 # 24 ghante same board ka result cache rahega - API call bachega

def _get_cache_key(mode: str, content: str) -> str:
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    return f"study_ai:{mode}:{h}"

def generate_summary(content: str) -> str:
    if not AI_ENABLED:
        raise RuntimeError("AI not configured")

    key = _get_cache_key("summary", content)
    if cached := cache.get(key):
        logger.info(f"CACHE HIT summary {key}")
        return cached

    prompt = f"""
    You are an expert tutor. Summarize the following whiteboard notes.
    Rules: Use bullet points, keep under 300 words, same language as input.
    Content:
    ---
    {content[:7000]}
    ---
    """

    res = _client.models.generate_content(model=_MODEL, contents=prompt)
    result = res.text.strip()

    cache.set(key, result, CACHE_TTL)
    return result

def generate_quiz(content: str) -> list:
    if not AI_ENABLED:
        raise RuntimeError("AI not configured")

    key = _get_cache_key("quiz", content)
    if cached := cache.get(key):
        logger.info(f"CACHE HIT quiz {key}")
        return cached

    prompt = f"""
    Generate 5 MCQs from content. Return ONLY valid JSON, no markdown.
    Format: {{"questions": [{{"question": "...", "options": ["A","B","C","D"], "answer": "A"}}]}}
    Content:
    ---
    {content[:7000]}
    ---
    """

    res = _client.models.generate_content(model=_MODEL, contents=prompt)
    text = res.text.replace("```json","").replace("```","").strip()
    data = json.loads(text)
    questions = data.get("questions", [])

    if not questions:
        raise ValueError("AI returned empty quiz")

    cache.set(key, questions, CACHE_TTL)
    return questions