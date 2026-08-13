from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError

from apps.audits.services import consume_parallel_audit_results


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
        while True:
            applied = consume_parallel_audit_results(limit=options["limit"])
            if applied:
                self.stdout.write(f"projected_audit_results={applied}")
            if not options["loop"]:
                return
            time.sleep(options["interval"])
