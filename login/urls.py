# login/urls.py
from django.urls import path
from .token_views import SessionTokenRefreshView  # L1 gap 1: refuses revoked sessions
from .session_views import LogoutView, SessionListView, SessionDetailView, LogoutAllView  # L1 gaps 10-11
from .ws_ticket_views import WsTicketView  # X6b -- one-time WebSocket ticket (token never in a ws URL)
from .security_views import (  # S3 -- security centre
    SecurityActivityView,
    SecurityOverviewView,
    TwoFactorBackupCodesView,
    TwoFactorDisableView,
    TwoFactorEnableView,
    TwoFactorSetupView,
    TwoFactorVerifyView,
)

from .data_rights_views import (  # S5 -- DPDP data rights
    ConsentView,
    DataExportDownloadView,
    DataExportView,
    DataRightsOverviewView,
    DeletionCancelView,
    DeletionStatusView,
)

from .connected_views import (  # S10 -- connected accounts & devices
    ConnectionsOverviewView,
    ContactConfirmView,
    ContactSendCodeView,
    GoogleLinkView,
    GoogleUnlinkView,
    LinkedAppDetailView,
    LinkedAppsView,
    PhoneRemoveView,
    QRAnswerView,
    QRCreateView,
    QRLookupView,
    QRPollView,
    ReauthSendCodeView,
    ReauthView,
)

from .views import *

urlpatterns = [
    path("", Login.as_view(), name="Login"),
    path("signup/", Signup.as_view(), name="signup"),
    path('auth/send-otp/', SendOTPView.as_view(), name='send_otp'),
    path('auth/verify-otp/', VerifyOTPView.as_view(), name='verify_otp'),
    path('auth/captcha/', CaptchaChallengeView.as_view(), name='captcha'),  # L1 gap 18
    path("auth/change-password/", ChangePasswordAPIView.as_view(), name="change-password"),
    path("auth/delete-account/", DeleteAccountView.as_view(), name="delete-account"),  # T0.8
    path("auth/google/", GoogleAuthView.as_view(), name="google-auth"),
    path("auth/complete-profile/", CompleteProfileView.as_view(), name="complete-profile"),
    path("auth/set-dob/", SetDateOfBirthView.as_view(), name="set-dob"),

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
    path("auth/token/refresh/", SessionTokenRefreshView.as_view(), name="token_refresh"),

    # L1 gap 10 -- server-side logout (ends the session, blacklists the refresh token).
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/ws-ticket/", WsTicketView.as_view(), name="ws-ticket"),  # X6b
    # L1 gap 11 -- device list, sign one device out, sign out everywhere.
    path("auth/sessions/", SessionListView.as_view(), name="session-list"),
    path("auth/sessions/<int:pk>/", SessionDetailView.as_view(), name="session-detail"),
    path("auth/logout-all/", LogoutAllView.as_view(), name="logout-all"),

    # S3 -- security centre (login/security_views.py)
    path("auth/security/overview/", SecurityOverviewView.as_view(), name="security-overview"),
    path("auth/security/activity/", SecurityActivityView.as_view(), name="security-activity"),
    path("auth/2fa/setup/", TwoFactorSetupView.as_view(), name="2fa-setup"),
    path("auth/2fa/enable/", TwoFactorEnableView.as_view(), name="2fa-enable"),
    path("auth/2fa/disable/", TwoFactorDisableView.as_view(), name="2fa-disable"),
    path("auth/2fa/backup-codes/", TwoFactorBackupCodesView.as_view(), name="2fa-backup-codes"),
    path("auth/2fa/verify/", TwoFactorVerifyView.as_view(), name="2fa-verify"),

    # S5 -- data rights (login/data_rights_views.py). Scheduling a deletion = POST auth/delete-account/ above.
    path("auth/data/overview/", DataRightsOverviewView.as_view(), name="data-rights-overview"),
    path("auth/data/export/", DataExportView.as_view(), name="data-export"),
    path("auth/data/export/<int:pk>/download/", DataExportDownloadView.as_view(), name="data-export-download"),
    path("auth/data/deletion/", DeletionStatusView.as_view(), name="data-deletion-status"),
    path("auth/data/deletion/cancel/", DeletionCancelView.as_view(), name="data-deletion-cancel"),
    path("auth/data/consents/", ConsentView.as_view(), name="data-consents"),

    # S10 -- connected accounts & devices (login/connected_views.py)
    path("auth/connections/", ConnectionsOverviewView.as_view(), name="connections-overview"),
    path("auth/connections/reauth/send-code/", ReauthSendCodeView.as_view(), name="connections-reauth-send"),
    path("auth/connections/reauth/", ReauthView.as_view(), name="connections-reauth"),
    path("auth/connections/google/link/", GoogleLinkView.as_view(), name="connections-google-link"),
    path("auth/connections/google/unlink/", GoogleUnlinkView.as_view(), name="connections-google-unlink"),
    path("auth/connections/contact/send-code/", ContactSendCodeView.as_view(), name="connections-contact-send"),
    path("auth/connections/contact/confirm/", ContactConfirmView.as_view(), name="connections-contact-confirm"),
    path("auth/connections/phone/remove/", PhoneRemoveView.as_view(), name="connections-phone-remove"),
    path("auth/connections/apps/", LinkedAppsView.as_view(), name="connections-apps"),
    path("auth/connections/apps/<int:pk>/", LinkedAppDetailView.as_view(), name="connections-app-detail"),
    path("auth/qr/create/", QRCreateView.as_view(), name="qr-create"),
    path("auth/qr/poll/", QRPollView.as_view(), name="qr-poll"),
    path("auth/qr/lookup/", QRLookupView.as_view(), name="qr-lookup"),
    path("auth/qr/answer/", QRAnswerView.as_view(), name="qr-answer"),
]

# ==============================================================================
# ⚠️ CONFIRM KARO — is app ka top-level project urls.py mein `include()`
# kis prefix ke peeche hai (e.g. `path('login/', include('login.urls'))`?
# ya root pe direct?). Us prefix + upar wale path ko jodke hi Flutter side
# ka `_refreshEndpoint` (auth_service.dart) ka poora URL banta hai — agar
# prefix `/login/` hai to poora path `/login/auth/token/refresh/` hoga,
# agar koi prefix nahi hai to `/auth/token/refresh/`.
# ==============================================================================