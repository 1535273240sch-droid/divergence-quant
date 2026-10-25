"""回测配置与时间口径。

Candle.ts_ms 在本模块中解释为 K 线开盘时间（UTC 毫秒）。
一条 K 线只有在其开盘时间加上周期长度之后才可被策略读取；1D 按北京时间和
OKX `bar=1D` 规则对齐到北京时间 00:00 开盘。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dquant.contracts import CandleBar

ENGINE_VERSION = "0.1.0"
FEE_MODEL_VERSION = "fee-v1"
SLIPPAGE_MODEL_VERSION = "slippage-v1"
FUNDING_MODEL_VERSION = "funding-v1"

MILLISECONDS_PER_DAY = 86_400_000
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

BAR_DURATION_MS: dict[CandleBar, int] = {
    CandleBar.ONE_HOUR: 3_600_000,
    CandleBar.FOUR_HOUR: 14_400_000,
    CandleBar.ONE_DAY: MILLISECONDS_PER_DAY,
}

BAR_RANK: dict[CandleBar, int] = {
    CandleBar.ONE_HOUR: 0,
    CandleBar.FOUR_HOUR: 1,
    CandleBar.ONE_DAY: 2,
}

PERIODS_PER_YEAR: dict[CandleBar, int] = {
    CandleBar.ONE_HOUR: 24 * 365,
    CandleBar.FOUR_HOUR: 6 * 365,
    CandleBar.ONE_DAY: 365,
}


class BacktestConfig(BaseModel):
    """单次回测的确定性参数。

    手续费、滑点和杠杆只影响回测撮合，不改变 M03/M04/M07 的信号代码。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_equity: Decimal = Field(default=Decimal("10000"), gt=0)
    execution_bar: CandleBar = CandleBar.ONE_HOUR
    fee_maker: Decimal = Field(default=Decimal("0.0002"), ge=0, lt=1)
    fee_taker: Decimal = Field(default=Decimal("0.0005"), ge=0, lt=1)
    slippage: Decimal = Field(default=Decimal("0.0005"), ge=0, lt=1)
    leverage: Decimal = Field(default=Decimal("3"), gt=0)
    limit_order_ttl_bars: int = Field(default=1, ge=1)
    start_ts_ms: int | None = Field(default=None, ge=0)
    end_ts_ms: int | None = Field(default=None, ge=0)
    timezone: str = "Asia/Shanghai"
    random_seed: int | None = None
    run_id: str | None = None
    close_positions_at_end: bool = True

    @model_validator(mode="after")
    def validate_window_and_timezone(self) -> BacktestConfig:
        if self.start_ts_ms is not None and self.end_ts_ms is not None:
            if self.start_ts_ms >= self.end_ts_ms:
                raise ValueError("start_ts_ms must be earlier than end_ts_ms")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {self.timezone}") from exc
        if self.run_id is not None and not self.run_id.strip():
            raise ValueError("run_id must not be blank")
        return self

    def with_window(self, start_ts_ms: int, end_ts_ms: int) -> BacktestConfig:
        """复制配置并替换交易窗口，供 Walk-Forward 使用。"""

        return self.model_copy(
            update={"start_ts_ms": start_ts_ms, "end_ts_ms": end_ts_ms, "run_id": None}
        )


def bar_duration_ms(bar: CandleBar) -> int:
    """返回 K 线周期长度。"""

    return BAR_DURATION_MS[bar]


def close_ts_ms(open_ts_ms: int, bar: CandleBar) -> int:
    """由开盘时间得到收盘时间。"""

    return open_ts_ms + bar_duration_ms(bar)


def is_bar_aligned(ts_ms: int, bar: CandleBar) -> bool:
    """校验 K 线开盘时间是否符合周期对齐规则。"""

    if ts_ms < 0:
        return False
    try:
        moment = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return False
    if moment.minute != 0 or moment.second != 0 or moment.microsecond != 0:
        return False
    if bar is CandleBar.ONE_HOUR:
        return True
    if bar is CandleBar.FOUR_HOUR:
        return moment.hour % 4 == 0
    local = moment.astimezone(SHANGHAI_TZ)
    return local.hour == 0 and local.minute == 0 and local.second == 0


def decimal_key(value: Decimal) -> str:
    """把 Decimal 规范化为稳定字符串，用于指纹和哈希。"""

    return format(value.normalize(), "f")


def config_payload(config: BacktestConfig) -> dict[str, Any]:
    """返回不包含 run_id 的配置字典；run_id 不改变回测行为。"""

    payload = config.model_dump(mode="json")
    payload.pop("run_id", None)
    return payload


def compute_config_hash(config: BacktestConfig) -> str:
    """计算配置哈希，用于复现实验记录。"""

    encoded = json.dumps(
        config_payload(config),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compute_run_id(
    *,
    config_hash: str,
    data_fingerprint: str,
    strategy_name: str,
    strategy_version: str,
    strategy_params: dict[str, Any],
) -> str:
    """由配置、行情和策略身份生成确定性 run_id。"""

    encoded = json.dumps(
        {
            "config_hash": config_hash,
            "data_fingerprint": data_fingerprint,
            "strategy_name": strategy_name,
            "strategy_version": strategy_version,
            "strategy_params": strategy_params,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"bt-{hashlib.sha256(encoded).hexdigest()[:16]}"
