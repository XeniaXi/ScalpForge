from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

from .demo_shadow_scheduled_cli import _append, run_scheduled


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the read-only demo shadow as one persistent worker"
    )
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=int, default=300)
    args = parser.parse_args()
    if args.interval_seconds < 60:
        parser.error("--interval-seconds must be at least 60")
    run_worker(args.protocol, args.source_dir, args.interval_seconds)


def run_worker(
    protocol_path: Path,
    source_dir: Path,
    interval_seconds: int,
    *,
    maximum_cycles: int | None = None,
) -> None:
    root = protocol_path.resolve().parent
    log_root = root / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    worker_log = log_root / f"{datetime.now(UTC):%Y%m%d}-worker.jsonl"
    _append(
        worker_log,
        {
            "event": "worker_started",
            "invoked_at_utc": datetime.now(UTC).isoformat(),
            "interval_seconds": interval_seconds,
            "order_submission_enabled": False,
        },
    )
    cycles = 0
    while maximum_cycles is None or cycles < maximum_cycles:
        cycle_started = time.monotonic()
        try:
            result = run_scheduled(protocol_path, source_dir)
            _append(
                worker_log,
                {
                    "event": "worker_cycle",
                    "invoked_at_utc": datetime.now(UTC).isoformat(),
                    "cycle": cycles + 1,
                    "result_event": result.get("event"),
                    "order_submission_enabled": False,
                },
            )
        except Exception as exc:
            _append(
                worker_log,
                {
                    "event": "worker_cycle_failed",
                    "invoked_at_utc": datetime.now(UTC).isoformat(),
                    "cycle": cycles + 1,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "order_submission_enabled": False,
                },
            )
        cycles += 1
        if maximum_cycles is not None and cycles >= maximum_cycles:
            break
        elapsed = time.monotonic() - cycle_started
        time.sleep(max(0.0, interval_seconds - elapsed))


if __name__ == "__main__":
    main()
