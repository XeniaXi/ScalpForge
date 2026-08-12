import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .trend_candidate_audit import run_trend_candidate_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit frozen trend-continuation Candidate A")
    parser.add_argument("--episode-manifest", type=Path, required=True)
    parser.add_argument("--outcome-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asdict(run_trend_candidate_audit(
        args.episode_manifest, args.outcome_manifest, args.output_root
    ))))
