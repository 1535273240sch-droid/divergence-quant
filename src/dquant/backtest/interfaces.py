"""回测与外部模块的注入式接口。

M03/M04/M05 的特征、M07 的信号代码和 M08 的风控代码都不在回测模块内复制。
回测只依赖以下最小协议：
- 特征提供器在策略调用前提供 RegimeResult/DivergenceSignal/ScreenerCandidate；
- 策略在每根 K 线收盘时返回公共 `Signal`；
- 风控接收完整组合上下文并返回公共 `RiskDecision`。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from dquant.contracts import (
    Candle,
    CandleBar,
    DivergenceSignal,
    Fill,
    Instrument,
    Position,
    RegimeResult,
    RiskDecision,
    ScreenerCandidate,
    Signal,
)

from .history import MarketHistoryView
from .models import RiskRequest


@dataclass(frozen=True)
class StrategyFeatures:
    """M03/M04/M05 在某一根 K 线收盘时提供的策略特征。

    回测不解释这些字段，只负责按确认时点原样传给策略。`extras` 用于 BTC 大盘过滤、
    周末系数等尚未进入公共契约的上下文，键名由 M07/M08 约定。
    """

    regime: RegimeResult | None = None
    divergence: DivergenceSignal | None = None
    candidate: ScreenerCandidate | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureRequest:
    """特征提供器看到的已收盘行情视图。"""

    inst_id: str
    bar: CandleBar
    close_ts_ms: int
    history: MarketHistoryView


class FeatureProviderProtocol(Protocol):
    """特征提供器协议；实现方通常包装 M03/M04/M05 的预计算索引。"""

    def __call__(self, request: FeatureRequest) -> StrategyFeatures:
        """返回该收盘时刻的策略特征。"""


class StrategyProtocol(Protocol):
    """策略协议；具体实现由 M07 提供。"""

    name: str
    version: str

    def on_bar(self, context: BacktestBarContext) -> Sequence[Signal]:
        """每根 K 线收盘调用，返回信号列表。"""

        raise NotImplementedError

    def on_fill(self, context: BacktestBarContext, fill: Fill) -> None:
        """成交回报，默认实现为空。"""

        return None


class RiskGateProtocol(Protocol):
    """风控协议；具体实现由 M08 提供。"""

    def evaluate(self, request: RiskRequest) -> RiskDecision:
        """审核一个信号并返回带数量的风控决策。"""

        raise NotImplementedError


@dataclass(frozen=True)
class BacktestBarContext:
    """策略在某一根 K 线收盘时看到的只读上下文。"""

    inst_id: str
    bar: CandleBar
    candle: Candle
    close_ts_ms: int
    equity: Decimal
    wallet_balance: Decimal
    used_margin: Decimal
    positions: tuple[Position, ...]
    instrument: Instrument
    history: MarketHistoryView
    features: StrategyFeatures = field(default_factory=StrategyFeatures)

    @property
    def now_ts_ms(self) -> int:
        """当前收盘时间，便于 M07 实盘/回测上下文对齐。"""

        return self.close_ts_ms

    @property
    def regime(self) -> RegimeResult | None:
        return self.features.regime

    @property
    def divergence(self) -> DivergenceSignal | None:
        return self.features.divergence

    @property
    def candidate(self) -> ScreenerCandidate | None:
        return self.features.candidate

    def candles(
        self,
        limit: int | None = None,
        *,
        inst_id: str | None = None,
        bar: CandleBar | None = None,
    ) -> tuple[Candle, ...]:
        """读取当前或指定行情流的历史，绝不包含未来 K 线。"""

        return self.history.candles(
            inst_id if inst_id is not None else self.inst_id,
            bar if bar is not None else self.bar,
            limit,
        )

    def position(self, inst_id: str | None = None) -> Position | None:
        """返回指定交易对的持仓快照。"""

        target = inst_id if inst_id is not None else self.inst_id
        for position in self.positions:
            if position.inst_id == target:
                return position
        return None
