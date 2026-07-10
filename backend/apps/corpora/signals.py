from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Corpus, CorpusDocumentation


@receiver(post_save, sender=Corpus)
def ensure_corpus_documentation(sender, instance: Corpus, created: bool, **kwargs) -> None:
    if created:
        CorpusDocumentation.objects.get_or_create(corpus=instance)
