import json

from scalpforge_strategy import demo_shadow_worker_cli as worker


def test_worker_runs_repeated_cycles_without_order_submission(tmp_path, monkeypatch) -> None:
    protocol = tmp_path / "protocol.json"
    protocol.write_text("{}", encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    calls = []

    def fake_run(protocol_path, source_dir):
        calls.append((protocol_path, source_dir))
        return {"event": "completed", "order_submission_enabled": False}

    monkeypatch.setattr(worker, "run_scheduled", fake_run)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    worker.run_worker(protocol, source, 300, maximum_cycles=2)

    assert len(calls) == 2
    log = next((tmp_path / "logs").glob("*-worker.jsonl"))
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [row["event"] for row in rows] == [
        "worker_started",
        "worker_cycle",
        "worker_cycle",
    ]
    assert all(row["order_submission_enabled"] is False for row in rows)
