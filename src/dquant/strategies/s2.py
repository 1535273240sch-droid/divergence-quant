"""S2 震荡区间背离反转策略。"""

from __future__ import annotations

from decimal import Decimal

from dquant.contracts import (
    DivergenceKind,
    RegimeState,
    ScreenerCandidate,
    Signal,
    SignalSide,
    TakeProfit,
)

from .base import BaseStrategy, StrategyContext
from .registry import register_strategy
from .signals import (
    as_decimal,
    build_open_signal,
    divergence_is_current,
    divergence_structure_invalidated,
    structure_break,
)


@register_strategy
class S2RangeReversalStrategy(BaseStrategy):
    """震荡区间上下沿的背离反转策略。"""

    name = "S2"
    version = "1.0.0"
    default_params = {
        "enabled": True,
        "structure_lookback": 3,
        "range_lookback": 50,
        "range_buffer_atr": Decimal("0.5"),
        "stop_atr": Decimal("1"),
        "allocation_multiplier": Decimal("0.5"),
    }

    def accepts(self, candidate: ScreenerCandidate) -> bool:
        """只处理 C/D 震荡池，趋势状态必须为 RANGE。"""

        return candidate.pool in {"C", "D"} and candidate.regime == RegimeState.RANGE

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        """生成区间反转开仓信号。"""

        if not self.params["enabled"] or not self.accepts(ctx.candidate) or ctx.divergence is None:
            return []

        side, divergence_kinds = self._direction(ctx.candidate)
        if side is None or not divergence_is_current(ctx, divergence_kinds):
            return []
        if divergence_structure_invalidated(ctx, side):
            return []

        range_source = ctx.range_candles
        range_lookback = int(self.params["range_lookback"])
        if range_source is None or len(range_source) < range_lookback:
            return []
        range_window = range_source[-range_lookback:]
        range_low = min(candle.low for candle in range_window)
        range_high = max(candle.high for candle in range_window)
        range_buffer = ctx.atr * as_decimal(self.params["range_buffer_atr"], "range_buffer_atr")
        extreme = ctx.divergence.seg2_extreme_price
        at_boundary = (
            extreme <= range_low + range_buffer
            if side == SignalSide.LONG
            else extreme >= range_high - range_buffer
        )
        if not at_boundary:
            return []
        if not structure_break(ctx, side, int(self.params["structure_lookback"])):
            return []

        stop_distance = ctx.atr * as_decimal(self.params["stop_atr"], "stop_atr")
        stop_loss = (
            range_low - stop_distance if side == SignalSide.LONG else range_high + stop_distance
        )
        midpoint = (range_low + range_high) / Decimal("2")
        take_profits = [
            TakeProfit(
                price=midpoint,
                fraction=Decimal("0.5"),
            ),
            TakeProfit(
                price=range_high if side == SignalSide.LONG else range_low,
                fraction=Decimal("0.5"),
            ),
        ]
        reason = (
            f"S2 v{self.version}：{ctx.candidate.pool} 池{side.value}，"
            f"区间边界确认{ctx.divergence.kind.value}并突破结构；仓位系数 "
            f"{self.params['allocation_multiplier']}"
        )
        signal = build_open_signal(
            ctx,
            strategy_name=self.name,
            version=self.version,
            side=side,
            stop_loss=stop_loss,
            take_profits=take_profits,
            reason=reason,
        )
        return [] if signal is None else [signal]

    def _direction(
        self,
        candidate: ScreenerCandidate,
    ) -> tuple[SignalSide | None, set[DivergenceKind]]:
        if candidate.pool == "C":
            return SignalSide.LONG, {DivergenceKind.BULL_REGULAR, DivergenceKind.BULL_HIDDEN}
        if candidate.pool == "D":
            return SignalSide.SHORT, {DivergenceKind.BEAR_REGULAR, DivergenceKind.BEAR_HIDDEN}
        return None, {DivergenceKind.BULL_REGULAR}
