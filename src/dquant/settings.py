"""配置文件与环境变量加载。

配置来源优先级：YAML 文件 < 支持的环境变量覆盖。
敏感值只允许通过环境变量提供，不能写入仓库中的 YAML 文件。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExchangeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["okx"] = "okx"
    mode: Literal["demo", "live", "backtest"] = "demo"
    inst_type: Literal["SWAP"] = "SWAP"
    position_mode: Literal["long_short_mode"] = "long_short_mode"
    margin_mode: Literal["isolated"] = "isolated"
    sub_account_only: bool = True


class TimeframeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    regime: Literal["1D"] = "1D"
    divergence: Literal["4H"] = "4H"
    trigger: Literal["1H"] = "1H"
    daily_close_tz: str = "Asia/Shanghai"


class UniverseSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: Literal["USDT"] = "USDT"
    min_quote_volume_24h: int = Field(default=20_000_000, ge=0)
    min_history_bars: int = Field(default=200, ge=1)
    top_n_by_volume: int = Field(default=150, ge=1)
    whitelist: list[str] = Field(default_factory=list)
    blacklist: list[str] = Field(default_factory=list)


class RiskSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["small_account", "standard"] = "small_account"
    risk_per_trade: float = Field(default=0.015, gt=0, le=1)
    max_positions: int = Field(default=3, ge=1)
    max_same_direction: int = Field(default=2, ge=1)
    max_gross_leverage: float = Field(default=3, gt=0)
    daily_loss_limit: float = Field(default=0.03, gt=0, le=1)
    weekly_loss_limit: float = Field(default=0.06, gt=0, le=1)
    max_drawdown_kill: float = Field(default=0.15, gt=0, le=1)


class ExecutionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    demo_confirm_mode: Literal["auto"] = "auto"
    live_confirm_mode: Literal["manual", "auto"] = "manual"
    confirm_ttl_sec: int = Field(default=900, ge=60)


class AISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    timeout_sec: int = Field(default=12, ge=1)
    daily_budget_usd: float = Field(default=1.0, ge=0)


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "postgresql+asyncpg://dquant@localhost:5432/dquant"
    echo: bool = False
    pool_pre_ping: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if "://" not in value:
            raise ValueError("database.url must be a URL")
        return value


class RedisSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "redis://localhost:6379/0"
    decode_responses: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("redis.url must start with redis:// or rediss://")
        return value


class AppSettings(BaseModel):
    """应用运行时配置。"""

    model_config = ConfigDict(extra="forbid")

    exchange: ExchangeSettings = Field(default_factory=ExchangeSettings)
    timeframes: TimeframeSettings = Field(default_factory=TimeframeSettings)
    universe: UniverseSettings = Field(default_factory=UniverseSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    ai: AISettings = Field(default_factory=AISettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)


_ENV_PATHS: dict[str, tuple[str, ...]] = {
    "DQUANT_EXCHANGE_MODE": ("exchange", "mode"),
    "DQUANT_DATABASE_URL": ("database", "url"),
    "DQUANT_REDIS_URL": ("redis", "url"),
}
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: Any, env: Mapping[str, str]) -> Any:
    """递归替换 YAML 中的 `${NAME}` 占位符。"""

    if isinstance(value, dict):
        return {key: _expand_env(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item, env) for item in value]
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda match: env.get(match.group(1), match.group(0)), value)
    return value


def _set_nested(mapping: dict[str, Any], path: tuple[str, ...], value: str) -> None:
    target = mapping
    for key in path[:-1]:
        child = target.setdefault(key, {})
        if not isinstance(child, dict):
            raise ValueError(f"cannot override nested setting {'.'.join(path)}")
        target = child
    target[path[-1]] = value


def load_settings(
    config_path: str | Path = "config/config.yaml",
    *,
    env: Mapping[str, str] | None = None,
) -> AppSettings:
    """加载并校验配置。

    `env` 参数用于测试和一次性命令；生产代码默认读取当前进程环境变量。
    """

    environment = dict(os.environ if env is None else env)
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("top-level YAML value must be a mapping")

    expanded = _expand_env(raw, environment)
    for variable, nested_path in _ENV_PATHS.items():
        if variable in environment:
            _set_nested(expanded, nested_path, environment[variable])

    settings = AppSettings.model_validate(expanded)
    if settings.exchange.mode == "live":
        if environment.get("LIVE_TRADING_CONFIRM") != "YES":
            raise ValueError("live mode requires LIVE_TRADING_CONFIRM=YES")
        if not settings.exchange.sub_account_only:
            raise ValueError("live mode requires exchange.sub_account_only=true")
    return settings


def default_settings() -> AppSettings:
    """读取项目默认配置。"""

    config_path = os.environ.get("DQUANT_CONFIG_PATH", "config/config.yaml")
    return load_settings(config_path)
