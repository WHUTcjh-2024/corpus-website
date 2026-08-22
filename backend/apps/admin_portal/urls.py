from django.urls import path

from . import views


app_name = "admin_portal"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("corpora/", views.corpus_list, name="corpus_list"),
    path("corpora/upload/", views.corpus_upload, name="corpus_upload"),
    path("corpora/<uuid:corpus_id>/visibility/", views.corpus_visibility, name="corpus_visibility"),
    path("users/", views.user_list, name="user_list"),
    path("users/<int:profile_id>/review/", views.user_review, name="user_review"),
    path("announcements/", views.announcement_list, name="announcement_list"),
    path("announcements/new/", views.announcement_edit, name="announcement_create"),
    path("announcements/<int:announcement_id>/", views.announcement_edit, name="announcement_edit"),
    path("feedback/", views.feedback_list, name="feedback_list"),
    path("feedback/<uuid:ticket_id>/", views.feedback_detail, name="feedback_detail"),
]
