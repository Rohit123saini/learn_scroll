
from django.urls import path
from .views import *

urlpatterns = [
    path("", Login.as_view(), name="Login"),
    path("signup/",Signup.as_view(),name="signup"),
    path('auth/send-otp/', SendOTPView.as_view(), name='send_otp'),
    path('auth/verify-otp/', VerifyOTPView.as_view(), name='verify_otp'),
    path("auth/change-password/",ChangePasswordAPIView.as_view(),name="change-password"),
    path("auth/google/", GoogleAuthView.as_view(), name="google-auth"),
    path("auth/complete-profile/", CompleteProfileView.as_view(), name="complete-profile"),
]