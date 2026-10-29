"""S1 顺势回调背离策略。"""

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
    build_close_signal,
    build_move_stop_signal,
    build_open_signal,
    divergence_is_current,
    divergence_structure_invalidated,
    position_signal_side,
    structure_break,
)


@register_strategy
class S1TrendPullbackStrategy(BaseStrategy):
    """上涨/下跌趋势中的回调背离策略。"""

    name = "S1"
    version = "1.0.0"
    default_params = {
        "enabled": True,
        "structure_lookback": 3,
        "stop_atr": Decimal("0.5"),
        "first_take_profit_r": Decimal("1.5"),
        "single_take_profit_r": Decimal("2.0"),
        "split_exits": True,
        "time_stop_bars": 30,
        "breakeven_buffer": Decimal("0.0012"),
    }

    def accepts(self, candidate: ScreenerCandidate) -> bool:
        """只处理 A/B 顺势候选池。"""

        return (candidate.pool == "A" and candidate.regime == RegimeState.UPTREND) or (
            candidate.pool == "B" and candidate.regime == RegimeState.DOWNTREND
        )

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        """生成顺势开仓或在途持仓保护信号。"""

        if ctx.position is not None:
            managed = self._manage_position(ctx)
            return [] if managed is None else [managed]
        if not self.params["enabled"] or not self.accepts(ctx.candidate):
            return []

        side, divergence_kinds = self._direction(ctx.candidate)
        if (
            side is None
            or ctx.divergence is None
            or not divergence_is_current(ctx, divergence_kinds)
        ):
            return []
        if divergence_structure_invalidated(ctx, side):
            return []
        if not structure_break(ctx, side, int(self.params["structure_lookback"])):
            return []

        atr = ctx.atr
        stop_distance = atr * as_decimal(self.params["stop_atr"], "stop_atr")
        extreme = ctx.divergence.seg2_extreme_price
        stop_loss = extreme - stop_distance if side == SignalSide.LONG else extreme + stop_distance
        entry = ctx.trigger_candles[-1].close
        risk = abs(entry - stop_loss)
        if risk <= 0:
            return []

        split_exits = bool(self.params["split_exits"])
        if split_exits:
            first_r = as_decimal(self.params["first_take_profit_r"], "first_take_profit_r")
            target_price = (
                entry + risk * first_r if side == SignalSide.LONG else entry - risk * first_r
            )
            if target_price <= 0:
                return []
            take_profits = [TakeProfit(price=target_price, fraction=Decimal("0.5"))]
            exit_note = "1.5R 平半仓，剩余仓位使用移动止损"
        else:
            target_r = as_decimal(self.params["single_take_profit_r"], "single_take_profit_r")
            target_price = (
                entry + risk * target_r if side == SignalSide.LONG else entry - risk * target_r
            )
            if target_price <= 0:
                return []
            take_profits = [TakeProfit(price=target_price, fraction=Decimal("1"))]
            exit_note = "小资金不拆单，1R 保本后整体 2R 止盈"

        reason = (
            f"S1 v{self.version}：{ctx.candidate.pool} 池顺势{side.value}，"
            f"确认{ctx.divergence.kind.value}并突破结构；{exit_note}"
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
        if candidate.pool == "A":
            return SignalSide.LONG, {DivergenceKind.BULL_REGULAR, DivergenceKind.BULL_HIDDEN}
        if candidate.pool == "B":
            return SignalSide.SHORT, {DivergenceKind.BEAR_REGULAR, DivergenceKind.BEAR_HIDDEN}
        return None, {DivergenceKind.BULL_REGULAR}

    def _manage_position(self, ctx: StrategyContext) -> Signal | None:
        """执行 S1 的反向背离、时间止损和日线破 MA200 保护。"""

        position = ctx.position
        if position is None:
            return None
        side = position_signal_side(position.pos_side)
        opposite_kind = (
            {DivergenceKind.BEAR_REGULAR, DivergenceKind.BEAR_HIDDEN}
            if side == SignalSide.LONG
            else {DivergenceKind.BULL_REGULAR, DivergenceKind.BULL_HIDDEN}
        )
        if ctx.divergence is not None and ctx.divergence.kind in opposite_kind:
            if ctx.divergence.confirmed_ts <= ctx.trigger_candles[-1].ts_ms:
                return build_close_signal(ctx, side, "S1：检测到 4H 反向背离，立即平掉剩余仓位")

        time_stop_bars = int(self.params["time_stop_bars"])
        if ctx.bars_since_entry >= time_stop_bars and not ctx.reached_one_r:
            return build_close_signal(
                ctx, side, f"S1：入场 {time_stop_bars} 根 K 线仍未达到 1R，执行时间止损"
            )

        if ctx.daily_close is None or ctx.daily_ma200 is None or position.upl <= 0:
            return None
        broken = (side == SignalSide.LONG and ctx.daily_close < ctx.daily_ma200) or (
            side == SignalSide.SHORT and ctx.daily_close > ctx.daily_ma200
        )
        if not broken:
            return None

        buffer_ratio = as_decimal(self.params["breakeven_buffer"], "breakeven_buffer")
        stop_loss = (
            position.avg_price * (Decimal("1") + buffer_ratio)
            if side == SignalSide.LONG
            else position.avg_price * (Decimal("1") - buffer_ratio)
        )
        return build_move_stop_signal(
            ctx, side, stop_loss, "S1：日线突破 MA200 且持仓浮盈，止损移至保本缓冲"
        )
