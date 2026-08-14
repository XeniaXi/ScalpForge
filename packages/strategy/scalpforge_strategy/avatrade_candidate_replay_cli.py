import argparse
import json
from datetime import datetime
from pathlib import Path

from .avatrade_candidate_replay import replay_avatrade_candidate


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay frozen Candidate A over locally gathered AvaTrade exports"
    )
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--start", type=datetime.fromisoformat)
    parser.add_argument("--end-exclusive", type=datetime.fromisoformat)
    args = parser.parse_args()
    print(
        json.dumps(
            replay_avatrade_candidate(
                args.protocol,
                args.source_dir,
                args.output_root,
                args.start,
                args.end_exclusive,
            )
        )
    )
