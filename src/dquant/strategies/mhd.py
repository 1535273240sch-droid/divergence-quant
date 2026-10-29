"""MHD 摆动点 MACD 柱背离策略模块。

策略读取 4H 已确认 K 线，在摆动高点/低点确认的那根收盘产生信号：

* 顶部 MHD（看跌背离）→ 开空；
* 底部 MHD（看涨背离）→ 开多；
* 持仓期间使用 ATR 移动止损，不设固定止盈。

仓位由 ``position_pct``（默认 50%）、``position_multiplier``（默认 1.0）
和 ``leverage``（默认 3x，仅用于交易所保证金设置）控制。参数写入
``data/strategy_runtime.json`` 后由引擎热加载，无需重启。
"""

from __future__ import annotations

import math
from decimal import Decimal

from dquant.contracts import (
    CandleBar,
    PositionSide,
    ScreenerCandidate,
    Signal,
    SignalSide,
)
from dquant.core.mhd import MHDConfig, MHDSignal, detect_mhd

from .atr_stop import initial_stop, trailing_stop
from .base import BaseStrategy, StrategyContext, StrategyInputError
from .registry import register_strategy
from .signals import (
    build_close_signal,
    build_move_stop_signal,
    build_open_signal,
)


def _decimal_param(params: dict[str, object], name: str) -> Decimal:
    try:
        return Decimal(str(params[name]))
    except (KeyError, ArithmeticError, ValueError) as exc:
        raise StrategyInputError(f"{name} must be a valid decimal") from exc


def _int_param(params: dict[str, object], name: str) -> int:
    raw = params[name]
    if isinstance(raw, bool):
        raise StrategyInputError(f"{name} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise StrategyInputError(f"{name} must be an integer") from exc
    if value != raw and not isinstance(raw, str):
        raise StrategyInputError(f"{name} must be an integer")
    return value


@register_strategy
class MHDDivergenceStrategy(BaseStrategy):
    """MHD 摆动点 MACD 柱背离 + ATR 移动止损策略。"""

    name = "MHD"
    version = "1.0.0"
    formula = (
        "pm=(price_b-price_ref)/ATR14_ref; "
        "hm=(hist_b-hist_ref)/stdev(hist,100)_b; "
        "D=pm-hm; D=max(D_pair,D_peak)"
    )
    default_params = {
        "enabled": True,
        # 运行周期：1H / 4H / 1D / AUTO（AUTO = 三个周期同时计算并取最强信号）
        "timeframe": "1H",
        # 摆动点确认参数
        "swing_left": 3,
        "swing_right": 3,
        # MACD / ATR / 标准化窗口
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_period": 14,
        "stdev_window": 100,
        "peak_lookback": 120,
        "min_d": 0.5,
        "peak_price_tolerance_atr": 0.5,
        # ATR 移动止损
        "atr_mult": 2.5,
        # 仓位与杠杆
        "position_pct": 0.5,
        "position_multiplier": 1.0,
        "leverage": 3,
    }

    def __init__(self, params: dict[str, object] | None = None) -> None:
        super().__init__(params)
        self._validate_params()

    def _validate_params(self) -> None:
        timeframe = str(self.params.get("timeframe", "1H")).strip().upper()
        if timeframe not in {"1H", "4H", "1D", "AUTO"}:
            raise StrategyInputError("timeframe must be one of 1H/4H/1D/AUTO")
        self.params["timeframe"] = timeframe
        pct = _decimal_param(self.params, "position_pct")
        multiplier = _decimal_param(self.params, "position_multiplier")
        leverage = _decimal_param(self.params, "leverage")
        atr_mult = _decimal_param(self.params, "atr_mult")
        min_d = _decimal_param(self.params, "min_d")
        if not (Decimal("0") < pct <= Decimal("1")):
            raise StrategyInputError("position_pct must be within (0, 1]")
        if not (Decimal("0") < multiplier <= Decimal("3")):
            raise StrategyInputError("position_multiplier must be within (0, 3]")
        if not (Decimal("1") <= leverage <= Decimal("20")):
            raise StrategyInputError("leverage must be within [1, 20]")
        if not (Decimal("0.1") <= atr_mult <= Decimal("10")):
            raise StrategyInputError("atr_mult must be within [0.1, 10]")
        if min_d < 0:
            raise StrategyInputError("min_d must not be negative")
        # 构造一次配置对象，统一校验窗口参数。
        self._config()

    def _config(self) -> MHDConfig:
        return MHDConfig(
            swing_left=_int_param(self.params, "swing_left"),
            swing_right=_int_param(self.params, "swing_right"),
            macd_fast=_int_param(self.params, "macd_fast"),
            macd_slow=_int_param(self.params, "macd_slow"),
            macd_signal=_int_param(self.params, "macd_signal"),
            atr_period=_int_param(self.params, "atr_period"),
            stdev_window=_int_param(self.params, "stdev_window"),
            peak_lookback=_int_param(self.params, "peak_lookback"),
            min_d=float(_decimal_param(self.params, "min_d")),
            peak_price_tolerance_atr=float(
                _decimal_param(self.params, "peak_price_tolerance_atr")
            ),
        )

    def accepts(self, candidate: ScreenerCandidate) -> bool:
        """MHD 是独立因子，不依赖现有选币池分类，所有候选都参与计算。"""

        return candidate.inst_id.endswith("-USDT-SWAP")

    def _bars(self) -> list[CandleBar]:
        timeframe = str(self.params.get("timeframe", "1H")).upper()
        if timeframe == "AUTO":
            return [CandleBar.ONE_HOUR, CandleBar.FOUR_HOUR, CandleBar.ONE_DAY]
        return [CandleBar(timeframe)]

    @staticmethod
    def _candles_for(ctx: StrategyContext, bar: CandleBar) -> tuple:
        series = ctx.timeframe_candles.get(bar)
        if series:
            return tuple(series)
        if bar is CandleBar.FOUR_HOUR and ctx.range_candles:
            return tuple(ctx.range_candles)
        return ()

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        if ctx.position is not None:
            return self._manage_position(ctx)
        if not bool(self.params["enabled"]):
            return []
        config = self._config()
        candidates: list[tuple[MHDSignal, CandleBar, tuple]] = []
        for bar in self._bars():
            candles = self._candles_for(ctx, bar)
            if not candles or len(candles) < config.warmup_bars:
                continue
            if any(candle.bar != bar for candle in candles[-5:]):
                continue
            result = detect_mhd(
                [float(candle.high) for candle in candles],
                [float(candle.low) for candle in candles],
                [float(candle.close) for candle in candles],
                config,
            )
            last_index = len(candles) - 1
            for item in result.signals:
                if item.confirmed_index == last_index:
                    candidates.append((item, bar, candles))
        if not candidates:
            return []
        strongest, bar, candles = max(
            candidates,
            key=lambda entry: (entry[0].d, entry[1] == CandleBar.ONE_HOUR),
        )
        side = SignalSide.SHORT if strongest.kind == "top" else SignalSide.LONG
        entry = candles[-1].close
        atr_last = detect_mhd(
            [float(candle.high) for candle in candles],
            [float(candle.low) for candle in candles],
            [float(candle.close) for candle in candles],
            config,
        ).atr[-1]
        if not math.isfinite(float(atr_last)) or atr_last <= 0:
            atr_last = float(ctx.atr)
        try:
            stop_loss = initial_stop(
                side,
                entry,
                Decimal(str(atr_last)),
                _decimal_param(self.params, "atr_mult"),
            )
        except ValueError:
            return []
        reason = self._reason(strongest, side, bar)
        signal = build_open_signal(
            ctx,
            strategy_name=self.name,
            version=self.version,
            side=side,
            stop_loss=stop_loss,
            take_profits=[],
            reason=reason,
            entry_price=entry,
            signal_ts_ms=candles[-1].ts_ms,
        )
        return [] if signal is None else [signal]

    def _reason(self, item: MHDSignal, side: SignalSide, bar: CandleBar) -> str:
        pair_text = "—" if item.d_pair is None else f"{item.d_pair:.2f}"
        peak_text = "—" if item.d_peak is None else f"{item.d_peak:.2f}"
        return (
            f"MHD v{self.version} [{bar.value}]：{side.value} 摆动点确认，"
            f"{'顶背离' if item.kind == 'top' else '底背离'} "
            f"D={item.d:.2f}（D_pair={pair_text}, D_peak={peak_text}, "
            f"reference={item.reference}, pm={item.pm:.2f}, hm={item.hm:.2f}）"
        )

    def _manage_position(self, ctx: StrategyContext) -> list[Signal]:
        position = ctx.position
        if position is None:
            return []
        bar = self._bars()[0]
        candles = self._candles_for(ctx, bar)
        if not candles or len(candles) < self._config().warmup_bars:
            return []
        atr_values = detect_mhd(
            [float(candle.high) for candle in candles],
            [float(candle.low) for candle in candles],
            [float(candle.close) for candle in candles],
            self._config(),
        ).atr
        atr_last = atr_values[-1]
        if not math.isfinite(float(atr_last)) or atr_last <= 0:
            return []
        atr_decimal = Decimal(str(float(atr_last)))
        atr_mult = _decimal_param(self.params, "atr_mult")
        last_candle = candles[-1]
        mark = last_candle.close
        side = position.pos_side
        since_entry = [
            candle for candle in candles if candle.ts_ms >= position.opened_ts_ms
        ] or [last_candle]

        if side is PositionSide.LONG:
            reference = max(candle.high for candle in since_entry)
            if position.stop_loss is not None and mark <= position.stop_loss:
                return [
                    build_close_signal(
                        ctx,
                        SignalSide.LONG,
                        "MHD 持仓跌破 ATR 保护止损，保护性平仓",
                        price=mark,
                        ts_ms=last_candle.ts_ms,
                    )
                ]
            candidate = trailing_stop(
                side, reference, atr_decimal, atr_mult, position.stop_loss
            )
            current = position.stop_loss
            if candidate > 0 and (current is None or candidate > current):
                return [
                    build_move_stop_signal(
                        ctx,
                        SignalSide.LONG,
                        candidate,
                        f"MHD ATR 移动止损：参考高点 {reference} - {atr_mult}×ATR",
                        price=mark,
                        ts_ms=last_candle.ts_ms,
                    )
                ]
            return []

        reference = min(candle.low for candle in since_entry)
        if position.stop_loss is not None and mark >= position.stop_loss:
            return [
                build_close_signal(
                    ctx,
                    SignalSide.SHORT,
                    "MHD 持仓突破 ATR 保护止损，保护性平仓",
                    price=mark,
                    ts_ms=last_candle.ts_ms,
                )
            ]
        candidate = trailing_stop(side, reference, atr_decimal, atr_mult, position.stop_loss)
        current = position.stop_loss
        if candidate > 0 and (current is None or candidate < current):
            return [
                build_move_stop_signal(
                    ctx,
                    SignalSide.SHORT,
                    candidate,
                    f"MHD ATR 移动止损：参考低点 {reference} + {atr_mult}×ATR",
                    price=mark,
                    ts_ms=last_candle.ts_ms,
                )
            ]
        return []


__all__ = ["MHDDivergenceStrategy"]
