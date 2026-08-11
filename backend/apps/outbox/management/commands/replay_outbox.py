from __future__ import annotations

from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from apps.outbox.services import replay_dead_letter_events


class Command(BaseCommand):
    help = "Return selected dead-letter outbox events to the durable pending queue."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--event-id", action="append", default=[])
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options) -> None:
        event_ids = options["event_id"]
        if bool(event_ids) == options["all"]:
            raise CommandError("Pass exactly one of --event-id or --all.")
        if options["limit"] < 1:
            raise CommandError("--limit must be greater than zero")

        try:
            parsed_event_ids = [UUID(event_id) for event_id in event_ids]
        except ValueError as exc:
            raise CommandError("Each --event-id must be a UUID.") from exc

        summary = replay_dead_letter_events(
            event_ids=None if options["all"] else parsed_event_ids,
            limit=options["limit"],
        )
        self.stdout.write(
            f"replayed={summary.replayed} skipped={summary.skipped}"
        )
