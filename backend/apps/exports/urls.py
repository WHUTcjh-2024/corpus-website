from django.urls import path

from . import views


app_name = "exports"

urlpatterns = [
    path("", views.export_list, name="list"),
    path("corpora/<uuid:corpus_id>/kwic/", views.create_kwic_export, name="create_kwic"),
    path(
        "corpora/<uuid:corpus_id>/parallel/",
        views.create_parallel_export,
        name="create_parallel",
    ),
    path("<uuid:job_id>/status/", views.export_status, name="status"),
    path("<uuid:job_id>/download/", views.export_download, name="download"),
]
