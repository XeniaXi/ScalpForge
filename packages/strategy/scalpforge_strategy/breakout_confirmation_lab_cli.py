import argparse
import json
from pathlib import Path

from scalpforge_strategy.breakout_confirmation_lab import run_breakout_confirmation_lab


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fixed breakout confirmation research")
    parser.add_argument("--episode-manifest", type=Path, required=True)
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = run_breakout_confirmation_lab(
        args.episode_manifest, args.feature_manifest, args.output_root
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
