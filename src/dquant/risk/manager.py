"""M08 风控审核器。

风控只审核策略信号并输出公共 ``RiskDecision``，不创建订单、不访问交易所。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

from dquant.contracts import (
    AIAction,
    AIReview,
    Instrument,
    Position,
    PositionSide,
    RegimeState,
    RiskDecision,
    Signal,
    SignalAction,
    SignalSide,
)

from .calculator import (
    RiskInputError,
    calculate_fixed_fraction_size,
    calculate_position_size,
)
from .killswitch import KillSwitch


@dataclass(frozen=True, slots=True)
class MarketRiskContext:
    """风控需要的 BTC、资金费率和 AI 审核快照。"""

    btc_regime: RegimeState | None = None
    btc_bear_divergence_recent: bool = False
    btc_bull_divergence_recent: bool = False
    btc_one_hour_return: Decimal | None = None
    btc_move_ts_ms: int | None = None
    funding_rate: Decimal = Decimal("0")
    ai_review: AIReview | None = None


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """M08 风控参数，默认值对应计划书的小资金档。"""

    profile: Literal["small_account", "standard"] = "small_account"
    risk_per_trade: Decimal | None = None
    max_positions: int = 3
    max_same_direction: int = 2
    max_gross_leverage: Decimal = Decimal("3")
    daily_loss_limit: Decimal = Decimal("0.03")
    weekly_loss_limit: Decimal = Decimal("0.06")
    max_drawdown_kill: Decimal = Decimal("0.15")
    weekend_factor: Decimal = Decimal("0.7")
    btc_filter_factor: Decimal = Decimal("0.5")
    inverse_pool_factor: Decimal = Decimal("0.3")
    funding_rate_limit: Decimal = Decimal("0.001")
    btc_spike_threshold: Decimal = Decimal("0.04")
    btc_spike_lock_ms: int = 2 * 60 * 60 * 1000
    weekend_timezone: str = "Asia/Shanghai"

    def __post_init__(self) -> None:
        """校验并规范化配置数值。"""

        if self.profile not in ("small_account", "standard"):
            raise RiskInputError("profile must be small_account or standard")
        default_risk = Decimal("0.015") if self.profile == "small_account" else Decimal("0.01")
        values: dict[str, Decimal] = {
            "risk_per_trade": _decimal(
                default_risk if self.risk_per_trade is None else self.risk_per_trade,
                "risk_per_trade",
            ),
            "max_gross_leverage": _decimal(self.max_gross_leverage, "max_gross_leverage"),
            "daily_loss_limit": _decimal(self.daily_loss_limit, "daily_loss_limit"),
            "weekly_loss_limit": _decimal(self.weekly_loss_limit, "weekly_loss_limit"),
            "max_drawdown_kill": _decimal(self.max_drawdown_kill, "max_drawdown_kill"),
            "weekend_factor": _decimal(self.weekend_factor, "weekend_factor"),
            "btc_filter_factor": _decimal(self.btc_filter_factor, "btc_filter_factor"),
            "inverse_pool_factor": _decimal(self.inverse_pool_factor, "inverse_pool_factor"),
            "funding_rate_limit": _decimal(self.funding_rate_limit, "funding_rate_limit"),
            "btc_spike_threshold": _decimal(self.btc_spike_threshold, "btc_spike_threshold"),
        }
        if self.max_positions < 1 or self.max_same_direction < 1:
            raise RiskInputError("position limits must be positive")
        if self.btc_spike_lock_ms <= 0:
            raise RiskInputError("btc_spike_lock_ms must be positive")
        if not 0 < values["risk_per_trade"] <= 1:
            raise RiskInputError("risk_per_trade must be between 0 and 1")
        for name in ("daily_loss_limit", "weekly_loss_limit", "max_drawdown_kill"):
            if not 0 < values[name] <= 1:
                raise RiskInputError(f"{name} must be between 0 and 1")
        for name in ("max_gross_leverage", "funding_rate_limit", "btc_spike_threshold"):
            if values[name] <= 0:
                raise RiskInputError(f"{name} must be positive")
        for name in ("weekend_factor", "btc_filter_factor", "inverse_pool_factor"):
            if not 0 <= values[name] <= 1:
                raise RiskInputError(f"{name} must be between 0 and 1")
        try:
            ZoneInfo(self.weekend_timezone)
        except Exception as exc:
            raise RiskInputError("weekend_timezone must be a valid IANA timezone") from exc
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @classmethod
    def from_settings(cls, settings: object) -> RiskConfig:
        """从 M01 的 ``RiskSettings`` 读取已存在字段，其余使用本模块默认值。"""

        return cls(
            profile=getattr(settings, "profile", "small_account"),
            risk_per_trade=getattr(settings, "risk_per_trade", None),
            max_positions=getattr(settings, "max_positions", 3),
            max_same_direction=getattr(settings, "max_same_direction", 2),
            max_gross_leverage=getattr(settings, "max_gross_leverage", Decimal("3")),
            daily_loss_limit=getattr(settings, "daily_loss_limit", Decimal("0.03")),
            weekly_loss_limit=getattr(settings, "weekly_loss_limit", Decimal("0.06")),
            max_drawdown_kill=getattr(settings, "max_drawdown_kill", Decimal("0.15")),
        )


@dataclass(frozen=True, slots=True)
class RiskRequest:
    """风控审核输入；字段使用公共 ``Signal``、``Instrument``、``Position`` 模型。"""

    signal: Signal
    equity: Decimal
    instrument: Instrument | None = None
    positions: tuple[Position, ...] = ()
    pending_orders: tuple[object, ...] = ()
    wallet_balance: Decimal | None = None
    used_margin: Decimal = Decimal("0")
    available_equity: Decimal | None = None
    ts_ms: int | None = None
    daily_pnl: Decimal = Decimal("0")
    weekly_pnl: Decimal = Decimal("0")
    peak_equity: Decimal | None = None
    market: MarketRiskContext = field(default_factory=MarketRiskContext)
    pool_factor: Decimal = Decimal("1")
    # MHD / 固定比例仓位：position_pct 为名义仓位占权益比例，multiplier 为前端可调倍数。
    position_pct: Decimal | None = None
    position_multiplier: Decimal = Decimal("1")
    leverage: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        """校验风控输入的金额、时间和品种一致性。"""

        if self.equity <= 0:
            raise RiskInputError("equity must be positive")
        if self.instrument is not None and self.instrument.inst_id != self.signal.inst_id:
            raise RiskInputError("instrument must match signal.inst_id")
        ts_ms = self.signal.created_ts_ms if self.ts_ms is None else self.ts_ms
        object.__setattr__(self, "ts_ms", ts_ms)
        if ts_ms < 0:
            raise RiskInputError("ts_ms must not be negative")
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "pending_orders", tuple(self.pending_orders))
        object.__setattr__(self, "pool_factor", _decimal(self.pool_factor, "pool_factor"))
        if self.pool_factor < 0:
            raise RiskInputError("pool_factor must not be negative")
        if self.position_pct is not None:
            object.__setattr__(
                self, "position_pct", _decimal(self.position_pct, "position_pct")
            )
            if not 0 < self.position_pct <= 1:
                raise RiskInputError("position_pct must be within (0, 1]")
        object.__setattr__(
            self,
            "position_multiplier",
            _decimal(self.position_multiplier, "position_multiplier"),
        )
        if not 0 < self.position_multiplier <= 3:
            raise RiskInputError("position_multiplier must be within (0, 3]")
        object.__setattr__(self, "leverage", _decimal(self.leverage, "leverage"))
        if not 1 <= self.leverage <= 20:
            raise RiskInputError("leverage must be within [1, 20]")


class RiskManager:
    """组合风控审核器。

    ``evaluate`` 只对开仓信号应用开仓限制；平仓、减仓和移动止损信号始终允许继续管理已有仓位。
    """

    def __init__(
        self,
        config: RiskConfig | None = None,
        *,
        instruments: Mapping[str, Instrument] | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self.config = config or RiskConfig()
        self.instruments = dict(instruments or {})
        self.kill_switch = kill_switch or KillSwitch()
        self._peak_equity: Decimal | None = None
        self._long_blocked_until_ms: int | None = None
        self._short_blocked_until_ms: int | None = None

    def evaluate(self, request: RiskRequest | object) -> RiskDecision:
        """审核一次信号并返回公共 ``RiskDecision``。"""

        normalized = self._normalize_request(request)
        signal = normalized.signal
        if signal.action is not SignalAction.OPEN:
            return self._management_decision(signal)
        instrument = normalized.instrument
        if instrument is None:
            return self._reject(signal, "INSTRUMENT_UNAVAILABLE", "缺少交易对精度信息")

        risk_fraction = self.config.risk_per_trade
        if risk_fraction is None:
            raise RiskInputError("risk_per_trade must be configured")
        peak_equity = self._update_peak(normalized.equity, normalized.peak_equity)
        drawdown = (peak_equity - normalized.equity) / peak_equity
        if drawdown >= self.config.max_drawdown_kill:
            self.kill_switch.trigger_circuit_breaker("达到最大回撤阈值", close_all=True)
            return self._reject(signal, "MAX_DRAWDOWN", f"当前回撤 {drawdown}")
        if -normalized.daily_pnl >= normalized.equity * self.config.daily_loss_limit:
            self.kill_switch.stop_opening("达到单日亏损阈值")
            return self._reject(signal, "DAILY_LOSS_LIMIT", "当日停止开新仓")
        if -normalized.weekly_pnl >= normalized.equity * self.config.weekly_loss_limit:
            self.kill_switch.stop_opening("达到单周亏损阈值")
            return self._reject(signal, "WEEKLY_LOSS_LIMIT", "本周停止开新仓")
        if not self.kill_switch.can_open:
            return self._reject(signal, "KILL_SWITCH", self.kill_switch.state.reason or "禁止开仓")

        active_positions = tuple(position for position in normalized.positions if position.size > 0)
        pending_opens = tuple(order for order in normalized.pending_orders if _order_is_open(order))
        if any(position.inst_id == signal.inst_id for position in active_positions):
            return self._reject(signal, "SAME_SYMBOL_POSITION", "同一币种已有持仓")
        if len(active_positions) + len(pending_opens) >= self.config.max_positions:
            return self._reject(signal, "MAX_POSITIONS", "达到最大同时持仓数")

        same_direction = sum(
            1 for position in active_positions if _position_matches_side(position, signal.side)
        ) + sum(1 for order in pending_opens if _order_matches_side(order, signal.side))
        if same_direction >= self.config.max_same_direction:
            return self._reject(signal, "MAX_SAME_DIRECTION", "达到同方向最大持仓数")

        context = normalized.market
        if self._blocked_by_btc_spike(signal.side, normalized.ts_ms or 0, context):
            rule = (
                "BTC_SPIKE_LONG_BLOCK"
                if signal.side is SignalSide.LONG
                else "BTC_SPIKE_SHORT_BLOCK"
            )
            return self._reject(signal, rule, "BTC 急跌/急涨熔断窗口内禁止该方向开仓")
        if self._blocked_by_funding(signal.side, context.funding_rate):
            return self._reject(signal, "FUNDING_FILTER", "资金费率超过不利方向阈值")

        market_factor = Decimal("1")
        factor_notes: list[str] = []
        if self._btc_filter_applies(signal, context):
            market_factor *= self.config.btc_filter_factor
            factor_notes.append(f"btc_filter={self.config.btc_filter_factor}")

        ai_review = context.ai_review
        if ai_review is not None and ai_review.inst_id == signal.inst_id:
            if ai_review.action is AIAction.VETO:
                return self._reject(signal, "AI_VETO", "AI 审核否决该开仓信号")
            if ai_review.action is AIAction.HALF_SIZE:
                market_factor *= Decimal("0.5")
                factor_notes.append("ai_factor=0.5")

        weekend_factor = Decimal("1")
        if self._is_weekend(normalized.ts_ms or 0):
            weekend_factor = self.config.weekend_factor
            factor_notes.append(f"weekend_factor={weekend_factor}")

        if normalized.position_pct is not None:
            sizing = calculate_fixed_fraction_size(
                equity=normalized.equity,
                position_pct=normalized.position_pct,
                position_multiplier=normalized.position_multiplier,
                entry_price=signal.price,
                instrument=instrument,
            )
            sizing_mode = "fixed_fraction"
            decision_risk_fraction = normalized.position_pct
        else:
            sizing = calculate_position_size(
                equity=normalized.equity,
                risk_fraction=risk_fraction,
                entry_price=signal.price,
                stop_loss_price=signal.stop_loss or signal.price,
                instrument=instrument,
                pool_factor=normalized.pool_factor,
                market_factor=market_factor,
                weekend_factor=weekend_factor,
            )
            sizing_mode = "risk_fraction"
            decision_risk_fraction = risk_fraction
        if sizing.rejected:
            return self._reject(signal, "MIN_ORDER_SIZE", "计算出的数量低于交易所最小下单量")

        gross_notional = self._gross_notional(normalized, instrument, sizing.qty, signal.price)
        if gross_notional > normalized.equity * self.config.max_gross_leverage:
            return self._reject(signal, "MAX_GROSS_LEVERAGE", "达到组合总名义杠杆上限")

        rule = "APPROVED"
        if any(note.startswith("btc_filter") for note in factor_notes):
            rule = "BTC_FILTER"
        elif any(note.startswith("ai_factor") for note in factor_notes):
            rule = "AI_HALF_SIZE"
        elif any(note.startswith("weekend_factor") for note in factor_notes):
            rule = "WEEKEND_SIZE"
        detail = (
            f"风控通过 qty={sizing.qty}; sizing_mode={sizing_mode}; "
            f"single_tp_mode={sizing.single_tp_mode}"
        )
        if sizing_mode == "fixed_fraction":
            detail += (
                f"; position_pct={normalized.position_pct}; "
                f"position_multiplier={normalized.position_multiplier}; "
                f"leverage={normalized.leverage}"
            )
        if factor_notes:
            detail += "; " + ", ".join(factor_notes)
        return RiskDecision(
            signal_id=signal.signal_id,
            approved=True,
            rule=rule,
            detail=detail,
            qty=sizing.qty,
            risk_fraction=decision_risk_fraction,
        )

    def stop_opening(self, reason: str) -> None:
        """手动停止开仓。"""

        self.kill_switch.stop_opening(reason)

    def resume_opening(self) -> None:
        """手动恢复开仓；熔断需先单独解除。"""

        self.kill_switch.resume_opening()

    def release_circuit_breaker(self) -> None:
        """人工解除最大回撤熔断。"""

        self.kill_switch.release_circuit_breaker()

    def _normalize_request(self, request: RiskRequest | object) -> RiskRequest:
        if isinstance(request, RiskRequest):
            return request
        signal = getattr(request, "signal", None)
        if not isinstance(signal, Signal):
            raise RiskInputError("request.signal must be a public Signal model")
        instrument = getattr(request, "instrument", None) or self.instruments.get(signal.inst_id)
        peak_equity = getattr(request, "peak_equity", None)
        return RiskRequest(
            signal=signal,
            equity=_decimal(getattr(request, "equity", 0), "equity"),
            instrument=instrument,
            positions=tuple(getattr(request, "positions", ())),
            pending_orders=tuple(getattr(request, "pending_orders", ())),
            wallet_balance=getattr(request, "wallet_balance", None),
            used_margin=_decimal(getattr(request, "used_margin", 0), "used_margin"),
            available_equity=getattr(request, "available_equity", None),
            ts_ms=getattr(request, "ts_ms", signal.created_ts_ms),
            daily_pnl=_decimal(getattr(request, "daily_pnl", 0), "daily_pnl"),
            weekly_pnl=_decimal(getattr(request, "weekly_pnl", 0), "weekly_pnl"),
            peak_equity=None if peak_equity is None else _decimal(peak_equity, "peak_equity"),
            market=getattr(request, "market", MarketRiskContext()),
            pool_factor=_decimal(getattr(request, "pool_factor", 1), "pool_factor"),
            position_pct=getattr(request, "position_pct", None),
            position_multiplier=getattr(request, "position_multiplier", 1),
            leverage=getattr(request, "leverage", 1),
        )

    def _update_peak(self, equity: Decimal, explicit_peak: Decimal | None) -> Decimal:
        candidate = equity if explicit_peak is None else max(equity, explicit_peak)
        self._peak_equity = (
            candidate if self._peak_equity is None else max(self._peak_equity, candidate)
        )
        return self._peak_equity

    def _blocked_by_btc_spike(
        self,
        side: SignalSide,
        ts_ms: int,
        context: MarketRiskContext,
    ) -> bool:
        movement = context.btc_one_hour_return
        if movement is not None and abs(movement) >= self.config.btc_spike_threshold:
            movement_ts = context.btc_move_ts_ms or ts_ms
            until = movement_ts + self.config.btc_spike_lock_ms
            if movement < 0:
                self._long_blocked_until_ms = max(self._long_blocked_until_ms or 0, until)
            else:
                self._short_blocked_until_ms = max(self._short_blocked_until_ms or 0, until)
        if side is SignalSide.LONG:
            return self._long_blocked_until_ms is not None and ts_ms < self._long_blocked_until_ms
        return self._short_blocked_until_ms is not None and ts_ms < self._short_blocked_until_ms

    def _blocked_by_funding(self, side: SignalSide, funding_rate: Decimal) -> bool:
        if side is SignalSide.LONG:
            return funding_rate > self.config.funding_rate_limit
        return funding_rate < -self.config.funding_rate_limit

    @staticmethod
    def _btc_filter_applies(signal: Signal, context: MarketRiskContext) -> bool:
        if signal.inst_id.startswith("BTC-"):
            return False
        if signal.side is SignalSide.LONG:
            return context.btc_regime is RegimeState.DOWNTREND or context.btc_bear_divergence_recent
        return context.btc_regime is RegimeState.UPTREND or context.btc_bull_divergence_recent

    def _is_weekend(self, ts_ms: int) -> bool:
        local = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).astimezone(
            ZoneInfo(self.config.weekend_timezone)
        )
        return local.isoweekday() >= 6

    def _gross_notional(
        self,
        request: RiskRequest,
        instrument: Instrument,
        new_qty: Decimal,
        new_price: Decimal,
    ) -> Decimal:
        total = new_qty * new_price * instrument.ct_val
        for position in request.positions:
            position_instrument = self.instruments.get(position.inst_id, instrument)
            total += position.size * position.mark_price * position_instrument.ct_val
        for order in request.pending_orders:
            if _order_is_open(order):
                order_instrument = self.instruments.get(
                    str(getattr(order, "inst_id", "")), instrument
                )
                qty = _decimal(getattr(order, "qty", 0), "pending_order.qty")
                price = _decimal(getattr(order, "price", new_price), "pending_order.price")
                total += qty * price * order_instrument.ct_val
        return total

    @staticmethod
    def _management_decision(signal: Signal) -> RiskDecision:
        return RiskDecision(
            signal_id=signal.signal_id,
            approved=True,
            rule="MANAGEMENT_ALLOWED",
            detail="平仓、减仓和移动止损不受禁止开仓状态阻塞",
            qty=Decimal("0"),
            risk_fraction=Decimal("0"),
        )

    @staticmethod
    def _reject(signal: Signal, rule: str, detail: str) -> RiskDecision:
        return RiskDecision(
            signal_id=signal.signal_id,
            approved=False,
            rule=rule,
            detail=detail,
            qty=Decimal("0"),
            risk_fraction=Decimal("0"),
        )


def _decimal(value: object, field_name: str) -> Decimal:
    """内部 Decimal 转换，拒绝 NaN、无穷和不可解析值。"""

    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RiskInputError(f"{field_name} must be a valid decimal") from exc
    if not result.is_finite():
        raise RiskInputError(f"{field_name} must be finite")
    return result


def _position_matches_side(position: Position, side: SignalSide) -> bool:
    return (side is SignalSide.LONG and position.pos_side is PositionSide.LONG) or (
        side is SignalSide.SHORT and position.pos_side is PositionSide.SHORT
    )


def _order_is_open(order: object) -> bool:
    action = getattr(order, "action", None)
    return action is SignalAction.OPEN or action == SignalAction.OPEN or action == "open"


def _order_matches_side(order: object, side: SignalSide) -> bool:
    order_side = getattr(order, "side", None)
    return order_side is side or order_side == side or order_side == side.value
