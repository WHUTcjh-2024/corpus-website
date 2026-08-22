from django.contrib import admin
from django.urls import include, path

from apps.health.views import home


admin.site.site_header = "在线语料库平台管理后台"
admin.site.site_title = "在线语料库平台管理后台"
admin.site.index_title = "后台总览"

urlpatterns = [
    path("", home, name="home"),
    path("api/", include("apps.api.urls")),
    path("", include("apps.health.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("corpora/", include("apps.corpora.urls")),
    path("search/", include("apps.search.urls")),
    path("parallel/", include("apps.parallel.urls")),
    path("statistics/", include("apps.statistics.urls")),
    path("exports/", include("apps.exports.urls")),
    path("feedback/", include("apps.feedback.urls")),
    path("management/", include("apps.admin_portal.urls")),
    path("admin/", admin.site.urls),
]
