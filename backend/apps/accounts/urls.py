from django.urls import path

from . import views


app_name = "accounts"

urlpatterns = [
    path("apply/", views.apply, name="apply"),
    path("application-submitted/", views.application_submitted, name="application_submitted"),
    path("login/", views.AccountLoginView.as_view(), name="login"),
    path("logout/", views.AccountLogoutView.as_view(), name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("quota/request/", views.quota_request, name="quota_request"),
]
