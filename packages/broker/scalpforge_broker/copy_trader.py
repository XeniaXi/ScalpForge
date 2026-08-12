import csv
import hashlib
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

REQUIRED_COLUMNS = {
    "provider_id",
    "opened_at",
    "closed_at",
    "symbol",
    "side",
    "volume",
    "net_profit",
}


@dataclass(frozen=True)
class CopyTrade:
    provider_id: str
    opened_at: datetime
    closed_at: datetime
    symbol: str
    side: str
    volume: float
    net_profit: float


@dataclass(frozen=True)
class DueDiligencePolicy:
    minimum_history_days: int = 730
    minimum_closed_trades: int = 200
    maximum_drawdown_pct_of_starting_equity: float = 20.0
    minimum_profit_factor: float = 1.15
    maximum_top_trade_profit_share: float = 0.25
    maximum_loss_size_escalation_ratio: float = 1.25
    minimum_profitable_month_ratio: float = 0.55


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a UTC offset")
    return parsed.astimezone(UTC)


def read_copy_trades(path: Path) -> list[CopyTrade]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing required columns: {', '.join(sorted(missing))}")
        trades = []
        for number, row in enumerate(reader, start=2):
            opened = _parse_time(row["opened_at"])
            closed = _parse_time(row["closed_at"])
            side = row["side"].strip().lower()
            if side not in {"buy", "sell"}:
                raise ValueError(f"row {number}: side must be buy or sell")
            volume = float(row["volume"])
            if volume <= 0 or closed < opened:
                raise ValueError(f"row {number}: invalid volume or chronology")
            trades.append(
                CopyTrade(
                    provider_id=row["provider_id"].strip(),
                    opened_at=opened,
                    closed_at=closed,
                    symbol=row["symbol"].strip().upper(),
                    side=side,
                    volume=volume,
                    net_profit=float(row["net_profit"]),
                )
            )
    if not trades:
        raise ValueError("trade history is empty")
    provider_ids = {trade.provider_id for trade in trades}
    if len(provider_ids) != 1 or "" in provider_ids:
        raise ValueError("one non-empty provider_id is required per audit")
    return sorted(trades, key=lambda trade: (trade.closed_at, trade.opened_at))


def normalize_mql5_history(
    source: Path,
    destination: Path,
    *,
    provider_id: str,
    source_utc_offset_hours: float,
) -> dict[str, object]:
    """Convert MQL5's duplicate-header semicolon export into the audit contract."""
    if not provider_id.strip():
        raise ValueError("provider_id cannot be empty")
    offset = timezone(timedelta(hours=source_utc_offset_hours))
    normalized: list[dict[str, object]] = []
    skipped_balance_rows = 0
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        header = next(reader, None)
        compact_header = [
            "Time",
            "Type",
            "Volume",
            "Symbol",
            "Price",
            "Volume",
            "Time",
            "Price",
            "Commission",
            "Swap",
            "Profit",
        ]
        detailed_header = [
            "Time",
            "Type",
            "Volume",
            "Symbol",
            "Price",
            "S/L",
            "T/P",
            "Time",
            "Price",
            "Commission",
            "Swap",
            "Profit",
            "Comment",
        ]
        if header not in (compact_header, detailed_header):
            raise ValueError("unsupported MQL5 history header")
        detailed = header == detailed_header
        for number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise ValueError(f"row {number}: expected {len(header)} columns")
            side = row[1].strip().lower()
            if side not in {"buy", "sell"}:
                skipped_balance_rows += 1
                continue
            close_index = 7 if detailed else 6
            commission_index = 9 if detailed else 8
            swap_index = 10 if detailed else 9
            profit_index = 11 if detailed else 10
            opened = datetime.strptime(row[0], "%Y.%m.%d %H:%M:%S").replace(tzinfo=offset)
            closed = datetime.strptime(row[close_index], "%Y.%m.%d %H:%M:%S").replace(
                tzinfo=offset
            )
            number_value = lambda value: float(value.replace(" ", "") or 0)  # noqa: E731
            commission = number_value(row[commission_index])
            swap = number_value(row[swap_index])
            profit = number_value(row[profit_index])
            normalized.append(
                {
                    "provider_id": provider_id.strip(),
                    "opened_at": opened.astimezone(UTC).isoformat(),
                    "closed_at": closed.astimezone(UTC).isoformat(),
                    "symbol": row[3].strip().upper(),
                    "side": side,
                    "volume": number_value(row[2]),
                    "net_profit": profit + commission + swap,
                }
            )
    if not normalized:
        raise ValueError("MQL5 export contains no closed buy/sell trades")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(REQUIRED_COLUMNS))
        writer.writeheader()
        writer.writerows(normalized)
    return {
        "source_file": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "destination": str(destination),
        "closed_trades": len(normalized),
        "skipped_non_trade_rows": skipped_balance_rows,
        "source_utc_offset_hours": source_utc_offset_hours,
        "commission_and_swap_included": True,
    }


def _maximum_drawdown(pnls: list[float], starting_equity: float) -> tuple[float, float]:
    equity = peak = starting_equity
    maximum = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum, maximum / starting_equity * 100


def _loss_size_escalation(trades: list[CopyTrade]) -> float:
    following = [
        current.volume / previous.volume
        for previous, current in zip(trades, trades[1:], strict=False)
        if previous.net_profit < 0
    ]
    return statistics.median(following) if following else 1.0


def audit_copy_history(
    path: Path,
    *,
    starting_equity: float,
    policy: DueDiligencePolicy | None = None,
) -> dict[str, object]:
    if starting_equity <= 0:
        raise ValueError("starting_equity must be positive")
    policy = policy or DueDiligencePolicy()
    trades = read_copy_trades(path)
    pnls = [trade.net_profit for trade in trades]
    gains = sum(value for value in pnls if value > 0)
    losses = -sum(value for value in pnls if value < 0)
    profit_factor = gains / losses if losses else None
    maximum_drawdown, maximum_drawdown_pct = _maximum_drawdown(pnls, starting_equity)
    history_days = (trades[-1].closed_at - trades[0].opened_at).total_seconds() / 86400
    monthly: dict[str, float] = {}
    for trade in trades:
        key = trade.closed_at.strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0.0) + trade.net_profit
    profitable_month_ratio = sum(value > 0 for value in monthly.values()) / len(monthly)
    largest_win = max(pnls)
    top_trade_share = largest_win / gains if gains > 0 else 1.0
    escalation = _loss_size_escalation(trades)
    checks = {
        "history_length": history_days >= policy.minimum_history_days,
        "closed_trade_count": len(trades) >= policy.minimum_closed_trades,
        "maximum_drawdown": maximum_drawdown_pct
        <= policy.maximum_drawdown_pct_of_starting_equity,
        "profit_factor": profit_factor is not None
        and profit_factor >= policy.minimum_profit_factor,
        "top_trade_concentration": top_trade_share <= policy.maximum_top_trade_profit_share,
        "loss_size_escalation": escalation <= policy.maximum_loss_size_escalation_ratio,
        "profitable_month_consistency": profitable_month_ratio
        >= policy.minimum_profitable_month_ratio,
    }
    warnings = []
    if escalation > policy.maximum_loss_size_escalation_ratio:
        warnings.append(
            "position size tends to increase after losses; investigate martingale behaviour"
        )
    if top_trade_share > policy.maximum_top_trade_profit_share:
        warnings.append("performance depends excessively on the largest winning trade")
    if len(monthly) < 24:
        warnings.append("history does not contain 24 distinct calendar months")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "provider_id": trades[0].provider_id,
        "source_file": path.name,
        "source_sha256": digest,
        "policy": asdict(policy),
        "metrics": {
            "closed_trades": len(trades),
            "history_days": round(history_days, 2),
            "calendar_months": len(monthly),
            "net_profit": round(sum(pnls), 2),
            "win_rate": sum(value > 0 for value in pnls) / len(pnls),
            "profit_factor": profit_factor,
            "maximum_drawdown": round(maximum_drawdown, 2),
            "maximum_drawdown_pct_of_starting_equity": maximum_drawdown_pct,
            "profitable_month_ratio": profitable_month_ratio,
            "largest_win_profit_share": top_trade_share,
            "median_volume_ratio_after_loss": escalation,
            "xauusd_trade_share": sum(
                "XAU" in trade.symbol or "GOLD" in trade.symbol for trade in trades
            )
            / len(trades),
        },
        "checks": checks,
        "warnings": warnings,
        "paper_shadow_eligible": all(checks.values()),
        "real_money_enabled": False,
        "research_only": True,
    }


def write_audit(report: dict[str, object], output_root: Path) -> Path:
    report_id = "copy-audit-" + hashlib.sha256(
        json.dumps(report, sort_keys=True).encode()
    ).hexdigest()[:16]
    directory = output_root / report_id
    directory.mkdir(parents=True, exist_ok=False)
    destination = directory / "report.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return destination
