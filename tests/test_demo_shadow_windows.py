from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "ops" / "windows" / "Install-ScalpForgeDemoShadow.ps1"
RUNNER = ROOT / "ops" / "windows" / "Run-ScalpForgeDemoShadow.ps1"


def test_installer_is_read_only_and_prevents_overlap() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert "scalpforge-init-demo-shadow.exe" in text
    assert '"-m", "scalpforge_strategy.demo_shadow_scheduled_cli"' in text
    assert "-Execute $python" in text
    assert '"--protocol"' in text
    assert '"--source-dir"' in text
    assert "$verification = & $initializer --verify $protocolPath" in text
    assert "python -m scalpforge_strategy.demo_shadow_protocol_cli" not in text
    assert "-MultipleInstances IgnoreNew" in text
    assert "-Second 5" in text
    assert 'TaskName "ScalpForge-Demo-Shadow"' in text
    assert "OrderSend" not in text
    assert "submitOrder" not in text


def test_runner_persists_output_and_uses_frozen_engine() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert "scalpforge-run-demo-shadow.exe" in text
    assert "Out-File -LiteralPath $log -Append" in text
    assert "order_submission_enabled = $false" in text
    assert "Start-Process" not in text
