import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_data.dukascopy import merge_side_exports


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely merge Dukascopy ASK/BID tick exports")
    parser.add_argument("--ask", type=Path, required=True)
    parser.add_argument("--bid", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = merge_side_exports(args.ask, args.bid, args.output)
    print(json.dumps(asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
