from pathlib import Path

import pytest
from scalpforge_execution.brokers import MT4Bridge


@pytest.mark.asyncio
async def test_mt4_bridge_is_disabled() -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        await MT4Bridge().submit(None, None)  # type: ignore[arg-type]


def test_jforex_exporter_has_no_execution_surface() -> None:
    source = Path("jforex/Strategies/ScalpForgeHistoricalExporter.java").read_text(
        encoding="utf-8"
    )
    forbidden = ("IEngine", "submitOrder", "OrderCommand", "getEngine(")
    assert not any(token in source for token in forbidden)
    assert "IHistory" in source
    assert "getTicks(" in source
    assert "external_non_executable" in source


def test_jforex_market_hours_exporter_has_no_execution_surface() -> None:
    source = Path("jforex/Strategies/ScalpForgeMarketHoursExporter.java").read_text(
        encoding="utf-8"
    )
    forbidden = ("IEngine", "submitOrder", "OrderCommand", "getEngine(")
    assert not any(token in source for token in forbidden)
    assert "getOfflineTimeDomains(start, end, instrument)" in source
    assert "IInstrumentStatusMessage" in source
    assert "external_non_executable" in source
