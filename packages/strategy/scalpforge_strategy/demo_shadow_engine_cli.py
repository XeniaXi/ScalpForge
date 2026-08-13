import argparse
import json
from pathlib import Path

from .demo_shadow_engine import run_demo_shadow


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the read-only Candidate A demo shadow once")
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--lookback-days", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(run_demo_shadow(args.protocol, args.source_dir, args.lookback_days)))
