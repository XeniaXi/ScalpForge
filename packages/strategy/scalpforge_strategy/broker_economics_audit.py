from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class BrokerEconomicsConfig:
    primary_latency_seconds: float = 0.05
    commission_round_trip_stress_bps: tuple[float, ...] = (0.0, 0.5, 1.0, 2.0)
    interest_day_count: int = 360
    ignore_positive_swap_credit: bool = True
    schema_revision: int = 1


def run_broker_economics_audit(
    tick_replay_report: Path,
    symbol_spec: Path,
    output_root: Path,
    config: BrokerEconomicsConfig | None = None,
) -> dict[str, object]:
    cfg = config or BrokerEconomicsConfig()
    replay = _json(tick_replay_report)
    spec = _spec(symbol_spec)
    _validate(replay, spec)
    ledger_path = Path(str(replay["tick_replay_ledger"]["path"]))
    if _sha256(ledger_path) != replay["tick_replay_ledger"]["sha256"]:
        raise ValueError("tick replay ledger hash does not match its report")
    rows = _ledger(ledger_path, cfg.primary_latency_seconds)
    valid = [row for row in rows if row["valid"]]
    commission = {
        f"{cost:g}": _metrics([float(row["net_bps"]) - cost for row in valid])
        for cost in cfg.commission_round_trip_stress_bps
    }
    rollover = _rollover_sensitivity(valid, spec, cfg)
    terms_complete = (
        spec["commission_status"] != "not_exposed_by_mt4_symbol_api"
        and spec["triple_swap_weekday_status"] != "not_exposed_by_mt4_symbol_api"
    )
    identity = {
        "candidate_specification_hash": replay["candidate_specification_hash"],
        "tick_replay_report_sha256": _sha256(tick_replay_report),
        "symbol_spec_sha256": _sha256(symbol_spec),
        "config": asdict(cfg),
    }
    report_id = "broker-economics-audit-" + _digest(identity)[:16]
    report: dict[str, object] = {
        "report_id": report_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "candidate_id": replay["candidate_id"],
        "candidate_specification_hash": replay["candidate_specification_hash"],
        "tick_replay_report_sha256": _sha256(tick_replay_report),
        "symbol_spec_sha256": _sha256(symbol_spec),
        "holdout_evaluated": False,
        "broker_specification": spec,
        "config": asdict(cfg),
        "primary_latency_trade_count": len(rows),
        "valid_trade_count": len(valid),
        "commission_stress_metrics": commission,
        "rollover_hour_utc_sensitivity": rollover,
        "broker_terms_complete": terms_complete,
        "stress_evidence_supportive": all(
            value["mean_net_bps"] > 0 for value in commission.values()
        )
        and rollover["worst_hour_metrics"]["mean_net_bps"] > 0,
        "prospective_demo_readiness": "ready" if terms_complete else "blocked_metadata",
        "required_metadata": [
            name
            for name, unknown in (
                (
                    "commission schedule",
                    spec["commission_status"] == "not_exposed_by_mt4_symbol_api",
                ),
                (
                    "triple-swap weekday and rollover time",
                    spec["triple_swap_weekday_status"] == "not_exposed_by_mt4_symbol_api",
                ),
            )
            if unknown
        ],
        "limitations": [
            "rollover time is sensitivity-tested across all UTC hours, not assumed",
            "positive short swap credits are ignored conservatively by default",
            "interest swap uses a conservative 360-day denominator",
            "historical JForex fills remain a different venue from AvaTrade",
        ],
        "candidate_frozen": True,
        "research_only": True,
        "real_money_enabled": False,
    }
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _rollover_sensitivity(rows, spec, cfg):
    scenarios = {}
    commission = max(cfg.commission_round_trip_stress_bps)
    for hour in range(24):
        values = []
        crossed = 0
        for row in rows:
            swap = 0.0
            if _crosses_hour(row["entry_at"], row["exit_at"], hour):
                crossed += 1
                swap = _daily_swap_bps(row["side"], spec, cfg)
            values.append(float(row["net_bps"]) - commission + swap)
        scenarios[str(hour)] = {"rollover_crossings": crossed, **_metrics(values)}
    worst_hour = min(scenarios, key=lambda value: scenarios[value]["mean_net_bps"])
    return {
        "commission_stress_bps": commission,
        "worst_hour_utc": int(worst_hour),
        "worst_hour_metrics": scenarios[worst_hour],
        "hours": scenarios,
    }


def _daily_swap_bps(side, spec, cfg):
    value = spec["swap_long"] if side == 1 else spec["swap_short"]
    if spec["swap_type"] != 2:
        raise ValueError("only annual-interest swap type 2 is supported")
    bps = float(value) * 100 / cfg.interest_day_count
    return min(0.0, bps) if cfg.ignore_positive_swap_credit else bps


def _crosses_hour(start, end, hour):
    boundary = start.replace(hour=hour, minute=0, second=0, microsecond=0)
    if boundary <= start:
        boundary += timedelta(days=1)
    return boundary <= end


def _metrics(values):
    gains = math.fsum(value for value in values if value > 0)
    losses = abs(math.fsum(value for value in values if value < 0))
    return {
        "trade_count": len(values),
        "mean_net_bps": math.fsum(values) / len(values) if values else 0.0,
        "total_net_bps": math.fsum(values),
        "win_rate": sum(value > 0 for value in values) / len(values) if values else 0.0,
        "profit_factor": gains / losses if losses else None,
    }


def _ledger(path, latency):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if float(row["latency_seconds"]) != latency:
            continue
        for name in ("entry_at", "exit_at"):
            if row.get(name):
                row[name] = _utc(row[name])
        rows.append(row)
    return rows


def _spec(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError("symbol specification must contain exactly one data row")
    row = rows[0]
    numeric = {
        "digits": int,
        "point": float,
        "contract_size": float,
        "min_lot": float,
        "max_lot": float,
        "lot_step": float,
        "tick_size": float,
        "tick_value": float,
        "stop_level_points": float,
        "freeze_level_points": float,
        "swap_long": float,
        "swap_short": float,
        "swap_type": lambda value: int(float(value)),
        "profit_calculation_mode": lambda value: int(float(value)),
        "margin_calculation_mode": lambda value: int(float(value)),
        "margin_required": float,
        "trade_allowed": lambda value: bool(int(float(value))),
        "current_spread_points": float,
    }
    return {
        key: convert(row[key]) if key in numeric else row[key] for key, convert in numeric.items()
    } | {
        key: row[key]
        for key in (
            "captured_utc",
            "broker",
            "server",
            "symbol",
            "account_currency",
            "commission_status",
            "triple_swap_weekday_status",
        )
    }


def _validate(replay, spec):
    if replay.get("candidate_id") != "trend_continuation_1h_v1":
        raise ValueError("broker audit only supports frozen Candidate A")
    if replay.get("holdout_evaluated") is not False:
        raise ValueError("tick replay must not evaluate the sealed holdout")
    if spec["broker"] != "Ava Trade Ltd." or spec["symbol"] != "GOLD":
        raise ValueError("expected AvaTrade GOLD specification")
    if not spec["trade_allowed"]:
        raise ValueError("broker reports GOLD trading is unavailable")


def _utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
