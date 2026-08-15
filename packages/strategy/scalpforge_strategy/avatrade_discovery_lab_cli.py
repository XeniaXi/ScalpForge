import argparse
import json
from pathlib import Path

from .avatrade_discovery_lab import run_avatrade_discovery_lab


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the frozen AvaTrade strategy discovery sprint"
    )
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--news-events", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run_avatrade_discovery_lab(
                args.protocol,
                args.source_dir,
                args.output_root,
                args.news_events,
            )
        )
    )
