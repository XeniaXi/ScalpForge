import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_strategy.trade_management_lab import (
    TradeManagementConfig,
    run_trade_management_lab,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare provider and copied trade management")
    parser.add_argument("--provider-manifest", type=Path, required=True)
    parser.add_argument("--copied-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--maximum-entry-lag-seconds", type=int, default=30)
    parser.add_argument("--minimum-pairs", type=int, default=30)
    args = parser.parse_args()
    report = run_trade_management_lab(
        args.provider_manifest,
        args.copied_manifest,
        args.output_root,
        TradeManagementConfig(args.maximum_entry_lag_seconds, args.minimum_pairs),
    )
    print(json.dumps(asdict(report)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
