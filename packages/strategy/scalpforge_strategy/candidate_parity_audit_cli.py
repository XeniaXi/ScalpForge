import argparse
import json
from pathlib import Path

from .candidate_parity_audit import audit_candidate_parity


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit batch versus incremental Candidate A signal parity"
    )
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit_candidate_parity(args.protocol, args.source_dir, args.output_root)))
