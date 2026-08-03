# message/user_display.py
#
# 🔥 NAYA — SHARED helper: "user ka naam + photo kaise dikhana hai" ye
# logic sirf EK jagah likha hai, aur teen jagah reuse hota hai:
#   1. serializers.py  -> UserMiniSerializer (REST API — chat list, group
#      members, reactions, call history, waghera)
#   2. consumers.py    -> ChatConsumer / CallConsumer (WebSocket — live
#      chat_message, incoming_call events)
#   3. views.py        -> CallInitiateView / CallActionView / StudyRoom
#      (LiveKit display name, push notification title)
#
# Duplicate karne ke bajaye ek jagah rakha hai taaki agar kal ko naam
# banane ka logic badle (e.g. nickname add ho jaaye), sirf yahan change
# karna pade — REST aur WebSocket dono automatically sync rahenge.
#
# IMPORTANT: `first_name`, `last_name`, `profile_photo` — teeno seedhe
# custom User model (AUTH_USER_MODEL) ke columns hain, koi alag Profile
# table nahi. Isliye jahan bhi `select_related('sender')` /
# `select_related('user')` / `select_related('caller')` already lagi hai,
# ye fields BINA kisi extra query ke already available hote hain — is
# helper ka use karne se koi naya N+1 query nahi banta.

from typing import Optional

from django.conf import settings


def get_display_name(user) -> str:
    """
    Priority: "First Last" (agar dono/ek set hain) -> username -> str(user).
    Kabhi khali string return nahi karta (frontend me blank name na dikhe).
    """
    if user is None:
        return ""
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    username = (getattr(user, "username", "") or "").strip()
    if username:
        return username
    return str(user)


def get_profile_photo_url(user, request=None):
    """
    ImageField se absolute URL banata hai.
      - REST request context available ho (`request.build_absolute_uri`)
        to sabse reliable — sahi scheme + host + port automatically.
      - WebSocket consumer ke paas DRF `request` nahi hota, isliye
        settings me `MEDIA_ABSOLUTE_BASE_URL` (e.g.
        "https://api.yourapp.com") set karo — us se prefix kar dete hain.
      - Wo bhi na mile to relative URL (`/media/profile/xyz.jpg`) return
        karte hain — frontend agar already base host khud prepend karta
        hai (jaisa chat me file_url ke liye common hai) to bhi kaam chalega.
    Photo hi na ho to `None` — frontend default/placeholder avatar dikhaye.
    """
    photo = getattr(user, "profile_photo", None)
    if not photo:
        return None
    try:
        url = photo.url
    except ValueError:
        # ImageField pe filename set hai lekin storage se resolve nahi ho
        # paaya (corrupt record) — crash karne ke bajaye None de do.
        return None

    if request is not None:
        return request.build_absolute_uri(url)

    base = getattr(settings, "MEDIA_ABSOLUTE_BASE_URL", None)
    if base:
        return base.rstrip("/") + url
    return url


def build_user_mini(user, request=None) -> Optional[dict]:
    """
    Ek consistent dict — REST serializer aur WebSocket payload dono isi
    shape ko follow karte hain, taaki Flutter side pe ek hi model class
    (`UserMini`) dono se parse ho sake.
    """
    if user is None:
        return None
    return {
        "id": str(user.id),
        "username": getattr(user, "username", "") or "",
        "first_name": getattr(user, "first_name", "") or "",
        "last_name": getattr(user, "last_name", "") or "",
        "display_name": get_display_name(user),
        "profile_photo": get_profile_photo_url(user, request=request),
    }