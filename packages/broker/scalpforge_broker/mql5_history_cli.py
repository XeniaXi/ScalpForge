import argparse
import json
from pathlib import Path

from scalpforge_broker.copy_trader import normalize_mql5_history


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize an MQL5 trade-history export")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--source-utc-offset-hours", type=float, required=True)
    args = parser.parse_args()
    result = normalize_mql5_history(
        args.source,
        args.output,
        provider_id=args.provider_id,
        source_utc_offset_hours=args.source_utc_offset_hours,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
