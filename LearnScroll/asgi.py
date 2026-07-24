# # """
# # ASGI config for LearnScroll project.
# #
# # It exposes the ASGI callable as a module-level variable named ``application``.
# #
# # For more information on this file, see
# # https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
# # """
# #
# # import os
# #
# # from django.core.asgi import get_asgi_application
# # # import os
# # from django.core.asgi import get_asgi_application
# # from channels.routing import ProtocolTypeRouter, URLRouter
# # from channels.auth import AuthMiddlewareStack
# # os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'LearnScroll.settings')
# #
# # # application = get_asgi_application()
# # application = ProtocolTypeRouter({
# #     "http": get_asgi_application(),
# #     "websocket": AuthMiddlewareStack(
# #         URLRouter(
# #             # yahan tera message app ka routing ayega
# #             __import__('message.routing').routing.websocket_urlpatterns
# #         )
# #     ),
# # })
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
# import os
# import django
# from django.core.asgi import get_asgi_application
#
# os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'LearnScroll.settings')
# django.setup() # <-- Ye line sabse important hai, iske bina model import fail hoga
#
# from channels.routing import ProtocolTypeRouter, URLRouter
# from channels.auth import AuthMiddlewareStack
# from channels.security.websocket import AllowedHostsOriginValidator
#
# # Django setup ke BAAD import karna hai
# from message import routing as message_routing
#
# application = ProtocolTypeRouter({
#     "http": get_asgi_application(),
#     "websocket": AllowedHostsOriginValidator(
#         AuthMiddlewareStack(
#             URLRouter(
#                 message_routing.websocket_urlpatterns
#             )
#         )
#     ),
# })





















"""
ASGI config for LearnScroll project.

It exposes the ASGI callable as a module-level variable named ``application``.
"""

import os

import django
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'LearnScroll.settings')
django.setup()  # <-- models import se pehle zaroori hai

# 🔥 IMPORTANT: apna REST_FRAMEWORK sirf JWTAuthentication use karta hai
# (settings.py me), koi SessionAuthentication nahi. Isliye websocket pe
# `channels.auth.AuthMiddlewareStack` kaam NAHI karega — wo sirf Django
# session-cookie padhta hai, aur tera mobile/frontend client cookie nahi,
# JWT access-token bhejta hai. AuthMiddlewareStack rakhne par scope['user']
# hamesha AnonymousUser milega aur consumer connect hi reject kar dega
# (jaisa humne likha tha `is_authenticated` check).
#
# Isliye custom JWTAuthMiddleware use kar rahe hain jo query-string se
# ?token=<jwt> padh ke user verify karta hai.
from message.Middleware import JWTAuthMiddleware  # noqa: E402  (django.setup() ke baad import zaroori)
from message import routing as message_routing  # noqa: E402

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AllowedHostsOriginValidator(
        JWTAuthMiddleware(
            URLRouter(
                message_routing.websocket_urlpatterns
            )
        )
    ),
})