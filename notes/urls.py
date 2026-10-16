from django.urls import path
from rest_framework.authtoken.views import obtain_auth_token

from . import views

urlpatterns = [
    path("auth/register/", views.RegisterView.as_view(), name="register"),
    path("auth/token/", obtain_auth_token, name="obtain-token"),
    path("me/", views.MeView.as_view(), name="me"),
]
