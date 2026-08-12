import hashlib
import json
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scalpforge_broker.copy_episode_lab import RawTrade, read_mql5_account


def _quote_partitions(manifest_path: Path, start: datetime, end: datetime) -> list[Path]:
    meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    if str(meta.get("instrument", "")).upper() not in {"XAUUSD", "GOLD"}:
        raise ValueError("quote manifest is not XAUUSD/GOLD")
    paths = []
    root = manifest_path.parent.resolve()
    for stored in meta.get("partitions", []):
        path = Path(stored).resolve()
        if root not in path.parents:
            raise ValueError("quote partition escapes its dataset root")
        parts = {part.split("=", 1)[0]: part.split("=", 1)[1] for part in path.parts if "=" in part}
        if {"year", "month", "day"} <= parts.keys():
            day = datetime(int(parts["year"]), int(parts["month"]), int(parts["day"]), tzinfo=UTC)
            if day.date() < start.date() or day.date() > end.date():
                continue
        paths.append(path)
    return paths


def _read_quotes(
    paths: list[Path], start: datetime, end: datetime
) -> list[tuple[datetime, float, float]]:
    tables = []
    for path in paths:
        table = pq.read_table(path, columns=["occurred_at", "bid", "ask"])
        timestamps = table["occurred_at"]
        scalar_start = start if timestamps.type.tz else start.replace(tzinfo=None)
        scalar_end = end if timestamps.type.tz else end.replace(tzinfo=None)
        lower = pc.greater_equal(timestamps, pa.scalar(scalar_start, type=timestamps.type))
        upper = pc.less_equal(timestamps, pa.scalar(scalar_end, type=timestamps.type))
        filtered = table.filter(pc.and_(lower, upper))
        if filtered.num_rows:
            tables.append(filtered)
    if not tables:
        return []
    table = pa.concat_tables(tables).sort_by("occurred_at")
    timestamp_values = pc.cast(table["occurred_at"], pa.int64()).to_pylist()
    unit = table["occurred_at"].type.unit
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[unit]
    bids = table["bid"].to_pylist()
    asks = table["ask"].to_pylist()
    return [
        (datetime.fromtimestamp(value / divisor, tz=UTC), float(bid), float(ask))
        for value, bid, ask in zip(timestamp_values, bids, asks, strict=True)
    ]


def _nearest_quote(
    quotes: list[tuple[datetime, float, float]], timestamp: datetime, maximum_delay: float
) -> tuple[datetime, float, float] | None:
    candidate = min(
        quotes,
        key=lambda quote: abs((quote[0] - timestamp).total_seconds()),
        default=None,
    )
    if candidate is None or abs((candidate[0] - timestamp).total_seconds()) > maximum_delay:
        return None
    return candidate


def _analyze_trade(
    trade: RawTrade, quotes: list[tuple[datetime, float, float]], maximum_delay: float
) -> dict[str, object] | None:
    entry_quote = _nearest_quote(quotes, trade.opened_at, maximum_delay)
    exit_quote = _nearest_quote(quotes, trade.closed_at, maximum_delay)
    path = [quote for quote in quotes if trade.opened_at <= quote[0] <= trade.closed_at]
    if entry_quote is None or exit_quote is None or not path:
        return None
    side = 1 if trade.side == "buy" else -1
    entry = entry_quote[2] if side > 0 else entry_quote[1]
    path_prices = [quote[1] if side > 0 else quote[2] for quote in path]
    signed = [side * (price - entry) / entry * 10_000 for price in path_prices]
    exit_price = exit_quote[1] if side > 0 else exit_quote[2]
    reconstructed = side * (exit_price - entry) / entry * 10_000
    return {
        "trade": trade,
        "entry_delay_seconds": abs((entry_quote[0] - trade.opened_at).total_seconds()),
        "exit_delay_seconds": abs((exit_quote[0] - trade.closed_at).total_seconds()),
        "entry_price_difference_bps": (entry - trade.entry_price) / trade.entry_price * 10_000,
        "mfe_bps": max(signed),
        "mae_bps": min(signed),
        "reconstructed_gross_bps": reconstructed,
        "holding_seconds": (trade.closed_at - trade.opened_at).total_seconds(),
    }


def _added_into_adversity(trades: list[RawTrade]) -> tuple[int, int]:
    groups: dict[tuple[str, str], list[RawTrade]] = {}
    for trade in sorted(trades, key=lambda item: item.opened_at):
        groups.setdefault((trade.symbol, trade.side), []).append(trade)
    additions = adverse = 0
    for group in groups.values():
        active: list[RawTrade] = []
        for trade in group:
            active = [prior for prior in active if prior.closed_at >= trade.opened_at]
            if active:
                additions += 1
                prior = active[-1]
                side = 1 if trade.side == "buy" else -1
                if side * (trade.entry_price - prior.entry_price) < 0:
                    adverse += 1
            active.append(trade)
    return additions, adverse


def run_provider_path_lab(
    provider_history: Path,
    quote_manifest: Path,
    *,
    provider_id: str,
    source_utc_offset_hours: float,
    maximum_quote_delay_seconds: float = 2.0,
) -> dict[str, object]:
    trades, _, _ = read_mql5_account(
        provider_history, source_utc_offset_hours=source_utc_offset_hours
    )
    gold = [trade for trade in trades if "XAU" in trade.symbol or "GOLD" in trade.symbol]
    meta = json.loads(quote_manifest.read_text(encoding="utf-8"))
    quote_start = datetime.fromisoformat(str(meta["start_utc"]).replace("Z", "+00:00"))
    quote_end = datetime.fromisoformat(str(meta["end_utc_exclusive"]).replace("Z", "+00:00"))
    covered = [
        trade
        for trade in gold
        if trade.opened_at >= quote_start and trade.closed_at < quote_end
    ]
    if not covered:
        raise ValueError("provider history and quote dataset do not overlap")
    start = min(trade.opened_at for trade in covered)
    end = max(trade.closed_at for trade in covered)
    analyzed = []
    for trade in covered:
        window_start = trade.opened_at - timedelta(seconds=maximum_quote_delay_seconds)
        window_end = trade.closed_at + timedelta(seconds=maximum_quote_delay_seconds)
        paths = _quote_partitions(quote_manifest, window_start, window_end)
        quotes = _read_quotes(paths, window_start, window_end)
        result = _analyze_trade(trade, quotes, maximum_quote_delay_seconds)
        if result is not None:
            analyzed.append(result)
    if not analyzed:
        raise ValueError("no provider trades have executable quote coverage")
    quick = [row for row in analyzed if float(row["holding_seconds"]) <= 60]
    runners = [row for row in analyzed if float(row["holding_seconds"]) >= 900]
    maes = [float(row["mae_bps"]) for row in analyzed]
    mfes = [float(row["mfe_bps"]) for row in analyzed]
    additions, overlap_after_loss = _added_into_adversity(covered)
    coverage = len(analyzed) / len(gold)
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "provider_id": provider_id,
        "provider_source_sha256": hashlib.sha256(provider_history.read_bytes()).hexdigest(),
        "quote_dataset_id": meta["dataset_id"],
        "quote_provider": meta.get("provider"),
        "coverage": {
            "provider_gold_trades": len(gold),
            "date_overlap_trades": len(covered),
            "analyzed_trades": len(analyzed),
            "full_history_coverage_ratio": coverage,
            "analysis_start": start.isoformat(),
            "analysis_end": end.isoformat(),
        },
        "path_metrics": {
            "median_mae_bps": statistics.median(maes),
            "p95_adverse_excursion_bps": sorted(maes)[max(0, int(len(maes) * 0.05) - 1)],
            "worst_mae_bps": min(maes),
            "median_mfe_bps": statistics.median(mfes),
            "median_hold_seconds": statistics.median(
                float(row["holding_seconds"]) for row in analyzed
            ),
            "quick_exit_ratio": len(quick) / len(analyzed),
            "runner_ratio": len(runners) / len(analyzed),
            "quick_exit_median_mae_bps": statistics.median(
                [float(row["mae_bps"]) for row in quick]
            )
            if quick
            else None,
            "runner_median_mae_bps": statistics.median(
                [float(row["mae_bps"]) for row in runners]
            )
            if runners
            else None,
            "overlapping_entry_count": additions,
            "entries_added_at_worse_price_than_prior_open_ticket": overlap_after_loss,
        },
        "limitations": [
            "provider and quote data come from different venues",
            "quote coverage is limited to the external dataset date range",
            "basket cash P&L cannot be reconstructed without contract-value metadata",
            "worse-price additions are a point-in-time averaging proxy, not proof of martingale",
        ],
        "hypothesis_status": "descriptive_only",
        "research_only": True,
        "real_money_enabled": False,
    }


def write_provider_path_report(report: dict[str, object], output_root: Path) -> Path:
    report_id = "provider-path-" + hashlib.sha256(
        json.dumps(report, sort_keys=True).encode()
    ).hexdigest()[:16]
    directory = output_root / report_id
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "report.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path
