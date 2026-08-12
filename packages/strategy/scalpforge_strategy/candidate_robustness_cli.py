import argparse
import json
from pathlib import Path

from .candidate_robustness import run_candidate_robustness


def main() -> None:
    parser = argparse.ArgumentParser(description="Run frozen Candidate A robustness gate")
    parser.add_argument("--episode-manifest", type=Path, required=True)
    parser.add_argument("--outcome-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--experiment-registry", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run_candidate_robustness(
                args.episode_manifest,
                args.outcome_manifest,
                args.output_root,
                registry_path=args.experiment_registry,
            )
        )
    )
