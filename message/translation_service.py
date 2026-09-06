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


def translate_text(text: str, target_lang: str, source_lang: Optional[str] = None) -> str:
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