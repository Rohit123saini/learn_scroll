# message/voice_meta.py
#
# 🔥 NAYA (M4a) — voice-note waveform. Voice metadata (`duration_seconds`,
# `transcript`, ...) pehle se `Message.meta` (JSONField) me rehta hai, isliye
# waveform bhi wahin `meta['waveform']` me jaata hai — koi migration nahi.
#
# Waveform sirf cosmetic hai, isliye galat/ajeeb data pe message REJECT nahi
# hota; bas waveform key hata di jaati hai (ya 64 tak resample ho jaati hai).
# REST (`MessageCreateSerializer`) aur WebSocket (`ChatConsumer`) dono isi
# ek function se guzarte hain, taaki dono paths same rule follow karein.

MAX_WAVEFORM_BARS = 64
WAVEFORM_MAX_VALUE = 100  # har bar 0..100 (relative loudness, percent)


def sanitize_voice_meta(meta, message_type):
    """`meta` dict ko clean karke (naya dict) return karta hai.

    - audio ke alawa kisi message type me `waveform` hata deta hai.
    - audio me: list of numbers -> int, 0..100 clamp, >64 ho to evenly 64 tak
      resample; khali/invalid ho to key hata deta hai.
    """
    if not isinstance(meta, dict):
        return meta
    if 'waveform' not in meta:
        return meta
    cleaned = dict(meta)
    raw = cleaned.pop('waveform')
    if message_type != 'audio' or not isinstance(raw, (list, tuple)) or not raw:
        return cleaned
    values = []
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return cleaned  # ek bhi galat value -> poora waveform skip
        values.append(max(0, min(WAVEFORM_MAX_VALUE, int(round(v)))))
    if len(values) > MAX_WAVEFORM_BARS:
        n = len(values)
        values = [values[i * n // MAX_WAVEFORM_BARS] for i in range(MAX_WAVEFORM_BARS)]
    cleaned['waveform'] = values
    return cleaned
