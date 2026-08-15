import argparse
import json
from pathlib import Path

from .avatrade_shock_forensics import audit_avatrade_shock_forensics


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit the frozen AvaTrade shock hypothesis")
    parser.add_argument("--discovery-report", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--news-events", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            audit_avatrade_shock_forensics(
                args.discovery_report,
                args.source_dir,
                args.output_root,
                args.news_events,
            )
        )
    )
