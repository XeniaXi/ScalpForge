import json

from scalpforge_strategy import demo_shadow_scheduled_cli as scheduled


def test_scheduled_runner_records_start_and_completion(tmp_path, monkeypatch) -> None:
    protocol = tmp_path / "protocol.json"
    protocol.write_text("{}", encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(
        scheduled,
        "_run_engine",
        lambda protocol_path, source_dir: {
            "status": "healthy",
            "order_submission_enabled": False,
        },
    )
    result = scheduled.run_scheduled(protocol, source)
    assert result["event"] == "completed"
    rows = [
        json.loads(line)
        for line in next((tmp_path / "logs").glob("*.jsonl")).read_text().splitlines()
    ]
    assert [row["event"] for row in rows] == ["started", "loading_engine", "completed"]
    assert all(row["order_submission_enabled"] is False for row in rows)
    assert not (tmp_path / "scheduled-run.lock").exists()


def test_scheduled_runner_skips_live_lock(tmp_path) -> None:
    protocol = tmp_path / "protocol.json"
    protocol.write_text("{}", encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    (tmp_path / "scheduled-run.lock").write_text("locked", encoding="utf-8")
    result = scheduled.run_scheduled(protocol, source)
    assert result["event"] == "skipped_overlap"
