import argparse
import json
from pathlib import Path

from scalpforge_broker.report import compare_feed_files


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare ScalpForge MT4 demo quote feeds")
    parser.add_argument("files", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = compare_feed_files(args.files)
    payload = json.dumps([report.to_dict() for report in reports], indent=2)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
