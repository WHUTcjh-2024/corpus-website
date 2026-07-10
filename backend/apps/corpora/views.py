from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import approved_user_required

from .forms import PersonalCorpusForm
from .services import can_create_personal_corpus, visible_corpora_for


@approved_user_required
def corpus_list(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "corpora/corpus_list.html",
        {
            "corpora": visible_corpora_for(request.user),
            "can_create_personal": can_create_personal_corpus(request.user),
        },
    )


@approved_user_required
def corpus_create(request: HttpRequest) -> HttpResponse:
    if not can_create_personal_corpus(request.user):
        return HttpResponse("当前账号不能登记个人语料库。", status=403)
    if request.method == "POST":
        form = PersonalCorpusForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                corpus = form.save()
            except PermissionDenied:
                return HttpResponse("当前账号不能登记个人语料库。", status=403)
            return redirect("corpora:documentation", corpus_id=corpus.pk)
    else:
        form = PersonalCorpusForm(user=request.user)
    return render(request, "corpora/corpus_create.html", {"form": form})


@approved_user_required
def corpus_documentation(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus = get_object_or_404(
        visible_corpora_for(request.user).select_related("documentation"),
        pk=corpus_id,
    )
    return render(
        request,
        "corpora/documentation.html",
        {"corpus": corpus, "documentation": corpus.documentation},
    )
