import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.structural import StructuralConfig, write_structural_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Build causal XAU/USD structural features")
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = write_structural_dataset(args.feature_manifest, args.output_root, StructuralConfig())
    print(json.dumps(asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
