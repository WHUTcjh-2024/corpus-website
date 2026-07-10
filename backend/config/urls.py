from django.contrib import admin
from django.urls import include, path

from apps.health.views import home


urlpatterns = [
    path("", home, name="home"),
    path("", include("apps.health.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("corpora/", include("apps.corpora.urls")),
    path("admin/", admin.site.urls),
]
