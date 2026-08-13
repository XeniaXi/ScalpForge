from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class DemoShadowConfig:
    candidate_id: str = "trend_continuation_1h_v1"
    holding_seconds: int = 3600
    family_cooldown_seconds: int = 28_800
    minimum_path_efficiency: float = 0.35
    hypothetical_risk_per_trade_pct: float = 0.25
    maximum_hypothetical_daily_loss_pct: float = 1.0
    maximum_hypothetical_drawdown_pct: float = 6.0
    maximum_quote_age_seconds: float = 5.0
    minimum_calendar_weeks: int = 12
    minimum_closed_trades: int = 150
    minimum_active_days: int = 45
    minimum_execution_coverage_ratio: float = 0.98
    minimum_profit_factor: float = 1.10
    maximum_drawdown_pct_for_go: float = 6.0
    maximum_best_day_positive_profit_share: float = 0.20
    maximum_top_five_days_positive_profit_share: float = 0.50
    schema_revision: int = 1


def initialize_demo_shadow_protocol(
    robustness_report: Path,
    tick_replay_report: Path,
    broker_economics_report: Path,
    symbol_spec: Path,
    output_root: Path,
    config: DemoShadowConfig | None = None,
) -> dict[str, object]:
    cfg = config or DemoShadowConfig()
    robustness = _json(robustness_report)
    replay = _json(tick_replay_report)
    economics = _json(broker_economics_report)
    _validate(cfg, robustness, replay, economics)
    evidence = {
        "robustness_report": _evidence(robustness_report),
        "tick_replay_report": _evidence(tick_replay_report),
        "broker_economics_report": _evidence(broker_economics_report),
        "symbol_spec": _evidence(symbol_spec),
    }
    frozen = {
        "candidate_id": cfg.candidate_id,
        "candidate_specification_hash": robustness["specification_hash"],
        "holding_seconds": cfg.holding_seconds,
        "family_cooldown_seconds": cfg.family_cooldown_seconds,
        "minimum_path_efficiency": cfg.minimum_path_efficiency,
        "entry": "first_observed_avatrade_executable_side_after_signal",
        "exit": "first_observed_avatrade_executable_side_at_or_after_one_hour",
        "overlap": "one_position_maximum_ignore_signals_while_open",
        "sizing": "hypothetical_risk_only_no_order_submission",
        "news_policy": "record_for_attribution_do_not_filter_v1",
    }
    gates = {
        "minimum_calendar_weeks": cfg.minimum_calendar_weeks,
        "minimum_closed_trades": cfg.minimum_closed_trades,
        "minimum_active_days": cfg.minimum_active_days,
        "mean_net_bps_above_zero": True,
        "minimum_profit_factor": cfg.minimum_profit_factor,
        "minimum_execution_coverage_ratio": cfg.minimum_execution_coverage_ratio,
        "maximum_drawdown_pct": cfg.maximum_drawdown_pct_for_go,
        "maximum_best_day_positive_profit_share": (cfg.maximum_best_day_positive_profit_share),
        "maximum_top_five_days_positive_profit_share": (
            cfg.maximum_top_five_days_positive_profit_share
        ),
        "both_directions_nonnegative": True,
        "first_and_second_halves_nonnegative": True,
        "unresolved_data_or_execution_failures": 0,
    }
    identity = {"frozen_specification": frozen, "gates": gates, "evidence": evidence}
    protocol_id = "demo-shadow-" + _digest(identity)[:16]
    root = output_root / protocol_id
    if root.exists():
        existing = _json(root / "protocol.json")
        if existing["protocol_hash"] != _digest(identity):
            raise ValueError("existing protocol directory has different frozen contents")
        return existing
    root.mkdir(parents=True, exist_ok=False)
    ledgers = {}
    for name in ("signals", "fills", "health", "events", "weekly-evaluations"):
        path = root / f"{name}.jsonl"
        path.write_text("", encoding="utf-8")
        ledgers[name] = str(path.resolve())
    protocol = {
        "protocol_id": protocol_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "protocol_hash": _digest(identity),
        "frozen_specification": frozen,
        "acceptance_gates": gates,
        "risk_limits": {
            "hypothetical_risk_per_trade_pct": cfg.hypothetical_risk_per_trade_pct,
            "maximum_hypothetical_daily_loss_pct": (cfg.maximum_hypothetical_daily_loss_pct),
            "maximum_hypothetical_drawdown_pct": (cfg.maximum_hypothetical_drawdown_pct),
            "maximum_quote_age_seconds": cfg.maximum_quote_age_seconds,
        },
        "evidence": evidence,
        "development_decision": robustness["decision"],
        "execution_interpretation": replay["advancement_interpretation"],
        "economics_stress_supportive": economics["stress_evidence_supportive"],
        "broker_terms_resolution": {
            "commission": "zero_for_standard_gold_cfd_per_official_avatrade_terms",
            "rollover_time": "22:00_utc_per_official_avatrade_help_centre",
            "triple_swap_weekday": "wednesday_per_official_avatrade_help_centre",
            "verified_at": datetime.now(UTC).date().isoformat(),
            "source_urls": [
                "https://www.avatrade.com/commodities/gold",
                "https://www.avatrade.com/trading-info/our-fees-charges",
                "https://support.avatrade.com/hc/fi/articles/360001783591-Are-there-costs-or-fees-incurred-when-trading-CFDs",
            ],
        },
        "ledgers": ledgers,
        "initial_status": "ready_for_read_only_shadow_signal_engine",
        "holdout_evaluated": False,
        "candidate_frozen": True,
        "order_submission_enabled": False,
        "research_only": True,
        "real_money_enabled": False,
    }
    (root / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n", encoding="utf-8")
    return protocol


def verify_protocol(protocol_path: Path) -> dict[str, object]:
    protocol = _json(protocol_path)
    evidence_ok = all(
        Path(str(item["path"])).exists() and _sha256(Path(str(item["path"]))) == item["sha256"]
        for item in protocol["evidence"].values()
    )
    ledgers_ok = all(Path(path).exists() for path in protocol["ledgers"].values())
    safe = (
        evidence_ok
        and ledgers_ok
        and protocol["order_submission_enabled"] is False
        and protocol["real_money_enabled"] is False
        and protocol["holdout_evaluated"] is False
    )
    return {
        "protocol_id": protocol["protocol_id"],
        "evidence_hashes_valid": evidence_ok,
        "ledgers_present": ledgers_ok,
        "safety_flags_valid": safe,
        "ready": safe,
    }


def _validate(cfg, robustness, replay, economics):
    reports = (robustness, replay, economics)
    if any(report.get("candidate_id") != cfg.candidate_id for report in reports):
        raise ValueError("evidence does not reference frozen Candidate A")
    if any(report.get("holdout_evaluated") is not False for report in reports):
        raise ValueError("sealed holdout must remain unevaluated")
    spec_hash = robustness["specification_hash"]
    if replay["candidate_specification_hash"] != spec_hash:
        raise ValueError("tick replay candidate hash does not align")
    if economics["candidate_specification_hash"] != spec_hash:
        raise ValueError("broker economics candidate hash does not align")
    if replay.get("advancement_interpretation") != "execution_evidence_supportive":
        raise ValueError("raw-tick execution evidence is not supportive")
    if economics.get("stress_evidence_supportive") is not True:
        raise ValueError("broker economics stress evidence is not supportive")
    if cfg.holding_seconds != 3600 or cfg.family_cooldown_seconds != 28_800:
        raise ValueError("Candidate A timing is frozen")


def _evidence(path):
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
