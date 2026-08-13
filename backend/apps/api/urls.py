from django.urls import include, path

from . import views
from apps.audits import views as audit_views

app_name = "api"

urlpatterns = [
    path("health/", views.health, name="health"),
    path("csrf/", views.CsrfView.as_view(), name="csrf"),
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("session/", views.SessionView.as_view(), name="session"),
    path("public-corpora/", views.PublicCorpusOverviewView.as_view(), name="public-corpora"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("corpora/", views.CorpusListView.as_view(), name="corpora-list"),
    path("corpora/<uuid:pk>/", views.CorpusDetailView.as_view(), name="corpora-detail"),
    path("agent/", include("apps.agent.urls")),
    path(
        "internal/audits/<uuid:audit_id>/callback/",
        audit_views.remote_auditor_callback,
        name="auditor-callback",
    ),
]
