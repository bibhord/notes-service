from django.urls import path
from rest_framework.authtoken.views import obtain_auth_token

from . import views

urlpatterns = [
    path("auth/register/", views.RegisterView.as_view(), name="register"),
    path("auth/token/", obtain_auth_token, name="obtain-token"),
    path("me/", views.MeView.as_view(), name="me"),
    path("teams/", views.TeamListCreateView.as_view(), name="team-list"),
    path("teams/<slug:slug>/", views.TeamDetailView.as_view(), name="team-detail"),
    path(
        "teams/<slug:slug>/members/",
        views.MembershipListCreateView.as_view(),
        name="membership-list",
    ),
    path(
        "teams/<slug:slug>/members/<str:username>/",
        views.MembershipDetailView.as_view(),
        name="membership-detail",
    ),
]
