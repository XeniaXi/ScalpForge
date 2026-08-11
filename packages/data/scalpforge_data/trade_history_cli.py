import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_data.trade_history import TradeHistoryCsvNormalizer


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize an authorized, anonymized MT4/MT5 trade-history CSV"
    )
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-system", required=True, choices=("mt4", "mt5", "copier", "other"))
    parser.add_argument(
        "--source-role",
        required=True,
        choices=("provider_master", "copied_account", "manual_account", "unknown"),
    )
    parser.add_argument("--account-alias", required=True)
    parser.add_argument("--broker", default="unknown")
    parser.add_argument("--source-timezone", required=True)
    parser.add_argument(
        "--entry-origin",
        default="unknown",
        choices=("provider", "copier", "manual", "ea_local", "unknown"),
    )
    parser.add_argument(
        "--exit-origin",
        default="unknown",
        choices=("provider", "copier", "manual", "ea_local", "unknown"),
    )
    args = parser.parse_args()
    result = TradeHistoryCsvNormalizer().normalize(
        args.csv,
        args.output_root,
        source_system=args.source_system,
        source_role=args.source_role,
        account_alias=args.account_alias,
        broker=args.broker,
        source_timezone=args.source_timezone,
        entry_origin=args.entry_origin,
        exit_origin=args.exit_origin,
    )
    print(json.dumps(asdict(result.manifest)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
