# message/urls.py
"""
NOTE FROM THIS REBUILD:
    Yeh file poori tarah reconstruct nahi ho payi hai kyunki `message` app
    ka core `views.py` (jisme Conversation/Message ViewSets hongi) abhi
    tak upload nahi hui — jo files milin unme baar-baar `middleware.py`
    (JWTAuthMiddleware) ya `liveclass/views.py` galti se aa gayi.

    Jo cheez CONFIRM ho chuki hai aur isme daal di gayi hai:
        - AiStudyRoomView (message/views_ai.py) -> POST /ai-study-room/

    Jo abhi TODO hai (neeche saaf marked hai):
        - Conversation list/detail
        - Message list/create/send
        - DeviceToken registration (push ke liye, notifications.py me
          reference hai message.push_utils.send_push_to_users ka, jiska
          matlab kahin DeviceToken register karne ka endpoint zaroor
          hoga)

    Jab asli `message/views.py` mil jaye, TODO wale section me sirf apne
    real ViewSet/View class names daal dena — structure yeh raise-ready
    hai.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views_ai import AiStudyRoomView

# -----------------------------------------------------------------------
# TODO: yahan apne asli message/views.py se imports daalo, jaise:
# from .views import ConversationViewSet, MessageViewSet, DeviceTokenViewSet
# -----------------------------------------------------------------------

router = DefaultRouter()

# TODO: apne real ViewSets yahan register karo, jaise:
# router.register(r"conversations", ConversationViewSet, basename="conversation")
# router.register(r"messages", MessageViewSet, basename="message")
# router.register(r"device-tokens", DeviceTokenViewSet, basename="device-token")

urlpatterns = [
    path("", include(router.urls)),

    # --- CONFIRMED: AI Study Room (summary/quiz generation from board content) ---
    path("ai-study-room/", AiStudyRoomView.as_view(), name="ai-study-room"),

    # TODO: koi bhi plain APIView (router se auto-wire nahi hoti) yahan
    # manually add karo, jaise teacher-earnings ko liveclass/urls.py me
    # kiya gaya tha:
    # path("some-plain-view/", SomePlainView.as_view(), name="some-plain-view"),
]