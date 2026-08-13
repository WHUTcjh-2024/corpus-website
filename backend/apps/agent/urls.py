from django.urls import path

from . import views


app_name = "agent"

urlpatterns = [
    path("runs/", views.AgentRunListCreateView.as_view(), name="run-list"),
    path("runs/<uuid:pk>/", views.AgentRunDetailView.as_view(), name="run-detail"),
    path("runs/<uuid:pk>/approve/", views.AgentRunApprovalView.as_view(), name="run-approve"),
    path("runs/<uuid:pk>/cancel/", views.AgentRunCancelView.as_view(), name="run-cancel"),
]
