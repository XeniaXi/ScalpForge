import csv
import hashlib
import json
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class RawTrade:
    opened_at: datetime
    closed_at: datetime
    symbol: str
    side: str
    volume: float
    net_profit: float


@dataclass(frozen=True)
class CashFlow:
    occurred_at: datetime
    amount: float


@dataclass(frozen=True)
class Episode:
    symbol: str
    side: str
    opened_at: datetime
    closed_at: datetime
    ticket_count: int
    total_volume: float
    maximum_concurrent_volume: float
    net_profit: float


def _number(value: str) -> float:
    return float(value.replace(" ", "") or 0)


def read_mql5_account(
    path: Path, *, source_utc_offset_hours: float
) -> tuple[list[RawTrade], list[CashFlow], int]:
    offset = timezone(timedelta(hours=source_utc_offset_hours))
    trades: list[RawTrade] = []
    cash_flows: list[CashFlow] = []
    skipped_orders = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        header = next(reader, None)
        if not header or header[:5] != ["Time", "Type", "Volume", "Symbol", "Price"]:
            raise ValueError("unsupported MQL5 history header")
        detailed = len(header) == 13 and header[5:7] == ["S/L", "T/P"]
        if len(header) not in {11, 13}:
            raise ValueError("unsupported MQL5 history column count")
        close_index = 7 if detailed else 6
        commission_index = 9 if detailed else 8
        swap_index = 10 if detailed else 9
        profit_index = 11 if detailed else 10
        for number, row in enumerate(reader, start=2):
            if not row or all(not value.strip() for value in row):
                continue
            if len(row) != len(header):
                raise ValueError(f"row {number}: expected {len(header)} columns")
            kind = row[1].strip().lower()
            occurred = datetime.strptime(row[0], "%Y.%m.%d %H:%M:%S").replace(
                tzinfo=offset
            )
            if kind == "balance":
                cash_flows.append(
                    CashFlow(
                        occurred_at=occurred.astimezone(UTC),
                        amount=_number(row[profit_index]),
                    )
                )
                continue
            if kind not in {"buy", "sell"}:
                skipped_orders += 1
                continue
            closed = datetime.strptime(row[close_index], "%Y.%m.%d %H:%M:%S").replace(
                tzinfo=offset
            )
            trades.append(
                RawTrade(
                    opened_at=occurred.astimezone(UTC),
                    closed_at=closed.astimezone(UTC),
                    symbol=row[3].strip().upper(),
                    side=kind,
                    volume=_number(row[2]),
                    net_profit=_number(row[profit_index])
                    + _number(row[commission_index])
                    + _number(row[swap_index]),
                )
            )
    if not trades:
        raise ValueError("history has no closed trades")
    return trades, cash_flows, skipped_orders


def _maximum_concurrent_volume(trades: list[RawTrade]) -> tuple[float, int]:
    events = []
    for trade in trades:
        events.extend(
            [
                (trade.opened_at, 1, trade.volume, 1),
                (trade.closed_at, 0, -trade.volume, -1),
            ]
        )
    volume = 0.0
    tickets = 0
    maximum_volume = 0.0
    maximum_tickets = 0
    for _, _, change, ticket_change in sorted(events):
        volume += change
        tickets += ticket_change
        maximum_volume = max(maximum_volume, volume)
        maximum_tickets = max(maximum_tickets, tickets)
    return maximum_volume, maximum_tickets


def build_episodes(trades: list[RawTrade]) -> list[Episode]:
    episodes = []
    for key in sorted({(trade.symbol, trade.side) for trade in trades}):
        candidates = sorted(
            (trade for trade in trades if (trade.symbol, trade.side) == key),
            key=lambda trade: trade.opened_at,
        )
        current: list[RawTrade] = []
        current_end = datetime.min.replace(tzinfo=UTC)
        for trade in candidates:
            if current and trade.opened_at > current_end:
                maximum, _ = _maximum_concurrent_volume(current)
                episodes.append(
                    Episode(
                        symbol=key[0],
                        side=key[1],
                        opened_at=current[0].opened_at,
                        closed_at=current_end,
                        ticket_count=len(current),
                        total_volume=sum(item.volume for item in current),
                        maximum_concurrent_volume=maximum,
                        net_profit=sum(item.net_profit for item in current),
                    )
                )
                current = []
            current.append(trade)
            current_end = max(current_end, trade.closed_at)
        if current:
            maximum, _ = _maximum_concurrent_volume(current)
            episodes.append(
                Episode(
                    symbol=key[0],
                    side=key[1],
                    opened_at=current[0].opened_at,
                    closed_at=current_end,
                    ticket_count=len(current),
                    total_volume=sum(item.volume for item in current),
                    maximum_concurrent_volume=maximum,
                    net_profit=sum(item.net_profit for item in current),
                )
            )
    return sorted(episodes, key=lambda episode: episode.opened_at)


def _closed_balance_drawdown(
    trades: list[RawTrade], cash_flows: list[CashFlow]
) -> tuple[float, float | None, float]:
    events = [(flow.occurred_at, 0, "cash", flow.amount) for flow in cash_flows]
    events += [(trade.closed_at, 1, "pnl", trade.net_profit) for trade in trades]
    balance = peak = 0.0
    maximum_amount = 0.0
    maximum_pct: float | None = None
    for _, _, kind, amount in sorted(events):
        if kind == "cash":
            balance += amount
            peak += amount
            peak = max(peak, balance)
        else:
            balance += amount
            peak = max(peak, balance)
            drawdown = peak - balance
            maximum_amount = max(maximum_amount, drawdown)
            if peak > 0:
                maximum_pct = max(maximum_pct or 0.0, drawdown / peak * 100)
    return maximum_amount, maximum_pct, balance


def run_episode_audit(
    source: Path, *, provider_id: str, source_utc_offset_hours: float
) -> dict[str, object]:
    trades, cash_flows, skipped_orders = read_mql5_account(
        source, source_utc_offset_hours=source_utc_offset_hours
    )
    episodes = build_episodes(trades)
    episode_pnls = [episode.net_profit for episode in episodes]
    ticket_pnls = [trade.net_profit for trade in trades]
    gains = sum(value for value in episode_pnls if value > 0)
    losses = -sum(value for value in episode_pnls if value < 0)
    maximum_volume, maximum_tickets = _maximum_concurrent_volume(trades)
    drawdown, drawdown_pct, ending_balance = _closed_balance_drawdown(trades, cash_flows)
    day_profit: dict[str, float] = {}
    for episode in episodes:
        day = episode.closed_at.date().isoformat()
        day_profit[day] = day_profit.get(day, 0.0) + episode.net_profit
    profitable_total = sum(value for value in episode_pnls if value > 0)
    top_episode_share = max(episode_pnls) / profitable_total if profitable_total else 1.0
    top_day_share = max(day_profit.values()) / profitable_total if profitable_total else 1.0
    fixed_size_profit = sum(trade.net_profit * 0.01 / trade.volume for trade in trades)
    episode_size_ratios = [
        current.total_volume / previous.total_volume
        for previous, current in zip(episodes, episodes[1:], strict=False)
        if previous.net_profit < 0 and previous.total_volume > 0
    ]
    warnings = ["drawdown uses closed balance only; floating intratrade drawdown is unavailable"]
    history_days = max(trade.closed_at for trade in trades) - min(
        trade.opened_at for trade in trades
    )
    if history_days.days < 730:
        warnings.append("history is shorter than the 730-day paper-shadow gate")
    if top_day_share > 0.25:
        warnings.append("more than 25% of profitable episode P&L is concentrated in one day")
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "provider_id": provider_id,
        "source_file": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_utc_offset_hours": source_utc_offset_hours,
        "cash_flows": {
            "count": len(cash_flows),
            "deposits": sum(flow.amount for flow in cash_flows if flow.amount > 0),
            "withdrawals": sum(flow.amount for flow in cash_flows if flow.amount < 0),
            "net": sum(flow.amount for flow in cash_flows),
        },
        "metrics": {
            "history_days": history_days.total_seconds() / 86400,
            "closed_trades": len(trades),
            "skipped_pending_orders": skipped_orders,
            "episode_count": len(episodes),
            "multi_ticket_episode_ratio": sum(item.ticket_count > 1 for item in episodes)
            / len(episodes),
            "ticket_net_profit": sum(ticket_pnls),
            "episode_win_rate": sum(value > 0 for value in episode_pnls) / len(episodes),
            "episode_profit_factor": gains / losses if losses else None,
            "closed_balance_drawdown": drawdown,
            "closed_balance_drawdown_pct": drawdown_pct,
            "reconstructed_ending_closed_balance": ending_balance,
            "maximum_concurrent_volume": maximum_volume,
            "maximum_concurrent_tickets": maximum_tickets,
            "maximum_episode_tickets": max(item.ticket_count for item in episodes),
            "maximum_episode_total_volume": max(item.total_volume for item in episodes),
            "top_profitable_episode_share": top_episode_share,
            "top_profitable_day_share": top_day_share,
            "fixed_0_01_volume_net_profit": fixed_size_profit,
            "median_episode_size_ratio_after_loss": statistics.median(episode_size_ratios)
            if episode_size_ratios
            else 1.0,
        },
        "symbol_metrics": {
            symbol: {
                "trades": sum(trade.symbol == symbol for trade in trades),
                "net_profit": sum(trade.net_profit for trade in trades if trade.symbol == symbol),
            }
            for symbol in sorted({trade.symbol for trade in trades})
        },
        "warnings": warnings,
        "paper_shadow_eligible": False,
        "eligibility_reason": "requires prospective paper shadowing and floating-equity evidence",
        "research_only": True,
        "real_money_enabled": False,
    }


def write_episode_audit(report: dict[str, object], output_root: Path) -> Path:
    report_id = "copy-episode-audit-" + hashlib.sha256(
        json.dumps(report, sort_keys=True).encode()
    ).hexdigest()[:16]
    directory = output_root / report_id
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "report.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path
