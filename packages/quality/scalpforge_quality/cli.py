from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .dataset import build_parquet_dataset
from .news_report import build_news_quality_report, write_news_quality_report
from .tick_report import build_tick_quality_report, write_tick_quality_report


def main() -> None:
    parser = argparse.ArgumentParser(description="ScalpForge Phase 1 quality toolkit")
    sub = parser.add_subparsers(dest="command", required=True)
    ticks = sub.add_parser("ticks")
    ticks.add_argument("--archive", type=Path, default=Path("data/raw/avatrade/GOLD"))
    ticks.add_argument("--output", type=Path, default=Path("data/reports/tick-quality.latest.json"))
    dataset = sub.add_parser("dataset")
    dataset.add_argument("--archive", type=Path, default=Path("data/raw/avatrade/GOLD"))
    dataset.add_argument("--output-root", type=Path, default=Path("data/curated/ticks"))
    news = sub.add_parser("news")
    news.add_argument("--events", type=Path, default=Path("data/normalized/news/events.jsonl"))
    news.add_argument("--raw", type=Path, default=Path("data/raw/news"))
    news.add_argument("--output", type=Path, default=Path("data/reports/news-quality.latest.json"))
    args = parser.parse_args()
    if args.command == "ticks":
        result = build_tick_quality_report(args.archive)
        write_tick_quality_report(result, args.output)
    elif args.command == "dataset":
        result = build_parquet_dataset(args.archive, args.output_root)
    else:
        result = build_news_quality_report(args.events, args.raw)
        write_news_quality_report(result, args.output)
    print(json.dumps(asdict(result)))


if __name__ == "__main__":
    main()
