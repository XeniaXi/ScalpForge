import argparse
import json
from pathlib import Path

from scalpforge_strategy.controlled_breakout_lab import run_controlled_breakout_lab


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the controlled breakout episode lab")
    parser.add_argument("--episode-manifest", type=Path, required=True)
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run_controlled_breakout_lab(
                args.episode_manifest, args.feature_manifest, args.output_root
            )
        )
    )


if __name__ == "__main__":
    main()
