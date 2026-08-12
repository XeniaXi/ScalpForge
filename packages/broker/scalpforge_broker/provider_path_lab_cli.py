import argparse
import json
from pathlib import Path

from scalpforge_broker.provider_path_lab import run_provider_path_lab, write_provider_path_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconstruct provider paths against quote history")
    parser.add_argument("provider_history", type=Path)
    parser.add_argument("--quote-manifest", type=Path, required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--source-utc-offset-hours", type=float, required=True)
    parser.add_argument("--maximum-quote-delay-seconds", type=float, default=2.0)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = run_provider_path_lab(
        args.provider_history,
        args.quote_manifest,
        provider_id=args.provider_id,
        source_utc_offset_hours=args.source_utc_offset_hours,
        maximum_quote_delay_seconds=args.maximum_quote_delay_seconds,
    )
    path = write_provider_path_report(report, args.output_root)
    print(json.dumps({**report, "report_path": str(path)}))


if __name__ == "__main__":
    main()
