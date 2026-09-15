from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import QueryDict
from django.urls import reverse

from apps.corpora.models import Corpus, CorpusStatus
from apps.corpora.services import visible_corpora_for

from .models import SavedSearch, SavedSearchKind


SEARCH_ROUTES = {
    SavedSearchKind.KWIC: "search:kwic",
    SavedSearchKind.PARALLEL: "parallel:search",
    SavedSearchKind.WORD_LIST: "statistics:word_list",
    SavedSearchKind.CLUSTERS: "statistics:clusters",
    SavedSearchKind.NGRAMS: "statistics:ngrams",
    SavedSearchKind.COLLOCATES: "statistics:collocates",
    SavedSearchKind.KEYWORDS: "statistics:keywords",
    SavedSearchKind.WORDCLOUD: "statistics:wordcloud",
    SavedSearchKind.CONCORDANCE_PLOT: "statistics:concordance_plot",
}


@dataclass(frozen=True, slots=True)
class SavedSearchUsage:
    count: int
    bytes_used: int
    max_items: int
    max_bytes: int


def saved_search_usage(user) -> SavedSearchUsage:
    rows = SavedSearch.objects.filter(user=user).values_list("name", "query_string")
    return SavedSearchUsage(
        count=len(rows),
        bytes_used=sum(len(name.encode("utf-8")) + len(query.encode("utf-8")) for name, query in rows),
        max_items=settings.SAVED_SEARCH_MAX_ITEMS,
        max_bytes=settings.SAVED_SEARCH_TOTAL_BYTES,
    )


@transaction.atomic
def save_search(*, user, corpus_id, kind: str, name: str, query_string: str) -> SavedSearch:
    if kind not in SEARCH_ROUTES:
        raise ValidationError("不支持的检索类型。")
    try:
        corpus = Corpus.objects.select_for_update().get(pk=corpus_id)
    except Corpus.DoesNotExist as exc:
        raise ValidationError("语料库不存在。") from exc
    if not visible_corpora_for(user).filter(pk=corpus.pk).exists():
        raise PermissionDenied("无权保存该语料库的检索条件。")
    if corpus.status != CorpusStatus.READY:
        raise ValidationError("语料库尚未加工完成。")

    normalized_query = _normalize_query_string(query_string)
    display_name = " ".join(name.split())[:120]
    if not display_name:
        label = SavedSearchKind(kind).label
        query = QueryDict(normalized_query).get("q", "")
        display_name = f"{label} · {query or corpus.name}"[:120]

    existing = SavedSearch.objects.filter(
        user=user,
        corpus=corpus,
        kind=kind,
        query_string=normalized_query,
    ).first()
    if existing:
        if existing.name != display_name:
            existing.name = display_name
            existing.save(update_fields=["name", "updated_at"])
        return existing

    usage = saved_search_usage(user)
    item_bytes = len(display_name.encode("utf-8")) + len(normalized_query.encode("utf-8"))
    if usage.count >= usage.max_items:
        raise ValidationError(f"最多保存 {usage.max_items} 条检索。")
    if usage.bytes_used + item_bytes > usage.max_bytes:
        raise ValidationError("已保存检索达到账号容量上限，请先删除不再使用的记录。")
    return SavedSearch.objects.create(
        user=user,
        corpus=corpus,
        kind=kind,
        name=display_name,
        query_string=normalized_query,
    )


def replay_url(saved: SavedSearch) -> str:
    route = SEARCH_ROUTES.get(saved.kind)
    if route is None:
        return reverse("research_history:list")
    base = reverse(route, kwargs={"corpus_id": saved.corpus_id})
    return f"{base}?{saved.query_string}" if saved.query_string else base


def history_replay_url(*, path: str, parameters: dict) -> str:
    if not path.startswith("/") or path.startswith("//"):
        return reverse("research_history:list")
    safe_parameters = {
        str(key): value
        for key, value in parameters.items()
        if key not in {"page", "page_size"}
        and value is not None
        and value != ""
        and value is not False
    }
    query = urlencode(safe_parameters, doseq=True)
    return f"{path}?{query}" if query else path


def _normalize_query_string(value: str) -> str:
    if any(ord(character) < 32 for character in value):
        raise ValidationError("查询参数包含非法字符。")
    if len(value.encode("utf-8")) > settings.SAVED_SEARCH_MAX_QUERY_BYTES:
        raise ValidationError("单条检索条件超过保存上限。")
    query = QueryDict(value, mutable=True)
    query.pop("page", None)
    query.pop("page_size", None)
    return query.urlencode()
