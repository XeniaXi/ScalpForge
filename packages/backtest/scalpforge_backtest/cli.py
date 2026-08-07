import argparse
import csv
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from scalpforge_core.models import Side
from scalpforge_data import TickCsvImporter

from scalpforge_backtest.engine import BacktestEngine
from scalpforge_backtest.models import BacktestConfig, TradeIntent


def read_intents(path: Path) -> list[TradeIntent]:
    intents: list[TradeIntent] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row_number, row in enumerate(reader, start=2):
            try:
                instant = datetime.fromisoformat(row["generated_at"].replace("Z", "+00:00"))
                if instant.tzinfo is None:
                    raise ValueError("generated_at must include a timezone")
                intents.append(
                    TradeIntent(
                        generated_at=instant.astimezone(UTC),
                        side=Side(row["side"].lower()),
                        stop_distance=float(row["stop_distance"]),
                        take_profit_distance=float(row["take_profit_distance"]),
                        score=float(row.get("score") or 1.0),
                    )
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(f"invalid signal row {row_number}: {exc}") from exc
    return intents


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a cost-aware XAU/USD tick backtest")
    parser.add_argument("ticks", type=Path)
    parser.add_argument("signals", type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--initial-equity", type=float, default=10_000)
    parser.add_argument("--lots", type=float, default=0.10)
    parser.add_argument("--latency-ms", type=int, default=250)
    parser.add_argument("--slippage-bps", type=float, default=0.5)
    parser.add_argument("--commission-per-lot-side", type=float, default=0.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    imported = TickCsvImporter().load(args.ticks, source=args.source)
    if not imported.is_usable:
        print("tick dataset failed quality validation; backtest refused")
        return 2
    config = BacktestConfig(
        initial_equity=args.initial_equity,
        quantity_lots=args.lots,
        entry_latency_ms=args.latency_ms,
        slippage_bps=args.slippage_bps,
        commission_per_lot_per_side=args.commission_per_lot_side,
    )
    result = BacktestEngine(config).run(imported.ticks, read_intents(args.signals))
    payload = json.dumps(asdict(result), indent=2, default=str)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
