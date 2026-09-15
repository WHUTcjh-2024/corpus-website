from django.urls import path

from . import views


app_name = "research_history"

urlpatterns = [
    path("", views.search_history, name="list"),
    path("save/", views.save_search_view, name="save"),
    path("<int:saved_id>/delete/", views.delete_saved_search, name="delete"),
]
