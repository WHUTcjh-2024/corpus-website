"""Dependency-free load test for safe public endpoints.

Example:
    python loadtests/public_api.py --base-url http://127.0.0.1:8010 \
        --concurrency 20 --requests 1000
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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
    except URLError:
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
    parser.add_argument(
        "--endpoint",
        action="append",
        dest="endpoints",
        help="safe GET endpoint to include; repeat to provide several",
    )
    args = parser.parse_args()
    if args.concurrency < 1 or args.request_count < 1 or args.timeout <= 0:
        parser.error("concurrency and requests must be positive; timeout must be greater than zero")
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
    print(
        "\n".join(
            (
                f"requests={len(results)} concurrency={args.concurrency} elapsed_s={elapsed_seconds:.2f}",
                f"rps={len(results) / elapsed_seconds:.2f} success_rate={successful / len(results):.2%}",
                f"latency_ms p50={statistics.median(latencies):.2f} "
                f"p95={percentile(latencies, 0.95):.2f} p99={percentile(latencies, 0.99):.2f}",
                f"status_counts={dict(sorted(statuses.items()))}",
            )
        )
    )
    return 0 if error_rate <= 0.01 else 1


if __name__ == "__main__":
    raise SystemExit(main())
