from __future__ import annotations

from django.core.signing import salted_hmac
from django.utils import timezone

from apps.corpora.models import Corpus, CorpusSourceType


def teacher_watermark(request) -> dict:
    resolver_match = getattr(request, "resolver_match", None)
    corpus_id = resolver_match.kwargs.get("corpus_id") if resolver_match else None
    user = getattr(request, "user", None)
    if not corpus_id or not getattr(user, "is_authenticated", False):
        return {"teacher_watermark": None}

    corpus = Corpus.objects.filter(pk=corpus_id, source_type=CorpusSourceType.TEACHER).first()
    if corpus is None:
        return {"teacher_watermark": None}

    occurred_at = timezone.localtime().replace(second=0, microsecond=0)
    trace = salted_hmac(
        "teacher-corpus-watermark",
        f"{user.pk}:{corpus.pk}:{occurred_at.isoformat()}",
    ).hexdigest()[:12]
    label = f"仅限授权检索 · {user.get_username()} · {occurred_at:%Y-%m-%d %H:%M} · {trace}"
    return {
        "teacher_watermark": {
            "label": label,
            "trace": trace,
            "corpus_id": str(corpus.pk),
        }
    }
