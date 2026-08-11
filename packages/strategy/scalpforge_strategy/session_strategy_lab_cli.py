import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.session_strategy_lab import run_session_strategy_lab


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the causal XAU/USD session strategy lab")
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--session-manifest", type=Path, required=True)
    parser.add_argument("--outcome-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = run_session_strategy_lab(
        args.feature_manifest, args.session_manifest, args.outcome_manifest, args.output_root
    )
    print(json.dumps(asdict(report)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
