import argparse
import json
from pathlib import Path

from .broker_economics_audit import run_broker_economics_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Candidate A with AvaTrade economics")
    parser.add_argument("--tick-replay-report", type=Path, required=True)
    parser.add_argument("--symbol-spec", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = run_broker_economics_audit(args.tick_replay_report, args.symbol_spec, args.output_root)
    print(json.dumps(report))
