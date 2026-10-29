"""S3 逆势背离策略。"""

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
class S3CounterTrendStrategy(BaseStrategy):
    """满足强背离和大盘过滤条件后才允许启用的逆势策略。"""

    name = "S3"
    version = "1.0.0"
    default_params = {
        "enabled": False,
        "structure_lookback": 3,
        "score_threshold": 70,
        "stop_atr": Decimal("0.5"),
        "allocation_multiplier": Decimal("0.3"),
    }

    def accepts(self, candidate: ScreenerCandidate) -> bool:
        """只处理 E 逆势池，且方向必须与趋势相反。"""

        if not self.params["enabled"] or candidate.pool != "E":
            return False
        if candidate.score < float(self.params["score_threshold"]):
            return False
        return (
            candidate.regime == RegimeState.UPTREND
            and candidate.divergence_kind
            in {DivergenceKind.BEAR_REGULAR, DivergenceKind.BEAR_HIDDEN}
        ) or (
            candidate.regime == RegimeState.DOWNTREND
            and candidate.divergence_kind
            in {DivergenceKind.BULL_REGULAR, DivergenceKind.BULL_HIDDEN}
        )

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        """生成经过强度、BTC 趋势和 MA200 目标校验的逆势信号。"""

        if not self.accepts(ctx.candidate) or ctx.divergence is None:
            return []
        side, divergence_kinds = self._direction(ctx.candidate)
        if side is None or not divergence_is_current(ctx, divergence_kinds):
            return []
        if divergence_structure_invalidated(ctx, side):
            return []
        if not ctx.divergence.height_div or not ctx.divergence.area_div:
            return []
        if ctx.btc_regime is None or self._btc_blocks(side, ctx.btc_regime):
            return []
        if ctx.ma200 is None or not structure_break(
            ctx, side, int(self.params["structure_lookback"])
        ):
            return []

        entry = ctx.trigger_candles[-1].close
        stop_distance = ctx.atr * as_decimal(self.params["stop_atr"], "stop_atr")
        extreme = ctx.divergence.seg2_extreme_price
        stop_loss = extreme - stop_distance if side == SignalSide.LONG else extreme + stop_distance
        risk = abs(entry - stop_loss)
        if risk <= 0:
            return []
        if (side == SignalSide.LONG and ctx.ma200 <= entry) or (
            side == SignalSide.SHORT and ctx.ma200 >= entry
        ):
            return []

        second_target = (
            entry + risk * Decimal("2") if side == SignalSide.LONG else entry - risk * Decimal("2")
        )
        if second_target <= 0 or ctx.ma200 <= 0:
            return []
        take_profits = [
            TakeProfit(price=ctx.ma200, fraction=Decimal("0.5")),
            TakeProfit(price=second_target, fraction=Decimal("0.5")),
        ]
        reason = (
            f"S3 v{self.version}：E 池逆势{side.value}，评分 {ctx.candidate.score:.1f}，"
            f"柱高度与面积均背离；仓位系数 {self.params['allocation_multiplier']}，BTC 过滤通过"
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
        if candidate.regime == RegimeState.UPTREND:
            return SignalSide.SHORT, {DivergenceKind.BEAR_REGULAR, DivergenceKind.BEAR_HIDDEN}
        if candidate.regime == RegimeState.DOWNTREND:
            return SignalSide.LONG, {DivergenceKind.BULL_REGULAR, DivergenceKind.BULL_HIDDEN}
        return None, {DivergenceKind.BULL_REGULAR}

    @staticmethod
    def _btc_blocks(side: SignalSide, btc_regime: RegimeState) -> bool:
        """BTC 强趋势方向与逆势交易方向相反时禁开仓。"""

        return (side == SignalSide.LONG and btc_regime == RegimeState.DOWNTREND) or (
            side == SignalSide.SHORT and btc_regime == RegimeState.UPTREND
        )
