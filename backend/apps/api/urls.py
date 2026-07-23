from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("health/", views.health, name="health"),
    path("csrf/", views.CsrfView.as_view(), name="csrf"),
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("session/", views.SessionView.as_view(), name="session"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("corpora/", views.CorpusListView.as_view(), name="corpora-list"),
    path("corpora/<uuid:pk>/", views.CorpusDetailView.as_view(), name="corpora-detail"),
]
