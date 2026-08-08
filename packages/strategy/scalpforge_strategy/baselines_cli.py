import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.baselines import BaselineConfig, run_baselines


def main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen cost-aware research baselines")
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--outcome-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_baselines(
        args.feature_manifest,
        args.outcome_manifest,
        args.output_root,
        BaselineConfig(),
    )
    print(json.dumps(asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
