from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.agent.evaluation import evaluate_cases


class Command(BaseCommand):
    help = "Run deterministic corpus Agent policy and grounding regression cases."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--cases",
            default="apps/agent/evaluation/cases.json",
            help="Path to the versioned JSON evaluation cases.",
        )
        parser.add_argument("--report", help="Optional JSON report output path.")
        parser.add_argument("--min-pass-rate", type=float, default=1.0)

    def handle(self, *args, **options) -> None:
        if not 0 <= options["min_pass_rate"] <= 1:
            raise CommandError("--min-pass-rate must be between 0 and 1")
        case_path = Path(options["cases"])
        try:
            cases = json.loads(case_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CommandError(f"Unable to read evaluation cases: {exc}") from exc
        if not isinstance(cases, list):
            raise CommandError("Evaluation cases must be a JSON array.")
        report = evaluate_cases(cases)
        encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if options["report"]:
            destination = Path(options["report"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(encoded, encoding="utf-8")
        self.stdout.write(encoded)
        if report["summary"]["pass_rate"] < options["min_pass_rate"]:
            raise CommandError("Corpus Agent evaluation gate failed.")
