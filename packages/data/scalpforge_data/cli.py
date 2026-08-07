import argparse
from pathlib import Path

from scalpforge_data.importer import TickCsvImporter


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a historical ScalpForge tick CSV")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--instrument", default="XAUUSD")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    importer = TickCsvImporter()
    result = importer.load(args.csv, source=args.source, instrument=args.instrument)
    if args.manifest:
        importer.write_manifest(result, args.manifest)
    print(
        f"dataset={result.manifest.dataset_id} "
        f"rows={len(result.ticks)} usable={result.is_usable}"
    )
    for issue in result.issues:
        print(f"{issue.severity.value}: row={issue.row_number} {issue.code}: {issue.message}")
    return 0 if result.is_usable else 2


if __name__ == "__main__":
    raise SystemExit(main())
