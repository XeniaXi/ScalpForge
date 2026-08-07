from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .collector import collect_once


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot and monitor read-only MT4 tick files")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--prefix", default="scalpforge")
    parser.add_argument("--symbol", default="GOLD")
    parser.add_argument("--archive", type=Path, default=Path("data/raw/avatrade/GOLD"))
    parser.add_argument("--stale-seconds", type=int, default=120)
    parser.add_argument("--watch-seconds", type=int, default=0)
    args = parser.parse_args()
    if args.source is None and args.source_dir is None:
        parser.error("provide --source or --source-dir")
    while True:
        source = args.source
        if source is None:
            day = datetime.now(UTC).strftime("%Y%m%d")
            source = args.source_dir / f"{args.prefix}_{args.symbol}_{day}_ticks.csv"
        result = collect_once(source, args.archive, stale_after_seconds=args.stale_seconds)
        print(json.dumps(asdict(result)))
        if args.watch_seconds <= 0:
            break
        time.sleep(args.watch_seconds)


if __name__ == "__main__":
    main()
