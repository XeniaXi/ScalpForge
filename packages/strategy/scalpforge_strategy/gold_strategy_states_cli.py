import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.gold_strategy_states import write_gold_strategy_states


def main() -> int:
    parser = argparse.ArgumentParser(description="Build causal multi-timeframe gold states")
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asdict(write_gold_strategy_states(args.feature_manifest, args.output_root))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
