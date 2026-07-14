from django.urls import path

from . import views


app_name = "search"

urlpatterns = [
    path("<uuid:corpus_id>/kwic/", views.kwic_search, name="kwic"),
]
