from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .gold_strategy_tournament import run_gold_strategy_tournament


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the sealed gold strategy tournament")
    parser.add_argument("--episode-manifest", type=Path, required=True)
    parser.add_argument("--outcome-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = run_gold_strategy_tournament(
        args.episode_manifest, args.outcome_manifest, args.output_root
    )
    print(json.dumps(asdict(report)))
