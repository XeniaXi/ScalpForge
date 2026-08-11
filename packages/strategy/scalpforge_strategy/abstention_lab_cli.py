import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.abstention_lab import run_abstention_lab


def main() -> int:
    parser = argparse.ArgumentParser(description="Run nested walk-forward abstention research")
    parser.add_argument("--episode-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = run_abstention_lab(args.episode_manifest, args.output_root)
    print(json.dumps(asdict(report)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
