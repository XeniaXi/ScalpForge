from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .archive import archive_payload
from .dedup import deduplicate
from .fred import fetch_series, normalize_series
from .gdelt import fetch as fetch_gdelt
from .gdelt import normalize as normalize_gdelt
from .models import NormalizedEvent

DEFAULT_GDELT_QUERY = (
    '(gold OR XAU OR bullion OR "Federal Reserve" OR FOMC OR inflation OR '
    "Treasury OR sanctions OR war) sourcelang:english"
)


def _write_jsonl(path: Path, events: list[NormalizedEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[NormalizedEvent] = []
    if path.exists():
        existing = [
            NormalizedEvent.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    combined = deduplicate([*existing, *events])
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for event in combined:
            stream.write(event.model_dump_json() + "\n")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest research-only news and macro evidence")
    subparsers = parser.add_subparsers(dest="provider", required=True)
    gdelt_parser = subparsers.add_parser("gdelt")
    gdelt_parser.add_argument("--query", default=DEFAULT_GDELT_QUERY)
    gdelt_parser.add_argument("--max-records", type=int, default=100)
    fred_parser = subparsers.add_parser("fred")
    fred_parser.add_argument("--series-id", required=True)
    fred_parser.add_argument("--title", required=True)
    fred_parser.add_argument("--realtime-start", required=True)
    fred_parser.add_argument("--realtime-end", required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/news"))
    parser.add_argument("--output", type=Path, default=Path("data/normalized/news/events.jsonl"))
    args = parser.parse_args()

    if args.provider == "gdelt":
        payload = fetch_gdelt(args.query, max_records=args.max_records)
        archive = archive_payload(args.raw_root, "gdelt", payload)
        events = normalize_gdelt(payload)
    else:
        api_key = os.environ.get("FRED_API_KEY")
        if not api_key:
            parser.error("FRED_API_KEY is required for the fred provider")
        payload = fetch_series(
            args.series_id,
            api_key,
            realtime_start=args.realtime_start,
            realtime_end=args.realtime_end,
        )
        archive = archive_payload(args.raw_root, "fred", payload)
        events = normalize_series(args.series_id, args.title, payload)
    normalized = deduplicate(events)
    _write_jsonl(args.output, normalized)
    print(
        json.dumps(
            {
                "provider": args.provider,
                "events": len(normalized),
                "raw_archive": str(archive),
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
