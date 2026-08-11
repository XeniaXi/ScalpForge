import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.session_episodes import write_session_episode_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Build label-separated session episodes")
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--session-manifest", type=Path, required=True)
    parser.add_argument("--structural-manifest", type=Path, required=True)
    parser.add_argument("--outcome-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = write_session_episode_dataset(
        args.feature_manifest,
        args.session_manifest,
        args.structural_manifest,
        args.outcome_manifest,
        args.output_root,
    )
    print(json.dumps(asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
