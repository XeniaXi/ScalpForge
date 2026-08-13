from pathlib import Path


def test_recorder_captures_broker_economics_without_claiming_unknowns() -> None:
    source = Path("mt4/Experts/ScalpForgeRecorder.mq4").read_text(encoding="utf-8")
    assert "MODE_SWAPTYPE" in source
    assert "MODE_PROFITCALCMODE" in source
    assert "MODE_MARGINCALCMODE" in source
    assert '"commission_status"' in source
    assert source.count('"not_exposed_by_mt4_symbol_api"') >= 2
    assert "OrderSend" not in source
