from django.urls import path

from . import views


app_name = "parallel"

urlpatterns = [
    path("<uuid:corpus_id>/", views.parallel_search, name="search"),
    path("<uuid:corpus_id>/export/", views.parallel_export, name="export"),
]
