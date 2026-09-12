# login/urls.py
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import *

urlpatterns = [
    path("", Login.as_view(), name="Login"),
    path("signup/", Signup.as_view(), name="signup"),
    path('auth/send-otp/', SendOTPView.as_view(), name='send_otp'),
    path('auth/verify-otp/', VerifyOTPView.as_view(), name='verify_otp'),
    path("auth/change-password/", ChangePasswordAPIView.as_view(), name="change-password"),
    path("auth/google/", GoogleAuthView.as_view(), name="google-auth"),
    path("auth/complete-profile/", CompleteProfileView.as_view(), name="complete-profile"),

    # 🔥 FIX (B-7) — dedicated forgot-password flow. Two steps, neither of
    # which returns a session: request a code for a known account, then
    # spend that code + a new password to actually change it. See
    # ForgotPasswordView / ResetPasswordView docstrings in views.py for why
    # this isn't just VerifyOTPView reused — that view's OTP-login branch
    # logs the user in and never touches the password, a different feature.
    path("auth/forgot-password/", ForgotPasswordView.as_view(), name="forgot-password"),
    path("auth/reset-password/", ResetPasswordView.as_view(), name="reset-password"),

    # 🔥 FIX — koi refresh-token redeem endpoint nahi tha. Tokens already
    # standard `RefreshToken.for_user()` se ban rahe the (Login/Signup/
    # GoogleAuthView/VerifyOTPView sab isi se), isliye simplejwt ka
    # built-in `TokenRefreshView` bina kisi custom logic ke kaam karega —
    # request {"refresh": "..."} -> response {"access": "..."}.
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]

# ==============================================================================
# ⚠️ CONFIRM KARO — is app ka top-level project urls.py mein `include()`
# kis prefix ke peeche hai (e.g. `path('login/', include('login.urls'))`?
# ya root pe direct?). Us prefix + upar wale path ko jodke hi Flutter side
# ka `_refreshEndpoint` (auth_service.dart) ka poora URL banta hai — agar
# prefix `/login/` hai to poora path `/login/auth/token/refresh/` hoga,
# agar koi prefix nahi hai to `/auth/token/refresh/`.
# ==============================================================================