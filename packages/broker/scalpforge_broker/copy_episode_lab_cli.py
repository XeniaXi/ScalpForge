import argparse
import json
from pathlib import Path

from scalpforge_broker.copy_episode_lab import run_episode_audit, write_episode_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a cash-flow and episode-aware MQL5 audit")
    parser.add_argument("source", type=Path)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--source-utc-offset-hours", type=float, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = run_episode_audit(
        args.source,
        provider_id=args.provider_id,
        source_utc_offset_hours=args.source_utc_offset_hours,
    )
    path = write_episode_audit(report, args.output_root)
    print(json.dumps({**report, "report_path": str(path)}))


if __name__ == "__main__":
    main()
