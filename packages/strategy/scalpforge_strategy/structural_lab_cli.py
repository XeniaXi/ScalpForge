import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.structural_lab import StructuralLabConfig, run_structural_lab


def main() -> int:
    parser = argparse.ArgumentParser(description="Run sealed-holdout XAU/USD structural lab")
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--structural-manifest", type=Path, required=True)
    parser.add_argument("--outcome-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_structural_lab(
        args.feature_manifest,
        args.structural_manifest,
        args.outcome_manifest,
        args.output_root,
        StructuralLabConfig(),
    )
    print(json.dumps(asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
