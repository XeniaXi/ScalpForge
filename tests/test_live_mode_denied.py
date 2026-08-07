import pytest
from pydantic import ValidationError
from scalpforge_core.config import Settings


def test_live_mode_is_rejected() -> None:
    with pytest.raises(ValidationError, match="intentionally disabled"):
        Settings(trading_mode="live")
