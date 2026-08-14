import argparse
import json
from pathlib import Path

from .demo_shadow_rollover import rollover_demo_shadow


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a clean Candidate A shadow protocol from certified canonical state"
    )
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--parity-report", type=Path, required=True)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--live-source-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            rollover_demo_shadow(
                args.protocol,
                args.parity_report,
                args.snapshot_dir,
                args.live_source_dir,
                args.output_root,
            )
        )
    )
