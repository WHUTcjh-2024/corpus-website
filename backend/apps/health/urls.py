from django.urls import path

from .views import healthz, metrics, readyz


urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path("metrics", metrics, name="metrics"),
]
