import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.multi_hour_outcomes import write_multi_hour_outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description="Build separate multi-hour outcome labels")
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asdict(write_multi_hour_outcomes(args.feature_manifest, args.output_root))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
