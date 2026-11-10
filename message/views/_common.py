"""
message/views/_common.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import os
from django.contrib.auth import get_user_model
from django.db.models import Prefetch, Q
from rest_framework.pagination import PageNumberPagination
from ..models import BlockedUser, Conversation, ConversationParticipant, MessageStatus


# 🔥 C4 — single source: livekit_utils.LIVEKIT_WS_URL (LAN-IP default hata diya;
# production me wss:// zaroori, DEBUG me hi local fallback).
from ..livekit_utils import LIVEKIT_WS_URL  # noqa: E402,F401


User = get_user_model()


# 🔥 NAYA — block-enforcement helper. Pehle `BlockedUser` sirf `start_private`
# (nayi conversation banate waqt) check hota tha — EXISTING conversation me
# message bhejte/call karte waqt kahin bhi check nahi tha, isliye block karne
# ke baad bhi dusra user normally message/call kar sakta tha. Ab is helper ko
# `ConversationViewSet.messages` aur `CallInitiateView.post` dono jagah use
# karte hain. Sirf 1-1 (private) conversations ke liye relevant hai — group
# me user-level block ka concept hi nahi hai, isliye caller khud `conversation
# .type != group` check karke hi ye function bulaye.
def is_blocked_pair(user_a_id, user_b_id):
    return BlockedUser.objects.filter(
        Q(blocker_id=user_a_id, blocked_id=user_b_id) |
        Q(blocker_id=user_b_id, blocked_id=user_a_id)
    ).exists()


# 🔧 FIX — `add_or_reactivate_participant` used to be defined here AND in
# `services.py` verbatim (two copies of the same "re-add a user whose
# `left_at` is still set" rule that could silently drift). Now imported
# from `.services` above (single source) — see that module for the full
# rationale (`get_or_create` alone doesn't reset `left_at`, so every
# membership check filtering on `left_at__isnull=True` would keep a
# re-added user silently locked out otherwise).


class MessagePagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = 'page_size'
    max_page_size = 100


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50


# 🔥 NAYA — PERF FIX. `MessageSerializer.get_is_starred`/`get_is_read_by_me` pehle
# HAR message row ke liye alag DB query karte the (`obj.starred_by.filter(...)
# .exists()` / `MessageStatus.objects.filter(...).exists()`) — 30-message page pe
# ye 2 extra query-per-row = 60 extra queries ek single list-load ke liye.
#
# Fix: is user ke liye already-narrowed `Prefetch` — poori list ke liye sirf 2
# EXTRA queries total (starred_by ke liye 1, delivery_status ke liye 1), row-count
# se independent. Serializer `obj.my_star.exists()`/`obj.my_read_status` (list,
# already Python-side filtered by prefetch) check karta hai instead of naya query
# maarne ke — `to_attr` isi liye diya hai taaki `.all()` cache use ho, `.filter()`
# nahi (jo prefetch cache ko bypass kar deta hai).
#
# Har jagah use karo jahan `MessageSerializer(qs_or_page, many=True, ...)` call
# hoti hai (list ke liye) — single-object responses (`MessageSerializer(message,
# ...)`) ko iski zaroorat nahi, wahan 1 query already theek hai.
def with_message_list_prefetch(queryset, user):
    return queryset.prefetch_related(
        Prefetch('starred_by', queryset=User.objects.filter(id=user.id), to_attr='my_star'),
        Prefetch(
            'delivery_status',
            queryset=MessageStatus.objects.filter(user=user, is_read=True),
            to_attr='my_read_status',
        ),
    )


# ======================================================================
# 🔥 NAYA (M1-BE) — MESSAGE REQUESTS
# ----------------------------------------------------------------------
#   GET  /message/requests/                       -> pending requests (paginated)
#   POST /message/requests/<conversation_id>/accept/   -> inbox me shift
#   POST /message/requests/<conversation_id>/decline/  -> hide; sender ko kuch nahi
#
# Rules/state-machine: `message_requests.py`. Decline pe sender ko koi
# event/push/system-message NAHI jaata — sirf receiver ke apne devices ko
# `message_request_resolved` inbox event milta hai.
# ======================================================================
def _conversation_list_qs_for(user):
    """Same shape/prefetch jo `ConversationViewSet.get_queryset` use karta hai."""
    return Conversation.objects.select_related('group_detail', 'last_message_sender').prefetch_related(
        Prefetch(
            'memberships',
            queryset=ConversationParticipant.objects.filter(user=user),
            to_attr='my_membership_list',
        )
    )
