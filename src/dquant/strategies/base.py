"""策略框架的基础类型。

本模块只负责把已确认的行情、选币结果和背离结果转换成公共 ``Signal``。
策略层不访问网络、不写数据库，也不创建订单。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from dquant.contracts import (
    Candle,
    CandleBar,
    DivergenceSignal,
    Fill,
    Position,
    RegimeState,
    ScreenerCandidate,
    Signal,
)


class StrategyInputError(ValueError):
    """策略输入不满足公共契约或防未来函数要求。"""


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """单根已收盘 K 线上的策略输入。

    ``trigger_candles`` 必须按时间升序排列，最后一根是当前已收盘 K 线。策略可以通过
    ``range_candles`` 接收独立的 4H 区间数据；未提供时不会把短周期触发数据误当作完整区间。
    """

    candidate: ScreenerCandidate
    divergence: DivergenceSignal | None
    trigger_candles: tuple[Candle, ...]
    atr: Decimal
    range_candles: tuple[Candle, ...] | None = None
    timeframe_candles: Mapping[CandleBar, Sequence[Candle]] = field(default_factory=dict)
    ma200: Decimal | None = None
    btc_regime: RegimeState | None = None
    position: Position | None = None
    bars_since_entry: int = 0
    reached_one_r: bool = False
    daily_close: Decimal | None = None
    daily_ma200: Decimal | None = None
    now_ts_ms: int | None = None

    def __post_init__(self) -> None:
        """校验策略只接收可回测的已确认数据。"""

        trigger_candles = tuple(self.trigger_candles)
        object.__setattr__(self, "trigger_candles", trigger_candles)
        if not trigger_candles:
            raise StrategyInputError("trigger_candles must not be empty")
        _validate_candles(trigger_candles, "trigger_candles")

        if self.range_candles is not None:
            range_candles = tuple(self.range_candles)
            object.__setattr__(self, "range_candles", range_candles)
            _validate_candles(range_candles, "range_candles")

        timeframe_candles: dict[CandleBar, tuple[Candle, ...]] = {}
        for bar, candles in dict(self.timeframe_candles or {}).items():
            bar_value = bar if isinstance(bar, CandleBar) else CandleBar(str(bar))
            series = tuple(candles)
            _validate_candles(series, f"timeframe_candles[{bar_value.value}]")
            timeframe_candles[bar_value] = series
        object.__setattr__(self, "timeframe_candles", timeframe_candles)

        if self.atr <= 0:
            raise StrategyInputError("atr must be greater than zero")
        if self.ma200 is not None and self.ma200 <= 0:
            raise StrategyInputError("ma200 must be greater than zero")
        if self.daily_ma200 is not None and self.daily_ma200 <= 0:
            raise StrategyInputError("daily_ma200 must be greater than zero")
        if self.daily_close is not None and self.daily_close <= 0:
            raise StrategyInputError("daily_close must be greater than zero")
        if self.bars_since_entry < 0:
            raise StrategyInputError("bars_since_entry must not be negative")

        if self.divergence is not None and self.divergence.inst_id != self.candidate.inst_id:
            raise StrategyInputError("divergence instrument must match candidate instrument")
        if self.position is not None and self.position.inst_id != self.candidate.inst_id:
            raise StrategyInputError("position instrument must match candidate instrument")

        latest_ts = trigger_candles[-1].ts_ms
        now_ts_ms = latest_ts if self.now_ts_ms is None else self.now_ts_ms
        if now_ts_ms < latest_ts:
            raise StrategyInputError("now_ts_ms must not be earlier than the latest trigger candle")
        object.__setattr__(self, "now_ts_ms", now_ts_ms)

    def with_range_candles(self, candles: Sequence[Candle]) -> StrategyContext:
        """返回带有独立区间数据的新上下文。"""

        return replace(self, range_candles=tuple(candles))

    def with_position(
        self,
        position: Position,
        *,
        bars_since_entry: int | None = None,
        reached_one_r: bool | None = None,
    ) -> StrategyContext:
        """返回带有持仓管理状态的新上下文。"""

        return replace(
            self,
            position=position,
            bars_since_entry=self.bars_since_entry
            if bars_since_entry is None
            else bars_since_entry,
            reached_one_r=self.reached_one_r if reached_one_r is None else reached_one_r,
        )


def _validate_candles(candles: tuple[Candle, ...], field_name: str) -> None:
    """校验 K 线时间序列和收盘确认状态。"""

    previous_ts: int | None = None
    for candle in candles:
        if not candle.confirmed:
            raise StrategyInputError(f"{field_name} must contain confirmed candles only")
        if previous_ts is not None and candle.ts_ms <= previous_ts:
            raise StrategyInputError(f"{field_name} must be strictly ordered by ts_ms")
        previous_ts = candle.ts_ms


class BaseStrategy(ABC):
    """所有策略的统一接口。"""

    name: str = "BASE"
    version: str = "1.0.0"
    formula: str | None = None
    default_params: Mapping[str, Any] = {"enabled": True}

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        merged = dict(self.default_params)
        if params:
            unknown = set(params) - set(merged)
            if unknown:
                names = ", ".join(sorted(unknown))
                raise StrategyInputError(f"unknown parameters for {self.name}: {names}")
            merged.update(params)
        self.params: dict[str, Any] = merged

    @abstractmethod
    def accepts(self, candidate: ScreenerCandidate) -> bool:
        """判断该策略是否处理选币候选。"""

    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        """在一根已收盘 K 线上生成开仓、平仓或调整止损信号。"""

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        """成交回报钩子；无状态策略默认不产生额外信号。"""

        del ctx, fill
        return None
