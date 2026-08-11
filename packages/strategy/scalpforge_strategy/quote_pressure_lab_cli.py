import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.quote_pressure_lab import QuotePressureConfig, run_quote_pressure_lab


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run nested quote-pressure cost feasibility research"
    )
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--structural-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = run_quote_pressure_lab(
        args.feature_manifest,
        args.structural_manifest,
        args.output_root,
        QuotePressureConfig(),
    )
    print(json.dumps(asdict(report)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
