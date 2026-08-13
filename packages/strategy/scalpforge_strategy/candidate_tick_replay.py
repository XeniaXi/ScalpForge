from __future__ import annotations

import bisect
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]


@dataclass(frozen=True)
class TickReplayConfig:
    latency_seconds: tuple[float, ...] = (0.05, 1.0, 5.0, 30.0, 60.0)
    maximum_quote_delay_seconds: float = 5.0
    slippage_bps_per_side: float = 0.5
    schema_revision: int = 1


def run_candidate_tick_replay(
    robustness_report: Path,
    tick_manifest: Path,
    output_root: Path,
    config: TickReplayConfig | None = None,
) -> dict[str, object]:
    cfg = config or TickReplayConfig()
    source_report = _json(robustness_report)
    tick_meta = _json(tick_manifest)
    _validate(source_report, tick_meta)
    ledger_path = Path(str(source_report["trade_ledger"]["path"]))
    ledger = _ledger(ledger_path)
    if _sha256(ledger_path) != source_report["trade_ledger"]["sha256"]:
        raise ValueError("Candidate A trade ledger hash does not match its report")
    partitions = _partitions_by_day(tick_manifest, tick_meta)
    rows = _replay(ledger, partitions, cfg)
    scenario_metrics = {
        _latency_key(latency): _metrics([row for row in rows if row["latency_seconds"] == latency])
        for latency in cfg.latency_seconds
    }
    primary = scenario_metrics[_latency_key(cfg.latency_seconds[0])]
    identity = {
        "candidate_specification_hash": source_report["specification_hash"],
        "tick_dataset_id": tick_meta["dataset_id"],
        "config": asdict(cfg),
    }
    report_id = "candidate-tick-replay-" + _digest(identity)[:16]
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    ledger_out = root / "tick-replay-ledger.jsonl"
    payload = "".join(json.dumps(_serializable(row), sort_keys=True) + "\n" for row in rows)
    ledger_out.write_text(payload, encoding="utf-8")
    report: dict[str, object] = {
        "report_id": report_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "candidate_id": source_report["candidate_id"],
        "candidate_specification_hash": source_report["specification_hash"],
        "source_robustness_report": str(robustness_report.resolve()),
        "source_robustness_report_sha256": _sha256(robustness_report),
        "source_trade_ledger_sha256": source_report["trade_ledger"]["sha256"],
        "tick_dataset_id": tick_meta["dataset_id"],
        "tick_manifest_sha256": _sha256(tick_manifest),
        "development_start": source_report["development_start"],
        "development_end_exclusive": source_report["development_end_exclusive"],
        "holdout_evaluated": False,
        "config": asdict(cfg),
        "source_trade_count": len(ledger),
        "scenario_metrics": scenario_metrics,
        "primary_metrics": primary,
        "tick_replay_ledger": {
            "path": str(ledger_out.resolve()),
            "sha256": _sha256(ledger_out),
        },
        "advancement_interpretation": (
            "execution_evidence_supportive"
            if all(value["mean_net_bps"] > 0 for value in scenario_metrics.values())
            else "execution_evidence_not_supportive"
        ),
        "limitations": [
            "historical JForex quotes do not prove live fills or broker acceptance",
            "received timestamps equal event timestamps in this historical dataset",
            "commission and rollover swap are not included",
            "latency scenarios are fixed stresses and may not match measured live latency",
        ],
        "candidate_frozen": True,
        "research_only": True,
        "real_money_enabled": False,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _replay(ledger, partitions, cfg):
    by_day: dict[str, list[dict[str, object]]] = {}
    for trade in ledger:
        by_day.setdefault(trade["entry_at"].date().isoformat(), []).append(trade)
    output = []
    for day, trades in sorted(by_day.items()):
        required_days = {day}
        required_days.update(trade["exit_at"].date().isoformat() for trade in trades)
        tables = [
            pq.read_table(partitions[value], columns=["occurred_at", "bid", "ask"])
            for value in sorted(required_days)
            if value in partitions
        ]
        if not tables:
            for trade in trades:
                output.extend(_missing_rows(trade, cfg, "missing_tick_partition"))
            continue
        ticks = pa.concat_tables(tables).sort_by("occurred_at")
        times = [_utc(value) for value in ticks["occurred_at"].to_pylist()]
        bids = ticks["bid"].to_pylist()
        asks = ticks["ask"].to_pylist()
        for trade in trades:
            for latency in cfg.latency_seconds:
                output.append(_execute(trade, latency, times, bids, asks, cfg))
    return output


def _execute(trade, latency, times, bids, asks, cfg):
    entry_eligible = trade["entry_at"] + timedelta(seconds=latency)
    exit_eligible = trade["exit_at"] + timedelta(seconds=latency)
    entry_index = bisect.bisect_left(times, entry_eligible)
    exit_index = bisect.bisect_left(times, exit_eligible)
    reason = _boundary_reason(entry_index, entry_eligible, times, cfg)
    reason = reason or _boundary_reason(exit_index, exit_eligible, times, cfg)
    base = {
        "timestamp": trade["timestamp"],
        "side": trade["side"],
        "latency_seconds": latency,
        "scheduled_entry_at": trade["entry_at"],
        "scheduled_exit_at": trade["exit_at"],
        "proxy_net_bps": trade["base"],
        "valid": reason is None,
        "invalid_reason": reason,
    }
    if reason:
        return base
    entry_bid, entry_ask = float(bids[entry_index]), float(asks[entry_index])
    exit_bid, exit_ask = float(bids[exit_index]), float(asks[exit_index])
    if trade["side"] == 1:
        gross = (exit_bid / entry_ask - 1) * 10_000
    else:
        gross = (entry_bid / exit_ask - 1) * 10_000
    net = gross - 2 * cfg.slippage_bps_per_side
    return {
        **base,
        "entry_at": times[entry_index],
        "exit_at": times[exit_index],
        "entry_quote_delay_seconds": (times[entry_index] - entry_eligible).total_seconds(),
        "exit_quote_delay_seconds": (times[exit_index] - exit_eligible).total_seconds(),
        "entry_bid": entry_bid,
        "entry_ask": entry_ask,
        "exit_bid": exit_bid,
        "exit_ask": exit_ask,
        "gross_bps": gross,
        "net_bps": net,
        "difference_from_proxy_bps": net - float(trade["base"]),
    }


def _boundary_reason(index, eligible, times, cfg):
    if index >= len(times):
        return "no_quote_after_boundary"
    if (times[index] - eligible).total_seconds() > cfg.maximum_quote_delay_seconds:
        return "stale_boundary_quote"
    return None


def _missing_rows(trade, cfg, reason):
    return [
        {
            "timestamp": trade["timestamp"],
            "side": trade["side"],
            "latency_seconds": latency,
            "valid": False,
            "invalid_reason": reason,
        }
        for latency in cfg.latency_seconds
    ]


def _metrics(rows):
    valid = [row for row in rows if row["valid"]]
    values = [float(row["net_bps"]) for row in valid]
    proxy = [float(row["difference_from_proxy_bps"]) for row in valid]
    reasons: dict[str, int] = {}
    for row in rows:
        if row["invalid_reason"]:
            reasons[row["invalid_reason"]] = reasons.get(row["invalid_reason"], 0) + 1
    return {
        "candidate_count": len(rows),
        "valid_count": len(valid),
        "rejected_count": len(rows) - len(valid),
        "rejection_reasons": reasons,
        "execution_coverage_ratio": len(valid) / len(rows) if rows else 0.0,
        "mean_net_bps": _mean(values),
        "median_net_bps": _median(values),
        "profit_factor": _profit_factor(values),
        "win_rate": sum(value > 0 for value in values) / len(values) if values else 0.0,
        "mean_difference_from_proxy_bps": _mean(proxy),
        "total_net_bps": math.fsum(values),
    }


def _partitions_by_day(manifest, meta):
    root = manifest.resolve().parent
    result = {}
    for stored in meta.get("partitions", []):
        path = Path(str(stored)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("tick partition escapes its manifest directory")
        parts = path.parts
        year = next(value.split("=", 1)[1] for value in parts if value.startswith("year="))
        month = next(value.split("=", 1)[1] for value in parts if value.startswith("month="))
        day = next(value.split("=", 1)[1] for value in parts if value.startswith("day="))
        result[f"{year}-{month}-{day}"] = path
    return result


def _ledger(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        row["timestamp"] = _utc(row["timestamp"])
        row["entry_at"] = _utc(row["entry_at"])
        row["exit_at"] = _utc(row["exit_at"])
        rows.append(row)
    return rows


def _validate(report, ticks):
    if report.get("candidate_id") != "trend_continuation_1h_v1":
        raise ValueError("tick replay only supports frozen Candidate A")
    if report.get("holdout_evaluated") is not False:
        raise ValueError("source report must not evaluate the sealed holdout")
    if ticks.get("provider") != "dukascopy-jforex":
        raise ValueError("tick replay requires the reference JForex dataset")
    if ticks.get("instrument") != "XAUUSD":
        raise ValueError("tick replay requires XAUUSD")


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _serializable(row):
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in row.items()
    }


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _latency_key(value):
    return f"{value:g}s"


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0


def _median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _profit_factor(values):
    gains = math.fsum(value for value in values if value > 0)
    losses = abs(math.fsum(value for value in values if value < 0))
    return gains / losses if losses else None


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
