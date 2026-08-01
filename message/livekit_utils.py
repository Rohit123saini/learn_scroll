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


def generate_livekit_token(room_name: str, user_id, user_name: str) -> str:
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
        .with_ttl(timedelta(hours=2))  # 2 ghante ka token
    )
    return token.to_jwt()