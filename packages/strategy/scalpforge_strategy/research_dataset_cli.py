import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_replay import ParquetTickReplaySource

from scalpforge_strategy.research_dataset import FeatureConfig, write_feature_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Build point-in-time XAUUSD research features")
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    source = ParquetTickReplaySource(args.source_manifest)
    ticks = (event.payload for event in source.events())
    result = write_feature_dataset(
        ticks,
        args.source_manifest,
        args.output_root,
        FeatureConfig(),
    )
    print(json.dumps(asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
