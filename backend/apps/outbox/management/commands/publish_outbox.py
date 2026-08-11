from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand, CommandError

from apps.outbox.services import publish_pending_events, purge_published_events


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Publish durable outbox events to Celery, with optional recovery loop."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=float, default=5.0)
        parser.add_argument("--cleanup-interval", type=float, default=3600.0)

    def handle(self, *args, **options) -> None:
        if options["limit"] < 1:
            raise CommandError("--limit must be greater than zero")
        if options["interval"] <= 0:
            raise CommandError("--interval must be greater than zero")
        if options["cleanup_interval"] <= 0:
            raise CommandError("--cleanup-interval must be greater than zero")

        next_cleanup_at = time.monotonic()
        while True:
            try:
                summary = publish_pending_events(limit=options["limit"])
                if summary.published or summary.retry_scheduled:
                    self.stdout.write(
                        "published={published} retry_scheduled={retry_scheduled} skipped={skipped}".format(
                            published=summary.published,
                            retry_scheduled=summary.retry_scheduled,
                            skipped=summary.skipped,
                        )
                    )
                if time.monotonic() >= next_cleanup_at:
                    deleted = purge_published_events()
                    if deleted:
                        self.stdout.write(f"purged={deleted}")
                    next_cleanup_at = time.monotonic() + options["cleanup_interval"]
            except Exception as exc:
                if not options["loop"]:
                    raise CommandError(f"Unable to publish outbox events: {exc}") from exc
                logger.exception("Outbox publisher iteration failed")

            if not options["loop"]:
                return
            time.sleep(options["interval"])
