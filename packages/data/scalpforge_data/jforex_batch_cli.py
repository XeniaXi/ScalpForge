import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from scalpforge_data.jforex_batch import ingest_jforex_batches


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive and curate JForex history batches")
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--start-utc", type=_utc, help="Inclusive ISO-8601 batch boundary")
    parser.add_argument("--end-utc", type=_utc, help="Exclusive ISO-8601 batch boundary")
    parser.add_argument(
        "--reference-source",
        action="store_true",
        help="Keep validated raw batches in source-dir instead of duplicating them",
    )
    args = parser.parse_args()
    result = ingest_jforex_batches(
        args.source_dir,
        args.archive_root,
        args.output_root,
        copy_to_archive=not args.reference_source,
        start_utc=args.start_utc,
        end_utc_exclusive=args.end_utc,
    )
    print(json.dumps(asdict(result)))
    return 0


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
