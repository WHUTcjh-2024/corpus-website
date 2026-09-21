from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

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
        previous_iteration_failed = False
        while True:
            # Management commands do not get Django's request-boundary connection
            # cleanup. Refresh persistent connections explicitly so a PostgreSQL
            # failover does not leave this publisher pinned to a dead socket.
            if options["loop"]:
                close_old_connections()
            try:
                summary = publish_pending_events(limit=options["limit"])
                if (
                    summary.published
                    or summary.retry_scheduled
                    or summary.dead_lettered
                ):
                    self.stdout.write(
                        "published={published} retry_scheduled={retry_scheduled} dead_lettered={dead_lettered} skipped={skipped}".format(
                            published=summary.published,
                            retry_scheduled=summary.retry_scheduled,
                            dead_lettered=summary.dead_lettered,
                            skipped=summary.skipped,
                        )
                    )
                current_time = time.monotonic()
                if current_time >= next_cleanup_at:
                    deleted = purge_published_events()
                    if deleted:
                        self.stdout.write(f"purged={deleted}")
                    next_cleanup_at = current_time + options["cleanup_interval"]
                if previous_iteration_failed:
                    logger.warning("Outbox publisher database loop recovered")
                previous_iteration_failed = False
            except Exception as exc:
                if not options["loop"]:
                    raise CommandError(f"Unable to publish outbox events: {exc}") from exc
                previous_iteration_failed = True
                logger.exception("Outbox publisher iteration failed")
            finally:
                if options["loop"]:
                    close_old_connections()

            if not options["loop"]:
                return
            time.sleep(options["interval"])
