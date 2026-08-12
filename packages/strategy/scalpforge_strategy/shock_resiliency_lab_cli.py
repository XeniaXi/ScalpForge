import argparse
import json
from pathlib import Path

from scalpforge_strategy.shock_resiliency_lab import run_shock_resiliency_lab


def main() -> None:
    parser = argparse.ArgumentParser(description="Run causal shock-resiliency research")
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--outcome-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = run_shock_resiliency_lab(
        args.feature_manifest, args.outcome_manifest, args.output_root
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
