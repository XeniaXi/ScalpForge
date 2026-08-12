from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .gold_strategy_episodes import write_gold_strategy_episodes


def main() -> None:
    parser = argparse.ArgumentParser(description="Build causal gold strategy episodes")
    parser.add_argument("--state-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asdict(write_gold_strategy_episodes(args.state_manifest, args.output_root))))
