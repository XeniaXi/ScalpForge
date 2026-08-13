import argparse
import json
from pathlib import Path

from .demo_shadow_engine import invalidate_shadow_signal, run_demo_shadow


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the read-only Candidate A demo shadow once")
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--invalidate-signal")
    parser.add_argument("--reason")
    args = parser.parse_args()
    if args.invalidate_signal:
        if not args.reason:
            parser.error("--invalidate-signal requires --reason")
        print(
            json.dumps(invalidate_shadow_signal(args.protocol, args.invalidate_signal, args.reason))
        )
        return
    if args.source_dir is None:
        parser.error("normal shadow execution requires --source-dir")
    print(json.dumps(run_demo_shadow(args.protocol, args.source_dir, args.lookback_days)))
