import argparse
import json
from pathlib import Path

from .candidate_tick_replay import run_candidate_tick_replay


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay frozen Candidate A on raw JForex ticks")
    parser.add_argument("--robustness-report", type=Path, required=True)
    parser.add_argument("--tick-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = run_candidate_tick_replay(args.robustness_report, args.tick_manifest, args.output_root)
    print(json.dumps(report))
