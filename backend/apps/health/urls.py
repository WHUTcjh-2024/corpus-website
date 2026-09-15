from django.urls import path

from .views import copyright_notice, healthz, metrics, privacy_policy, readyz, user_agreement


urlpatterns = [
    path("privacy/", privacy_policy, name="privacy_policy"),
    path("terms/", user_agreement, name="user_agreement"),
    path("copyright/", copyright_notice, name="copyright_notice"),
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path("metrics", metrics, name="metrics"),
]
