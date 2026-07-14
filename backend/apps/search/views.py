from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render

from apps.accounts.permissions import approved_user_required
from apps.audit.models import AuditEventType
from apps.audit.services import record_audit_event, serializable_form_data
from apps.corpora.models import Corpus, CorpusLanguage, CorpusSourceType
from apps.corpora.services import visible_corpora_for

from .forms import KwicSearchForm
from .kwic import KwicIndexCorrupt, KwicIndexUnavailable, KwicSearchEngine
from .query_engine import ComplexQueryEngine


@approved_user_required
def kwic_search(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus = get_object_or_404(Corpus, pk=corpus_id)
    if not visible_corpora_for(request.user).filter(pk=corpus.pk).exists():
        return HttpResponseForbidden("无权检索该语料库。")

    languages = _available_languages(corpus)
    form = KwicSearchForm(request.GET or None, available_languages=languages)
    result = None
    search_error = ""
    if form.is_bound and form.is_valid() and form.cleaned_data["q"]:
        engine_class = (
            ComplexQueryEngine
            if form.cleaned_data["query_mode"] == "cqp"
            else KwicSearchEngine
        )
        engine = engine_class(data_root=settings.DATA_ROOT, corpus_id=str(corpus.pk))
        try:
            search_options = {
                "query": form.cleaned_data["q"],
                "context_size": form.cleaned_data["context"],
                "page": form.cleaned_data["page"],
                "page_size": form.cleaned_data["page_size"],
                "sort_by": form.cleaned_data["sort_by"],
                "pos": form.cleaned_data["pos"],
            }
            if form.cleaned_data["query_mode"] == "cqp":
                search_options["language"] = form.cleaned_data["language"]
            result = engine.search(**search_options)
            record_audit_event(
                AuditEventType.KWIC_SEARCH,
                request=request,
                corpus=corpus,
                metadata={
                    "parameters": serializable_form_data(form.cleaned_data),
                    "result_count": result.total,
                },
            )
        except (KwicIndexUnavailable, KwicIndexCorrupt) as exc:
            search_error = str(exc)

    query_parameters = request.GET.copy()
    query_parameters.pop("page", None)
    query_mode_label = ""
    language_label = ""
    if result is not None:
        query_mode_label = (
            "CQP 子集" if form.cleaned_data["query_mode"] == "cqp" else "普通 KWIC"
        )
        language_label = "中文" if form.cleaned_data["language"] == "zh" else "English"
    return render(
        request,
        "search/kwic.html",
        {
            "corpus": corpus,
            "form": form,
            "result": result,
            "search_error": search_error,
            "query_string": query_parameters.urlencode(),
            "query_mode_label": query_mode_label,
            "language_label": language_label,
            "can_export": corpus.source_type == CorpusSourceType.USER
            and corpus.owner_id == request.user.pk,
            "export_query_string": query_parameters.urlencode(),
        },
        status=409 if search_error else 200,
    )


def _available_languages(corpus: Corpus) -> tuple[str, ...]:
    if corpus.language == CorpusLanguage.ZH_EN:
        return (CorpusLanguage.ZH, CorpusLanguage.EN)
    if corpus.language in {CorpusLanguage.ZH, CorpusLanguage.EN}:
        return (corpus.language,)
    return (CorpusLanguage.ZH, CorpusLanguage.EN)
