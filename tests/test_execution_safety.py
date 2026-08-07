import pytest
from scalpforge_execution.brokers import MT4Bridge


@pytest.mark.asyncio
async def test_mt4_bridge_is_disabled() -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        await MT4Bridge().submit(None, None)  # type: ignore[arg-type]
