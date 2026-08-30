# message/livekit_utils.py - FIX
import os
from datetime import timedelta

from livekit import api

# Ab hardcoded nahi - .env / environment variables se aayega
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
    raise RuntimeError(
        "LIVEKIT_API_KEY ya LIVEKIT_API_SECRET set nahi hai. "
        ".env file check karo ya environment variables set karo."
    )


# 🔥 FIX — TTL pehle hardcoded 2 ghante tha for EVERY token, calls ke liye
# aur study-room join ke liye bhi same function use hota hai
# (`StudyRoomJoinView`). Study room ek persistent Google-Meet-jaisa room
# hai jo 2 ghante se zyada aasani se chal sakta hai — token expire hote hi
# LiveKit connection bina kisi warning ke drop ho jaata, user ko lagta app
# crash ho gaya. Ab caller (calls vs study-room) apni zaroorat ke hisaab
# se TTL pass kar sakta hai; default 2 hours calls ke liye pehle jaisa hi
# rakha hai taaki behavior na badle jahan explicitly override nahi kiya.
def generate_livekit_token(room_name: str, user_id, user_name: str, ttl: timedelta = timedelta(hours=2)) -> str:
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
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