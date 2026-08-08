import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.outcomes import OutcomeConfig, write_outcome_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Build separate executable outcome labels")
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = write_outcome_dataset(args.feature_manifest, args.output_root, OutcomeConfig())
    print(json.dumps(asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
