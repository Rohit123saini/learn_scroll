# message/ai_service.py
import os, json, hashlib, logging
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

try:
    from google import genai
    _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    # 🔥 FIX — "gemini-2.0-flash" Google ne 1 June 2026 ko retire kar diya
    # (404 NOT_FOUND deta hai ab). Isse AI summary/quiz feature production
    # me chup-chaap dead pada tha, kyunki neeche wala try/except sab kuch
    # generic "AI temporarily unavailable" bana ke chhupa deta tha.
    # Ab env var se configurable hai — agli baar Google koi model retire
    # kare (gemini-2.5-flash khud Oct 16 2026 ko retire ho raha hai) to
    # sirf .env me GEMINI_MODEL change karna padega, code deploy nahi.
    _MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    AI_ENABLED = True
except Exception as e:
    logger.error(f"Gemini init failed: {e}")
    _client = None
    AI_ENABLED = False

CACHE_TTL = 60*60*24 # 24 ghante same board ka result cache rahega - API call bachega

def _get_cache_key(mode: str, content: str) -> str:
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    return f"study_ai:{mode}:{h}"

def _call_gemini(prompt: str):
    try:
        return _client.models.generate_content(model=_MODEL, contents=prompt)
    except Exception as e:
        # 🔥 NAYA — model retire/invalid hone par error CRITICAL level pe
        # log karo (500 to user ko waise bhi generic dikhega, par agar ye
        # sirf INFO/no-log rahega to production me poora AI feature months
        # tak silently dead pada reh sakta hai, jaisa gemini-2.0-flash ke
        # saath hua). Alerting/monitoring isi CRITICAL log pe hook karo.
        logger.critical(f"Gemini call failed (model={_MODEL}): {e}")
        raise


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

    res = _call_gemini(prompt)
    result = res.text.strip()

    cache.set(key, result, CACHE_TTL)
    return result

def transcribe_audio(file_url: str, mime_type: str = "audio/ogg") -> str:
    """
    🔥 NAYA — Voice-message transcription. Roadmap me "cheap win" the tha
    kyunki Gemini client already wired hai upar — sirf audio-input call
    add karna tha.

    `file_url` chat me already-uploaded voice message ka URL hai
    (`upload_view.py` se). Gemini ko file bytes chahiye, URL nahi, isliye
    pehle download karte hain — production me agar file size/latency
    concern ho to isse ek background task (Celery) me chalao aur result
    ko `Message.meta['transcript']` me save kar do, taaki client ko baar-baar
    re-transcribe na karna pade.

    Same 24h cache pattern jo summary/quiz already use karte hain — same
    audio dobara transcribe na ho (e.g. forwarded voice message).
    """
    if not AI_ENABLED:
        raise RuntimeError("AI not configured")

    key = _get_cache_key("transcript", file_url)
    if cached := cache.get(key):
        logger.info(f"CACHE HIT transcript {key}")
        return cached

    import requests  # local import — sirf is function ke liye chahiye

    resp = requests.get(file_url, timeout=15)
    resp.raise_for_status()
    audio_bytes = resp.content

    try:
        res = _client.models.generate_content(
            model=_MODEL,
            contents=[
                "Transcribe this audio message exactly, in its original language. "
                "Return ONLY the transcript text, no preamble, no explanation.",
                {"mime_type": mime_type, "data": audio_bytes},
            ],
        )
    except Exception as e:
        logger.critical(f"Gemini transcription failed (model={_MODEL}): {e}")
        raise

    result = res.text.strip()
    if not result:
        raise ValueError("AI returned empty transcript")

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

    res = _call_gemini(prompt)
    text = res.text.replace("```json","").replace("```","").strip()
    data = json.loads(text)
    questions = data.get("questions", [])

    if not questions:
        raise ValueError("AI returned empty quiz")

    cache.set(key, questions, CACHE_TTL)
    return questions