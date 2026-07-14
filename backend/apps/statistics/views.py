from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, QueryDict
from django.shortcuts import get_object_or_404, render

from apps.accounts.permissions import approved_user_required
from apps.audit.models import AuditEventType
from apps.audit.services import record_audit_event, serializable_form_data
from apps.corpora.models import Corpus, CorpusLanguage, CorpusStatus
from apps.corpora.services import visible_corpora_for

from .engine import StatisticsEngine, StatisticsIndexCorrupt, StatisticsIndexUnavailable
from .forms import (
    CollocateForm,
    ConcordancePlotForm,
    KeywordForm,
    NgramForm,
    WordcloudForm,
    WordListForm,
)


@approved_user_required
def word_list(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus, denied = _authorized_corpus(request, corpus_id)
    if denied:
        return denied
    languages = _available_languages(corpus)
    form_data = _form_data(request, languages[0])
    form = WordListForm(form_data, available_languages=languages)
    result = None
    error = _availability_error(corpus)
    if not error and form.is_valid():
        try:
            result = _engine(corpus).word_list(
                language=form.cleaned_data["language"],
                filter_text=form.cleaned_data["filter"],
                pos=form.cleaned_data["pos"],
                sort_by=form.cleaned_data["sort_by"],
                include_punctuation=form.cleaned_data["include_punctuation"],
                page=form.cleaned_data["page"],
                page_size=form.cleaned_data["page_size"],
            )
        except (StatisticsIndexUnavailable, StatisticsIndexCorrupt) as exc:
            error = str(exc)
    return _render(
        request,
        "statistics/word_list.html",
        corpus,
        form,
        result,
        error,
        form_data,
    )


@approved_user_required
def ngrams(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus, denied = _authorized_corpus(request, corpus_id)
    if denied:
        return denied
    languages = _available_languages(corpus)
    form_data = _form_data(request, languages[0])
    form = NgramForm(form_data, available_languages=languages)
    result = None
    error = _availability_error(corpus)
    if not error and form.is_valid():
        try:
            result = _engine(corpus).ngrams(
                language=form.cleaned_data["language"],
                n=form.cleaned_data["n"],
                min_frequency=form.cleaned_data["min_frequency"],
                filter_text=form.cleaned_data["filter"],
                include_punctuation=form.cleaned_data["include_punctuation"],
                page=form.cleaned_data["page"],
                page_size=form.cleaned_data["page_size"],
            )
        except (StatisticsIndexUnavailable, StatisticsIndexCorrupt) as exc:
            error = str(exc)
    return _render(
        request,
        "statistics/ngrams.html",
        corpus,
        form,
        result,
        error,
        form_data,
    )


@approved_user_required
def keywords(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus, denied = _authorized_corpus(request, corpus_id)
    if denied:
        return denied
    languages = _available_languages(corpus)
    candidate_references = list(
        visible_corpora_for(request.user)
        .filter(status=CorpusStatus.READY)
        .exclude(pk=corpus.pk)
        .select_related("documentation")
        .order_by("name", "pk")
    )
    compatible_languages_by_id = {
        reference.pk: tuple(
            language
            for language in languages
            if language in _available_languages(reference)
            and _segmentation_for_language(corpus, language)
            == _segmentation_for_language(reference, language)
        )
        for reference in candidate_references
    }
    references = [
        reference
        for reference in candidate_references
        if compatible_languages_by_id[reference.pk]
    ]
    reference_choices = tuple(
        (
            reference.pk,
            reference.name,
            compatible_languages_by_id[reference.pk],
        )
        for reference in references
    )
    form_data = _form_data(request, languages[0], bind_empty=False)
    form = KeywordForm(
        form_data,
        available_languages=languages,
        reference_corpora=reference_choices,
    )
    result = None
    error = _availability_error(corpus)
    if not references and not error:
        error = "没有可访问且已加工完成的参照语料库。"
    if not error and form.is_bound and form.is_valid():
        references_by_id = {str(reference.pk): reference for reference in references}
        reference = references_by_id[form.cleaned_data["reference_corpus"]]
        try:
            result = _engine(corpus).keywords(
                reference=_engine(reference),
                reference_name=reference.name,
                language=form.cleaned_data["language"],
                min_frequency=form.cleaned_data["min_frequency"],
                min_range=form.cleaned_data["min_range"],
                filter_text=form.cleaned_data["filter"],
                include_negative=form.cleaned_data["include_negative"],
                sort_by=form.cleaned_data["sort_by"],
                include_punctuation=form.cleaned_data["include_punctuation"],
                page=form.cleaned_data["page"],
                page_size=form.cleaned_data["page_size"],
            )
        except (StatisticsIndexUnavailable, StatisticsIndexCorrupt) as exc:
            error = str(exc)
        except ValueError as exc:
            form.add_error(None, str(exc))
    return _render(
        request,
        "statistics/keywords.html",
        corpus,
        form,
        result,
        error,
        form_data or QueryDict(),
    )


@approved_user_required
def wordcloud(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus, denied = _authorized_corpus(request, corpus_id)
    if denied:
        return denied
    languages = _available_languages(corpus)
    form_data = _form_data(request, languages[0])
    form = WordcloudForm(form_data, available_languages=languages)
    result = None
    error = _availability_error(corpus)
    if not error and form.is_valid():
        try:
            result = _engine(corpus).wordcloud(
                language=form.cleaned_data["language"],
                min_frequency=form.cleaned_data["min_frequency"],
                max_words=form.cleaned_data["max_words"],
                stopwords=form.cleaned_data["stopwords"],
                include_punctuation=form.cleaned_data["include_punctuation"],
                theme=form.cleaned_data["theme"],
            )
        except (StatisticsIndexUnavailable, StatisticsIndexCorrupt) as exc:
            error = str(exc)
    return _render(
        request,
        "statistics/wordcloud.html",
        corpus,
        form,
        result,
        error,
        form_data,
    )


@approved_user_required
def collocates(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus, denied = _authorized_corpus(request, corpus_id)
    if denied:
        return denied
    languages = _available_languages(corpus)
    form_data = _form_data(request, languages[0], bind_empty=False)
    form = CollocateForm(form_data, available_languages=languages)
    result = None
    error = _availability_error(corpus)
    if not error and form.is_bound and form.is_valid():
        try:
            result = _engine(corpus).collocates(
                form.cleaned_data["q"],
                language=form.cleaned_data["language"],
                left_span=form.cleaned_data["left_span"],
                right_span=form.cleaned_data["right_span"],
                min_frequency=form.cleaned_data["min_frequency"],
                pos=form.cleaned_data["pos"],
                sort_by=form.cleaned_data["sort_by"],
                include_punctuation=form.cleaned_data["include_punctuation"],
                page=form.cleaned_data["page"],
                page_size=form.cleaned_data["page_size"],
            )
        except (StatisticsIndexUnavailable, StatisticsIndexCorrupt) as exc:
            error = str(exc)
    return _render(
        request,
        "statistics/collocates.html",
        corpus,
        form,
        result,
        error,
        form_data or QueryDict(),
    )


@approved_user_required
def concordance_plot(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus, denied = _authorized_corpus(request, corpus_id)
    if denied:
        return denied
    languages = _available_languages(corpus)
    form_data = _form_data(request, languages[0], bind_empty=False)
    form = ConcordancePlotForm(form_data, available_languages=languages)
    result = None
    error = _availability_error(corpus)
    if not error and form.is_bound and form.is_valid():
        try:
            result = _engine(corpus).concordance_plot(
                form.cleaned_data["q"],
                language=form.cleaned_data["language"],
            )
        except (StatisticsIndexUnavailable, StatisticsIndexCorrupt) as exc:
            error = str(exc)
    return _render(
        request,
        "statistics/concordance_plot.html",
        corpus,
        form,
        result,
        error,
        form_data or QueryDict(),
    )


def _authorized_corpus(
    request: HttpRequest,
    corpus_id,
) -> tuple[Corpus, HttpResponse | None]:
    corpus = get_object_or_404(Corpus, pk=corpus_id)
    if not visible_corpora_for(request.user).filter(pk=corpus.pk).exists():
        return corpus, HttpResponseForbidden("无权分析该语料库。")
    return corpus, None


def _available_languages(corpus: Corpus) -> tuple[str, ...]:
    if corpus.language == CorpusLanguage.ZH:
        return ("zh",)
    if corpus.language == CorpusLanguage.EN:
        return ("en",)
    return ("zh", "en")


def _segmentation_for_language(corpus: Corpus, language: str) -> str:
    values = {}
    for item in corpus.documentation.segmentation_tool.split(";"):
        key, separator, value = item.partition(":")
        if separator:
            values[key] = value
    return values.get(language, corpus.documentation.segmentation_tool)


def _form_data(
    request: HttpRequest,
    default_language: str,
    *,
    bind_empty: bool = True,
) -> QueryDict | None:
    if not request.GET and not bind_empty:
        return None
    data = request.GET.copy()
    data.setdefault("language", default_language)
    return data


def _availability_error(corpus: Corpus) -> str:
    return "" if corpus.status == CorpusStatus.READY else "语料库尚未加工完成。"


def _engine(corpus: Corpus) -> StatisticsEngine:
    return StatisticsEngine(data_root=settings.DATA_ROOT, corpus_id=str(corpus.pk))


def _render(
    request: HttpRequest,
    template_name: str,
    corpus: Corpus,
    form,
    result,
    error: str,
    form_data: QueryDict,
) -> HttpResponse:
    query_parameters = form_data.copy()
    query_parameters.pop("page", None)
    if result is not None:
        result_count = getattr(result, "total", None)
        if result_count is None:
            result_count = getattr(result, "total_types", None)
        if result_count is None:
            result_count = len(getattr(result, "terms", ()))
        record_audit_event(
            AuditEventType.STATISTICS_QUERY,
            request=request,
            corpus=corpus,
            metadata={
                "tool": request.resolver_match.url_name if request.resolver_match else "",
                "parameters": serializable_form_data(form.cleaned_data),
                "result_count": result_count,
            },
        )
    return render(
        request,
        template_name,
        {
            "corpus": corpus,
            "form": form,
            "result": result,
            "statistics_error": error,
            "query_string": query_parameters.urlencode(),
        },
        status=409 if error else 200,
    )
