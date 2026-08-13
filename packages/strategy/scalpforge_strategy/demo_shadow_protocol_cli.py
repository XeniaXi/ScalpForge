import argparse
import json
from pathlib import Path

from .demo_shadow_protocol import initialize_demo_shadow_protocol, verify_protocol


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize or verify Candidate A demo shadow")
    parser.add_argument("--robustness-report", type=Path)
    parser.add_argument("--tick-replay-report", type=Path)
    parser.add_argument("--broker-economics-report", type=Path)
    parser.add_argument("--symbol-spec", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_protocol(args.verify)))
        return
    required = (
        args.robustness_report,
        args.tick_replay_report,
        args.broker_economics_report,
        args.symbol_spec,
        args.output_root,
    )
    if any(value is None for value in required):
        parser.error("initialization requires all evidence paths and --output-root")
    protocol = initialize_demo_shadow_protocol(
        args.robustness_report,
        args.tick_replay_report,
        args.broker_economics_report,
        args.symbol_spec,
        args.output_root,
    )
    print(json.dumps(protocol))
