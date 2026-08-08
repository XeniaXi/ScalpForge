import argparse
import json
from dataclasses import asdict
from pathlib import Path

from scalpforge_data.jforex_batch import ingest_jforex_batches


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive and curate JForex history batches")
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = ingest_jforex_batches(args.source_dir, args.archive_root, args.output_root)
    print(json.dumps(asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
