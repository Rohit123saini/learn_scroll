# message/livekit_utils.py - FIX
import os
from datetime import timedelta

from livekit import api

# Ab hardcoded nahi - .env / environment variables se aayega
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# 🔥 FIX (production readiness) — pehle ye check MODULE IMPORT time pe
# RuntimeError raise karta tha. `views.py` (jo `urls.py` -> Django startup
# pe hi import hoti hai) `livekit_utils` import karta hai — matlab agar
# LIVEKIT env vars set nahi hain to poora Django process boot hi nahi hota,
# CHAHE koi bhi call/study-room feature use na kar raha ho (plain text chat
# bhi crash). Ek unrelated integration ki missing config se poora app down
# ho jaana single-point-of-failure hai. Ab check LAZY hai — sirf tab fire
# hota hai jab actually koi token generate karne ki koshish ho (call
# initiate / study-room join), aur us waqt bhi clean 503-able error deta
# hai jise view apne except me pakad sakti hai, poore process ko nahi le
# jaata.
def _get_livekit_credentials():
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise RuntimeError(
            "LIVEKIT_API_KEY ya LIVEKIT_API_SECRET set nahi hai. "
            ".env file check karo ya environment variables set karo."
        )
    return LIVEKIT_API_KEY, LIVEKIT_API_SECRET


# 🔥 FIX — TTL pehle hardcoded 2 ghante tha for EVERY token, calls ke liye
# aur study-room join ke liye bhi same function use hota hai
# (`StudyRoomJoinView`). Study room ek persistent Google-Meet-jaisa room
# hai jo 2 ghante se zyada aasani se chal sakta hai — token expire hote hi
# LiveKit connection bina kisi warning ke drop ho jaata, user ko lagta app
# crash ho gaya. Ab caller (calls vs study-room) apni zaroorat ke hisaab
# se TTL pass kar sakta hai; default 2 hours calls ke liye pehle jaisa hi
# rakha hai taaki behavior na badle jahan explicitly override nahi kiya.
def generate_livekit_token(room_name: str, user_id, user_name: str, ttl: timedelta = timedelta(hours=2)) -> str:
    api_key, api_secret = _get_livekit_credentials()
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(str(user_id))
        .with_name(user_name)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_ttl(ttl)
    )
    return token.to_jwt()