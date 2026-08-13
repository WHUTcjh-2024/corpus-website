from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand, CommandError

from apps.agent.services import expire_pending_approvals
from apps.audits.services import reconcile_remote_audits
from apps.outbox.services import publish_pending_events, purge_published_events


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Publish durable outbox events to Celery, with optional recovery loop."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=float, default=5.0)
        parser.add_argument("--cleanup-interval", type=float, default=3600.0)
        parser.add_argument("--approval-cleanup-interval", type=float, default=60.0)
        parser.add_argument("--audit-reconcile-interval", type=float, default=60.0)

    def handle(self, *args, **options) -> None:
        if options["limit"] < 1:
            raise CommandError("--limit must be greater than zero")
        if options["interval"] <= 0:
            raise CommandError("--interval must be greater than zero")
        if options["cleanup_interval"] <= 0:
            raise CommandError("--cleanup-interval must be greater than zero")
        if options["approval_cleanup_interval"] <= 0:
            raise CommandError("--approval-cleanup-interval must be greater than zero")
        if options["audit_reconcile_interval"] <= 0:
            raise CommandError("--audit-reconcile-interval must be greater than zero")

        next_cleanup_at = time.monotonic()
        next_approval_cleanup_at = time.monotonic()
        next_audit_reconcile_at = time.monotonic()
        while True:
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
                if current_time >= next_approval_cleanup_at:
                    expired = expire_pending_approvals()
                    if expired:
                        self.stdout.write(f"expired_agent_approvals={expired}")
                    next_approval_cleanup_at = current_time + options["approval_cleanup_interval"]
                if current_time >= next_audit_reconcile_at:
                    reconciled = reconcile_remote_audits()
                    if reconciled:
                        self.stdout.write(f"reconciled_remote_audits={reconciled}")
                    next_audit_reconcile_at = current_time + options["audit_reconcile_interval"]
            except Exception as exc:
                if not options["loop"]:
                    raise CommandError(f"Unable to publish outbox events: {exc}") from exc
                logger.exception("Outbox publisher iteration failed")

            if not options["loop"]:
                return
            time.sleep(options["interval"])
