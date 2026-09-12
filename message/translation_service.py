# message/translation_service.py
"""
Feature 9 — Real-time message translate.

Pluggable so the actual provider (Google Cloud Translate, Azure
Translator, on-device ML Kit via a thin server relay, etc.) can be
swapped without touching `views.py`. Default implementation below uses
Google Cloud Translate's v2 REST API (simplest — plain API key, no SDK/
service-account JSON needed), gated behind `settings.GOOGLE_TRANSLATE_API_KEY`.

If that setting is missing (not configured yet), `translate_text` raises
`TranslationServiceUnavailable` — `MessageViewSet.translate` turns that
into a clean 503 instead of a 500, so "feature not configured yet" never
looks like a server crash.
"""
import logging
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GOOGLE_TRANSLATE_ENDPOINT = 'https://translation.googleapis.com/language/translate/v2'

# Languages surfaced in the app's language picker (see
# `language_picker_sheet.dart`) — keep this list in sync with that file.
# Not an enforced allow-list server-side (Google's API accepts many more
# ISO codes) — just what the UI currently offers.
SUPPORTED_LANGUAGES = {
    'en': 'English',
    'hi': 'Hindi',
    'mr': 'Marathi',
    'ta': 'Tamil',
    'te': 'Telugu',
    'kn': 'Kannada',
    'bn': 'Bengali',
    'gu': 'Gujarati',
    'pa': 'Punjabi',
    'ur': 'Urdu',
}


class TranslationError(Exception):
    """Provider reachable but returned an error (bad lang code, etc.)."""


class TranslationServiceUnavailable(Exception):
    """Provider not configured / not reachable right now."""


class UnsupportedLanguageError(TranslationError):
    """
    `target_lang` (or `source_lang`) isn't in `SUPPORTED_LANGUAGES`.

    TASK 29 — before this, `SUPPORTED_LANGUAGES` above was purely
    documentation: the app's language picker only offered these codes,
    but the API itself would happily forward ANY ISO code straight to
    Google (which accepts far more than the 10 we advertise/support in
    the UI). That meant a client bug, a stale app build, or someone
    calling the endpoint directly with e.g. `target_lang=fr` would
    silently succeed against Google and return a "supported" response
    for a language the rest of the product (UI strings, RTL handling,
    font fallback for `language_picker_sheet.dart`, etc.) was never
    built to handle. Raising here — before the network call — turns
    that into a clean, catchable, obviously-a-client-bug error instead
    of a translation that quietly works today and breaks some other
    part of the UI.

    Kept as a `TranslationError` subclass (not a new top-level class) so
    any existing `except TranslationError` call site still catches it
    without changes; callers that want to handle "unsupported language"
    differently from "provider returned garbage" can catch this
    subclass specifically first.
    """


def translate_text(text: str, target_lang: str, source_lang: Optional[str] = None) -> str:
    target_lang = (target_lang or '').strip().lower()
    if target_lang not in SUPPORTED_LANGUAGES:
        raise UnsupportedLanguageError(
            f"'{target_lang}' abhi supported nahi hai. Supported: "
            f"{', '.join(sorted(SUPPORTED_LANGUAGES))}."
        )
    if source_lang:
        source_lang = source_lang.strip().lower()
        if source_lang not in SUPPORTED_LANGUAGES:
            raise UnsupportedLanguageError(
                f"'{source_lang}' abhi supported nahi hai. Supported: "
                f"{', '.join(sorted(SUPPORTED_LANGUAGES))}."
            )

    api_key = getattr(settings, 'GOOGLE_TRANSLATE_API_KEY', None)
    if not api_key:
        raise TranslationServiceUnavailable(
            "Translation abhi configure nahi hai (GOOGLE_TRANSLATE_API_KEY missing)."
        )

    params = {
        'key': api_key,
        'q': text,
        'target': target_lang,
        'format': 'text',
    }
    if source_lang:
        params['source'] = source_lang

    try:
        resp = requests.post(GOOGLE_TRANSLATE_ENDPOINT, data=params, timeout=8)
    except requests.RequestException as e:
        logger.warning("Translate API unreachable: %s", e)
        raise TranslationServiceUnavailable("Translation service abhi unreachable hai, thodi der baad try karo.")

    if resp.status_code != 200:
        logger.warning("Translate API error %s: %s", resp.status_code, resp.text[:300])
        raise TranslationError("Translate nahi ho paaya — language code check karo.")

    data = resp.json()
    try:
        return data['data']['translations'][0]['translatedText']
    except (KeyError, IndexError):
        raise TranslationError("Translate response samajh nahi aaya.")