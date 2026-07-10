from django.urls import path

from . import views


app_name = "corpora"

urlpatterns = [
    path("", views.corpus_list, name="list"),
    path("create/", views.corpus_create, name="create"),
    path("<uuid:corpus_id>/documentation/", views.corpus_documentation, name="documentation"),
]
