from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from apps.audits.services import consume_parallel_audit_results


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Project terminal Go auditor result messages into Django audit state."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=float, default=1.0)

    def handle(self, *args, **options) -> None:
        if options["limit"] < 1:
            raise CommandError("--limit must be greater than zero")
        if options["interval"] <= 0:
            raise CommandError("--interval must be greater than zero")
        previous_iteration_failed = False
        while True:
            if options["loop"]:
                close_old_connections()
            try:
                applied = consume_parallel_audit_results(limit=options["limit"])
                if applied:
                    self.stdout.write(f"projected_audit_results={applied}")
                if previous_iteration_failed:
                    logger.warning("Audit result projector database loop recovered")
                previous_iteration_failed = False
            except Exception as exc:
                if not options["loop"]:
                    raise CommandError(f"Unable to project audit results: {exc}") from exc
                previous_iteration_failed = True
                logger.exception("Audit result projector iteration failed")
            finally:
                if options["loop"]:
                    close_old_connections()
            if not options["loop"]:
                return
            time.sleep(options["interval"])
