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

# 🔥 FIX — sentinel object, `None` se alag. `cache.get(key)` normally miss pe
# `None` deta hai, aur purana code `if cached := cache.get(key):` (truthy
# check) use karta tha — jisse agar kabhi ek EMPTY-but-valid result cache ho
# jaata (empty string / empty list), wo hamesha "miss" jaisa dikhta aur
# Gemini ko dobara-dobara call kiya jaata, 24h cache ke bawajood. Sentinel
# use karke sirf "key exists hi nahi" ko miss maana jaata hai, empty value
# ko nahi.
_CACHE_MISS = object()


def _get_cache_key(mode: str, content: str) -> str:
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    # 🔥 FIX — key me `_MODEL` bhi shamil, taaki `GEMINI_MODEL` env var
    # switch hone par (model retire/upgrade) purane model ka 24h-old cached
    # output naye model ke saath silently mix na ho. Model switch khud
    # naturally naya cache-key bana dega — koi manual cache-flush nahi
    # chahiye, aur agar kabhi debug karna pade ki "yeh output kis model ne
    # diya", key hi bata degi.
    return f"study_ai:{mode}:{_MODEL}:{h}"


def _cache_get(key: str):
    """Returns cached value, or `_CACHE_MISS` if genuinely not cached."""
    return cache.get(key, _CACHE_MISS)


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
    cached = _cache_get(key)
    if cached is not _CACHE_MISS:
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

    # 🔥 FIX — pehle koi empty-result guard nahi tha yahan (quiz/replies me
    # tha, summary me bhool gaye the). Agar Gemini kabhi whitespace-only
    # text laut deta, wo `""` cache ho jaata aur combined with the old
    # truthy-check bug, permanently "cache miss" dikhta us content-hash ke
    # liye — 24h cache silently kaam nahi karta us key ke liye. Ab dono
    # fix ho gaye: empty result cache hi nahi hota, aur agar future me
    # koi aur jagah se empty legitimately cache ho jaye to bhi sentinel
    # check use ho ke sahi tarah "hit" maana jayega.
    if not result:
        raise ValueError("AI returned empty summary")

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

    NOTE: cache key abhi bhi `file_url` ka hash hai, audio bytes ka nahi —
    forwarded voice message agar naya storage path/URL banata hai to same
    audio content dobara transcribe hoga (cache miss). Correctness bug
    nahi hai, sirf ek missed cache-hit opportunity — jaanbujh kar chhoda
    hai kyunki content-based key ke liye pehle hi download karna padega
    (cache-check ka fayda hi khatam ho jaata), trade-off worth nahi laga.
    """
    if not AI_ENABLED:
        raise RuntimeError("AI not configured")

    key = _get_cache_key("transcript", file_url)
    cached = _cache_get(key)
    if cached is not _CACHE_MISS:
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


# 🔥 NAYA — Smart-reply / quick-reply suggestions (Gmail/WhatsApp Business
# jaisa "tap-to-send" chips). Existing Gemini client + 24h content-hash
# cache pattern (upar summary/quiz/transcript) hi reuse kiya hai — koi
# naya AI wiring nahi chahiye tha.
#
# 🔥 FIX — cache key ab `conversation_id` bhi include karta hai, sirf
# `context_text` nahi. Study-room summary/quiz ke liye pure-content-hash
# cache sahi design hai (same board content = same result, intentionally
# shared cache across users). Lekin private-chat smart-reply ke liye risk
# tha: agar do ALAG conversations me by-chance byte-identical context ban
# jaaye (common first names + generic short messages jaise "ok"/"haan"),
# ek user ko doosre ke private-chat-context se generate hui suggestions
# mil sakti thi. `conversation_id` ko hash-input me daalne se ye cross-
# conversation leak ka path hi band ho jaata hai — key ab sirf tabhi match
# karega jab same conversation ho.
def generate_reply_suggestions(context_text: str, conversation_id: str) -> list:
    if not AI_ENABLED:
        raise RuntimeError("AI not configured")

    key = _get_cache_key("replies", f"{conversation_id}:{context_text}")
    cached = _cache_get(key)
    if cached is not _CACHE_MISS:
        logger.info(f"CACHE HIT replies {key}")
        return cached

    prompt = f"""
    You are helping someone quickly reply to an incoming chat message.
    Based on the recent conversation below (oldest first, "Me" is the
    person who needs to reply), suggest exactly 3 short quick-reply
    options they could tap to send as-is, with no further editing.

    Rules:
    - Each suggestion under 8 words.
    - Match the tone and language of the conversation (reply in the same
      language the conversation is in).
    - The 3 suggestions must be meaningfully different from each other,
      not just rephrasings of the same idea.
    - If the last message is a question or request, suggestions should be
      plausible direct answers, not generic filler.
    - Return ONLY valid JSON, no markdown, no explanation:
      {{"suggestions": ["...", "...", "..."]}}

    Conversation:
    ---
    {context_text[:4000]}
    ---
    """

    res = _call_gemini(prompt)
    text = res.text.replace("```json", "").replace("```", "").strip()
    data = json.loads(text)
    suggestions = [s.strip() for s in data.get("suggestions", []) if s and s.strip()][:3]

    if not suggestions:
        raise ValueError("AI returned empty suggestions")

    cache.set(key, suggestions, CACHE_TTL)
    return suggestions


# 🔥 FIX — `views_ai.py` (`ClassroomCopilotView`) already imports
# `generate_classroom_answer` from this module, but it was never actually
# defined here. That's not a "copilot is broken" bug — a missing name in
# a `from .ai_service import (...)` line raises `ImportError` at module
# load time, which takes down EVERY view in `views_ai.py` (summary/quiz,
# transcribe, smart-replies, transcript search, copilot — sab), not just
# the copilot endpoint. Adding the function fixes the import for the
# whole file.
#
# Same 24h content-hash cache pattern as `generate_reply_suggestions` —
# `conversation_id` is folded into the cache key for the same reason it
# is there (prevents a byte-identical `context_text` from two different
# conversations ever sharing a cached answer).
def generate_classroom_answer(question: str, context_text: str, conversation_id: str) -> str:
    if not AI_ENABLED:
        raise RuntimeError("AI not configured")

    key = _get_cache_key("classroom_qa", f"{conversation_id}:{question}:{context_text}")
    cached = _cache_get(key)
    if cached is not _CACHE_MISS:
        logger.info(f"CACHE HIT classroom_qa {key}")
        return cached

    prompt = f"""
    You are a helpful classroom tutor. Answer the student's question using
    ONLY the classroom context given below (recent chat, whiteboard notes,
    and/or transcript excerpts). If the context doesn't contain enough
    information to answer confidently, say so plainly instead of guessing.

    Rules:
    - Answer in the same language the student's question is in.
    - Keep it under 150 words unless the question genuinely needs more.
    - If you reference the transcript, you may mention the timestamp
      (mm:ss) if it's given in the context.

    Classroom context:
    ---
    {context_text[:6000]}
    ---

    Student's question: {question}
    """

    res = _call_gemini(prompt)
    result = res.text.strip()

    if not result:
        raise ValueError("AI returned empty answer")

    cache.set(key, result, CACHE_TTL)
    return result


# 🔥 NAYA — Revision Deck (Feature 5): summary/quiz ka agla step. Instead
# of a one-off summary or a single 5-question quiz off of whatever's on
# the board right now, this pulls together everything a student has for
# a class (whiteboard notes + recent chat + transcript excerpts, combined
# server-side by the caller in `views_ai.py`) and turns it into a
# self-contained revision pack: flashcards for quick recall + a slightly
# longer quiz for practice, both meant to be revisited later (not just
# read once) — hence `views_ai.py` persists the result in a `RevisionDeck`
# row instead of throwing it away like the plain summary/quiz responses.
#
# Same 24h content-hash cache pattern as the rest of this file — if
# nothing new was said/written since the last generation, this returns
# the cached deck instantly instead of hitting Gemini again.
def generate_revision_deck(content: str) -> dict:
    if not AI_ENABLED:
        raise RuntimeError("AI not configured")

    key = _get_cache_key("revision_deck", content)
    cached = _cache_get(key)
    if cached is not _CACHE_MISS:
        logger.info(f"CACHE HIT revision_deck {key}")
        return cached

    prompt = f"""
    You are an expert tutor preparing a student for an exam using their
    own class materials (whiteboard notes, chat discussion, and/or class
    transcript excerpts, combined below). Produce a self-revision pack.

    Rules:
    - Use the same language as the input content.
    - Produce 8-12 flashcards: short, exam-style question-answer pairs
      covering the key concepts (front = question/term, back = concise
      answer/definition, 1-2 sentences max).
    - Produce 5 MCQs testing the same material (don't just repeat the
      flashcard questions verbatim — test the concepts differently).
    - Return ONLY valid JSON, no markdown, no explanation, in this exact
      shape:
      {{
        "flashcards": [{{"front": "...", "back": "..."}}, ...],
        "quiz": [{{"question": "...", "options": ["A","B","C","D"], "answer": "A"}}, ...]
      }}

    Class materials:
    ---
    {content[:9000]}
    ---
    """

    res = _call_gemini(prompt)
    text = res.text.replace("```json", "").replace("```", "").strip()
    data = json.loads(text)

    flashcards = data.get("flashcards", [])
    quiz = data.get("quiz", [])
    if not flashcards and not quiz:
        raise ValueError("AI returned empty revision deck")

    result = {"flashcards": flashcards, "quiz": quiz}
    cache.set(key, result, CACHE_TTL)
    return result


def generate_quiz(content: str) -> list:
    if not AI_ENABLED:
        raise RuntimeError("AI not configured")

    key = _get_cache_key("quiz", content)
    cached = _cache_get(key)
    if cached is not _CACHE_MISS:
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