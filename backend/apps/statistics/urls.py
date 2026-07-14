from django.urls import path

from . import views


app_name = "statistics"

urlpatterns = [
    path("<uuid:corpus_id>/word-list/", views.word_list, name="word_list"),
    path("<uuid:corpus_id>/ngrams/", views.ngrams, name="ngrams"),
    path("<uuid:corpus_id>/collocates/", views.collocates, name="collocates"),
    path("<uuid:corpus_id>/keywords/", views.keywords, name="keywords"),
    path("<uuid:corpus_id>/wordcloud/", views.wordcloud, name="wordcloud"),
    path(
        "<uuid:corpus_id>/concordance-plot/",
        views.concordance_plot,
        name="concordance_plot",
    ),
]
