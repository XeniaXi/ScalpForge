import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.feasibility import FeasibilityConfig, run_feasibility_map


def main() -> int:
    parser = argparse.ArgumentParser(description="Map XAU/USD movement-to-cost feasibility")
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--structural-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_feasibility_map(
        args.feature_manifest,
        args.structural_manifest,
        args.output_root,
        FeasibilityConfig(),
    )
    print(json.dumps(asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
