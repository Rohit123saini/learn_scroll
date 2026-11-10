"""S2 / R14 — account-level sync of Appearance + Data-storage preferences.

Pehle yeh do groups sirf phone (SharedPreferences) me rehte the, isliye naye phone / doosre device par alag dikhte the.
Ab `UserPreference.appearance` aur `UserPreference.data_storage` (JSON) me server par bhi save hote hain.

Kya sync hota hai
  appearance    amoled, accent, high_contrast, bold_text, reduce_motion, live_captions, hinglish
  data_storage  data_saver, call_data_saver, video_quality, autoplay_rule, preload_rule, photo_rule,
                upload_photo, upload_video

Kya JAAN-BOOJH kar sync NAHI hota (phone ke hardware / plan se jude hain): haptics, Lite mode, low-end override.
(Theme + text size + language pehle se `UserPreference` ke apne columns me sync hote hain.)

Rules
  * PATCH me sirf badli hui keys aati hain; server unhe stored dict me MERGE karta hai (poora dict replace nahi),
    taaki do device alag-alag keys badlein to ek doosre ko overwrite na karein.
  * Unknown key / galat type / galat value => 400, errors per-key: {"appearance": {"accent": ["..."]}}.
  * Server sirf wahi keys store karta hai jo user ne kabhi set ki; khaali dict = "kabhi sync nahi hua" (client apni
    local values upar bhej deta hai, ek baar).
"""
from __future__ import annotations

from typing import Any, Dict

BOOL = "bool"

# key -> BOOL ya allowed-values tuple (strings).
APPEARANCE_SCHEMA: Dict[str, Any] = {
    "amoled": BOOL,
    "accent": ("violet", "blue", "teal", "green", "orange", "pink"),
    "high_contrast": BOOL,
    "bold_text": BOOL,
    "reduce_motion": BOOL,
    "live_captions": BOOL,
    "hinglish": BOOL,
}

DATA_STORAGE_SCHEMA: Dict[str, Any] = {
    "data_saver": BOOL,
    "call_data_saver": BOOL,
    "video_quality": ("auto", "low", "high"),
    "autoplay_rule": ("always", "wifi_only", "never"),
    "preload_rule": ("always", "wifi_only", "never"),
    # Photos ke liye "never" ka matlab nahi banta (koi tasveer hi nahi dikhegi) — client use wifi_only me badalta hai.
    "photo_rule": ("always", "wifi_only"),
    "upload_photo": ("auto", "data_saver", "high"),
    "upload_video": ("auto", "data_saver", "high"),
}

# UserPreference field name -> schema. Serializer / tests isi se chalte hain.
SYNC_FIELDS: Dict[str, Dict[str, Any]] = {
    "appearance": APPEARANCE_SCHEMA,
    "data_storage": DATA_STORAGE_SCHEMA,
}


class PrefSyncError(ValueError):
    """`errors` = {key: "message"} (per-key) taaki serializer nested error bana sake."""

    def __init__(self, errors: Dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def normalize_partial(value: Any, schema: Dict[str, Any]) -> Dict[str, Any]:
    """Client ka partial dict check karo. Saaf (validated) dict lautao; kuch galat ho to PrefSyncError.

    `None` value allowed NAHI hai: reset ka matlab default value ko explicitly bhejna hai (taaki doosre device ko
    \"reset hua\" aur \"kabhi set nahi hua\" me farak dikhe).
    """
    if not isinstance(value, dict):
        raise PrefSyncError({"_": "Ek object chahiye, jaise {\"key\": value}."})
    errors: Dict[str, str] = {}
    out: Dict[str, Any] = {}
    for key, raw in value.items():
        rule = schema.get(key)
        if rule is None:
            errors[str(key)] = "Unknown setting."
            continue
        if rule == BOOL:
            if isinstance(raw, bool):
                out[key] = raw
            else:
                errors[key] = "true ya false chahiye."
            continue
        if isinstance(raw, str) and raw in rule:
            out[key] = raw
        else:
            errors[key] = f"Allowed: {', '.join(rule)}."
    if errors:
        raise PrefSyncError(errors)
    return out


def merge_stored(current: Any, patch: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Stored dict + validated patch => naya dict. Stored me purani/ab-hata di gayi key ho to girā dete hain
    (schema se bahar ki kachra jama nahi hoti)."""
    base = {k: v for k, v in (current or {}).items() if k in schema} if isinstance(current, dict) else {}
    base.update(patch)
    return base
