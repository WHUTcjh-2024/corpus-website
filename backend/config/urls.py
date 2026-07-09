from django.contrib import admin
from django.urls import include, path

from apps.health.views import home


urlpatterns = [
    path("", home, name="home"),
    path("", include("apps.health.urls")),
    path("admin/", admin.site.urls),
]
