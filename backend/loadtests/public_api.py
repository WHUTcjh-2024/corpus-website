"""Dependency-free load test for safe public endpoints.

Example:
    python loadtests/public_api.py --base-url http://127.0.0.1:8010 \
        --concurrency 20 --requests 1000
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.client import RemoteDisconnected
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_ENDPOINTS = ("/healthz", "/api/session/", "/api/public-corpora/")


@dataclass(frozen=True, slots=True)
class RequestResult:
    elapsed_ms: float
    status_code: int


def request_once(*, base_url: str, endpoint: str, timeout: float) -> RequestResult:
    started_at = time.perf_counter()
    request = Request(
        f"{base_url.rstrip('/')}{endpoint}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - CLI URL is explicit.
            response.read()
            status_code = response.status
    except HTTPError as exc:
        status_code = exc.code
    except (RemoteDisconnected, TimeoutError, URLError):
        status_code = 0
    return RequestResult(
        elapsed_ms=(time.perf_counter() - started_at) * 1000,
        status_code=status_code,
    )


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percent)))
    return ordered[position]


def run_load_test(
    *,
    base_url: str,
    endpoints: tuple[str, ...],
    concurrency: int,
    request_count: int,
    timeout: float,
) -> tuple[list[RequestResult], float]:
    plan = [endpoints[index % len(endpoints)] for index in range(request_count)]
    started_at = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(
            executor.map(
                lambda endpoint: request_once(
                    base_url=base_url,
                    endpoint=endpoint,
                    timeout=timeout,
                ),
                plan,
            )
        )
    return results, time.perf_counter() - started_at


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load test safe corpus-platform GET endpoints.")
    parser.add_argument("--base-url", required=True, help="for example: http://127.0.0.1:8010")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=1000, dest="request_count")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float)
    parser.add_argument("--output", help="write the machine-readable result to this JSON file")
    parser.add_argument(
        "--endpoint",
        action="append",
        dest="endpoints",
        help="safe GET endpoint to include; repeat to provide several",
    )
    args = parser.parse_args()
    if args.concurrency < 1 or args.request_count < 1 or args.timeout <= 0:
        parser.error("concurrency and requests must be positive; timeout must be greater than zero")
    if not 0 <= args.max_error_rate <= 1:
        parser.error("max-error-rate must be between zero and one")
    if args.max_p95_ms is not None and args.max_p95_ms <= 0:
        parser.error("max-p95-ms must be greater than zero")
    return args


def main() -> int:
    args = parse_args()
    endpoints = tuple(args.endpoints or DEFAULT_ENDPOINTS)
    if any(not endpoint.startswith("/") for endpoint in endpoints):
        print("Every endpoint must begin with '/'.", file=sys.stderr)
        return 2

    results, elapsed_seconds = run_load_test(
        base_url=args.base_url,
        endpoints=endpoints,
        concurrency=args.concurrency,
        request_count=args.request_count,
        timeout=args.timeout,
    )
    latencies = [item.elapsed_ms for item in results]
    statuses = Counter(item.status_code for item in results)
    successful = sum(count for status, count in statuses.items() if 200 <= status < 400)
    error_rate = 1 - successful / len(results)
    p50_ms = statistics.median(latencies)
    p95_ms = percentile(latencies, 0.95)
    p99_ms = percentile(latencies, 0.99)
    report = {
        "base_url": args.base_url,
        "concurrency": args.concurrency,
        "elapsed_seconds": round(elapsed_seconds, 4),
        "endpoints": list(endpoints),
        "error_rate": round(error_rate, 6),
        "latency_ms": {
            "p50": round(p50_ms, 2),
            "p95": round(p95_ms, 2),
            "p99": round(p99_ms, 2),
        },
        "request_count": len(results),
        "requests_per_second": round(len(results) / elapsed_seconds, 2),
        "status_counts": {str(status): count for status, count in sorted(statuses.items())},
        "thresholds": {
            "max_error_rate": args.max_error_rate,
            "max_p95_ms": args.max_p95_ms,
        },
    }
    if args.output:
        with open(args.output, "w", encoding="utf-8") as output_file:
            json.dump(report, output_file, ensure_ascii=False, indent=2, sort_keys=True)
            output_file.write("\n")
    print(
        "\n".join(
            (
                f"requests={len(results)} concurrency={args.concurrency} elapsed_s={elapsed_seconds:.2f}",
                f"rps={len(results) / elapsed_seconds:.2f} success_rate={successful / len(results):.2%}",
                f"latency_ms p50={p50_ms:.2f} p95={p95_ms:.2f} p99={p99_ms:.2f}",
                f"status_counts={dict(sorted(statuses.items()))}",
            )
        )
    )
    error_rate_ok = error_rate <= args.max_error_rate
    p95_ok = args.max_p95_ms is None or p95_ms <= args.max_p95_ms
    return 0 if error_rate_ok and p95_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
