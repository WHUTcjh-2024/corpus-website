from __future__ import annotations

from collections.abc import Iterator

from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseForbidden,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, render

from apps.accounts.permissions import approved_user_required
from apps.audit.models import AuditEventType
from apps.audit.services import record_audit_event, serializable_form_data
from apps.corpora.models import Corpus, CorpusSourceType, CorpusStatus, CorpusType
from apps.corpora.services import visible_corpora_for

from .engine import ParallelIndexCorrupt, ParallelIndexUnavailable, ParallelSearchEngine
from .forms import ParallelSearchForm


PARALLEL_TYPES = {
    CorpusType.ALIGNED_TSV,
    CorpusType.PAIRED_RAW_ZH_EN,
    CorpusType.PAIRED_TAGGED_ZH_EN,
}


@approved_user_required
def parallel_search(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus = get_object_or_404(Corpus, pk=corpus_id)
    if not visible_corpora_for(request.user).filter(pk=corpus.pk).exists():
        return HttpResponseForbidden("无权检索该语料库。")

    available_alignment_units = _available_alignment_units(corpus)
    default_alignment_unit = available_alignment_units[0]
    form_data = request.GET.copy() if request.GET else None
    if form_data is not None:
        form_data.setdefault("alignment_unit", default_alignment_unit)
    form = ParallelSearchForm(
        form_data,
        default_alignment_unit=default_alignment_unit,
        available_alignment_units=available_alignment_units,
    )
    result = None
    search_error = _availability_error(corpus)
    if not search_error and form.is_bound and form.is_valid():
        engine = ParallelSearchEngine(data_root=settings.DATA_ROOT, corpus_id=str(corpus.pk))
        try:
            result = engine.search(
                form.to_query(),
                page=form.cleaned_data["page"],
                page_size=form.cleaned_data["page_size"],
            )
            record_audit_event(
                AuditEventType.PARALLEL_SEARCH,
                request=request,
                corpus=corpus,
                metadata={
                    "parameters": serializable_form_data(form.cleaned_data),
                    "result_count": result.total,
                },
            )
        except (ParallelIndexUnavailable, ParallelIndexCorrupt) as exc:
            search_error = str(exc)

    query_parameters = form_data.copy() if form_data is not None else request.GET.copy()
    query_parameters.pop("page", None)
    return render(
        request,
        "parallel/search.html",
        {
            "corpus": corpus,
            "form": form,
            "result": result,
            "search_error": search_error,
            "query_string": query_parameters.urlencode(),
            "can_export": corpus.source_type == CorpusSourceType.USER
            and corpus.owner_id == request.user.pk,
            "export_query_string": query_parameters.urlencode(),
        },
        status=409 if search_error else 200,
    )


@approved_user_required
def parallel_export(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus = get_object_or_404(Corpus, pk=corpus_id)
    if not visible_corpora_for(request.user).filter(pk=corpus.pk).exists():
        return HttpResponseForbidden("无权访问该语料库。")
    if corpus.source_type != CorpusSourceType.USER or corpus.owner_id != request.user.pk:
        return HttpResponseForbidden("教师和演示语料禁止导出；只能导出本人语料。")
    if availability_error := _availability_error(corpus):
        return HttpResponse(availability_error, status=409)

    available_alignment_units = _available_alignment_units(corpus)
    default_alignment_unit = available_alignment_units[0]
    form_data = request.GET.copy() if request.GET else None
    if form_data is not None:
        form_data.setdefault("alignment_unit", default_alignment_unit)
    form = ParallelSearchForm(
        form_data,
        default_alignment_unit=default_alignment_unit,
        available_alignment_units=available_alignment_units,
    )
    if not form.is_valid():
        return HttpResponse("导出条件无效。", status=400)
    engine = ParallelSearchEngine(data_root=settings.DATA_ROOT, corpus_id=str(corpus.pk))
    try:
        engine.search(form.to_query(), page_size=1)
    except (ParallelIndexUnavailable, ParallelIndexCorrupt) as exc:
        return HttpResponse(str(exc), status=409)
    response = StreamingHttpResponse(
        _tsv_rows(engine, form),
        content_type="text/tab-separated-values; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="parallel-{corpus.pk}.tsv"'
    response["X-Content-Type-Options"] = "nosniff"
    record_audit_event(
        AuditEventType.EXPORT_DOWNLOADED,
        request=request,
        corpus=corpus,
        metadata={"kind": "parallel_legacy_stream", "parameters": form.cleaned_data},
    )
    return response


def _availability_error(corpus: Corpus) -> str:
    if corpus.corpus_type not in PARALLEL_TYPES:
        return "该语料库不是中英平行语料。"
    if corpus.status != CorpusStatus.READY:
        return "语料库尚未加工完成。"
    return ""


def _available_alignment_units(corpus: Corpus) -> tuple[str, ...]:
    if corpus.corpus_type == CorpusType.PAIRED_RAW_ZH_EN:
        return ("paragraph",)
    if corpus.corpus_type == CorpusType.PAIRED_TAGGED_ZH_EN:
        return ("sentence", "paragraph")
    return ("sentence",)


def _tsv_rows(engine: ParallelSearchEngine, form: ParallelSearchForm) -> Iterator[str]:
    yield "\ufeff全局序号\t语料内序号\t中文\t英文\t对齐单元\t对齐方法\t置信度\r\n"
    for row in engine.iter_export_rows(form.to_query()):
        yield "\t".join(_safe_tsv_cell(value) for value in row) + "\r\n"


def _safe_tsv_cell(value: object) -> str:
    text = str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text
