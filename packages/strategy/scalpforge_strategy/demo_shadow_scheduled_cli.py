from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .demo_shadow_engine import run_demo_shadow


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the read-only demo shadow from a scheduler")
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_scheduled(args.protocol, args.source_dir)
    print(json.dumps(result))


def run_scheduled(protocol_path: Path, source_dir: Path) -> dict[str, object]:
    root = protocol_path.resolve().parent
    log_root = root / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    log_path = log_root / f"{datetime.now(UTC):%Y%m%d}.jsonl"
    lock_path = root / "scheduled-run.lock"
    lock_fd = _acquire_lock(lock_path)
    if lock_fd is None:
        result = {
            "invoked_at_utc": datetime.now(UTC).isoformat(),
            "event": "skipped_overlap",
            "order_submission_enabled": False,
        }
        _append(log_path, result)
        return result
    started = datetime.now(UTC)
    _append(
        log_path,
        {
            "invoked_at_utc": started.isoformat(),
            "event": "started",
            "pid": os.getpid(),
            "order_submission_enabled": False,
        },
    )
    try:
        output = run_demo_shadow(protocol_path, source_dir)
        result = {
            "invoked_at_utc": datetime.now(UTC).isoformat(),
            "event": "completed",
            "exit_code": 0,
            "duration_seconds": (datetime.now(UTC) - started).total_seconds(),
            "output": output,
            "order_submission_enabled": False,
        }
        _append(log_path, result)
        return result
    except Exception as exc:
        _append(
            log_path,
            {
                "invoked_at_utc": datetime.now(UTC).isoformat(),
                "event": "failed",
                "exit_code": 1,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "order_submission_enabled": False,
            },
        )
        raise
    finally:
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)


def _acquire_lock(path: Path) -> int | None:
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = datetime.now(UTC) - datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if age <= timedelta(minutes=10):
            return None
        path.unlink(missing_ok=True)
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)


def _append(path: Path, value: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
