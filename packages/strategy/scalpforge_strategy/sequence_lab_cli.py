import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.sequence_lab import SequenceLabConfig, run_sequence_lab


def main() -> int:
    parser = argparse.ArgumentParser(description="Run exact-path structural sequence research")
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--structural-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_sequence_lab(
        args.feature_manifest,
        args.structural_manifest,
        args.output_root,
        SequenceLabConfig(),
    )
    print(json.dumps(asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
