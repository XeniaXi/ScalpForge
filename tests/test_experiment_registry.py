import json

import pytest
from scalpforge_strategy.experiment_registry import (
    bonferroni_alpha,
    read_registry,
    register_experiment,
)


def test_registry_is_hash_chained_and_idempotent(tmp_path) -> None:
    path = tmp_path / "registry.jsonl"
    first = register_experiment(
        path,
        report_id="report-one",
        experiment_family="sequence",
        dataset_ids=("features-one", "structure-one"),
        hypothesis_count=20,
        holdout_evaluated=False,
    )
    assert first.per_hypothesis_alpha == 0.0025
    assert register_experiment(
        path,
        report_id="report-one",
        experiment_family="sequence",
        dataset_ids=("features-one", "structure-one"),
        hypothesis_count=20,
        holdout_evaluated=False,
    ) == first
    second = register_experiment(
        path,
        report_id="report-two",
        experiment_family="feasibility",
        dataset_ids=("features-one",),
        hypothesis_count=100,
        holdout_evaluated=False,
    )
    assert second.previous_hash == first.record_hash
    assert len(read_registry(path)) == 2


def test_registry_detects_history_rewrite(tmp_path) -> None:
    path = tmp_path / "registry.jsonl"
    register_experiment(
        path,
        report_id="report-one",
        experiment_family="sequence",
        dataset_ids=("features-one",),
        hypothesis_count=2,
        holdout_evaluated=False,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["hypothesis_count"] = 1
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        read_registry(path)


def test_multiple_testing_inputs_are_validated() -> None:
    with pytest.raises(ValueError, match="positive"):
        bonferroni_alpha(0)
