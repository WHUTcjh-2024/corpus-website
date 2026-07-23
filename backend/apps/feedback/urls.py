from django.urls import path

from . import views


app_name = "feedback"

urlpatterns = [
    path("", views.ticket_list, name="list"),
    path("new/", views.ticket_create, name="create"),
    path("<uuid:ticket_id>/", views.ticket_detail, name="detail"),
]
