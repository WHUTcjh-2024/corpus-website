from django.urls import path

from . import views


app_name = "corpora"

urlpatterns = [
    path("", views.corpus_list, name="list"),
    path("mine/", views.my_corpora, name="mine"),
    path("create/", views.corpus_create, name="create"),
    path("upload/", views.corpus_upload, name="upload"),
    path("<uuid:corpus_id>/status/", views.corpus_status, name="status"),
    path("<uuid:corpus_id>/retry/", views.corpus_retry, name="retry"),
    path("<uuid:corpus_id>/delete/", views.corpus_delete, name="delete"),
    path("<uuid:corpus_id>/documentation/", views.corpus_documentation, name="documentation"),
]
