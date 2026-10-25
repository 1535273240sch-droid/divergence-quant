from pathlib import Path

import pytest

from dquant.settings import load_settings

ROOT = Path(__file__).resolve().parents[2]


def test_default_config_loads_demo_mode() -> None:
    settings = load_settings(ROOT / "config" / "config.yaml", env={})

    assert settings.exchange.mode == "demo"
    assert settings.exchange.margin_mode == "isolated"
    assert settings.timeframes.daily_close_tz == "Asia/Shanghai"
    assert settings.database.url.startswith("postgresql+asyncpg://")


def test_environment_can_override_connection_urls() -> None:
    settings = load_settings(
        ROOT / "config" / "config.yaml",
        env={
            "DQUANT_DATABASE_URL": "sqlite+aiosqlite:///./test.db",
            "DQUANT_REDIS_URL": "redis://example.test:6379/2",
        },
    )

    assert settings.database.url == "sqlite+aiosqlite:///./test.db"
    assert settings.redis.url == "redis://example.test:6379/2"


def test_live_mode_requires_explicit_confirmation(tmp_path: Path) -> None:
    config = tmp_path / "live.yaml"
    config.write_text(
        """
exchange:
  mode: live
  sub_account_only: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="LIVE_TRADING_CONFIRM=YES"):
        load_settings(config, env={})

    settings = load_settings(config, env={"LIVE_TRADING_CONFIRM": "YES"})
    assert settings.exchange.mode == "live"


def test_live_mode_rejects_non_sub_account_configuration(tmp_path: Path) -> None:
    config = tmp_path / "unsafe-live.yaml"
    config.write_text(
        """
exchange:
  mode: live
  sub_account_only: false
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sub_account_only"):
        load_settings(config, env={"LIVE_TRADING_CONFIRM": "YES"})
