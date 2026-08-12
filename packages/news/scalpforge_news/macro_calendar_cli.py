import argparse
import json
from pathlib import Path

from scalpforge_news.macro_calendar import import_macro_events


def main() -> None:
    parser = argparse.ArgumentParser(description="Import and audit point-in-time macro events")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(import_macro_events(args.csv, args.output_root)))


if __name__ == "__main__":
    main()
