"""事件驱动、逐 K 线推进的回测引擎。

撮合口径（与计划书 §5.9 一致）：
- 信号在 K 线收盘产生，只能在下一根执行周期 K 线开盘成交；
- 同根 K 线同时触发止损与止盈时，按止损优先处理；
- 市价成交施加不利滑点，止损为市价单，止盈为限价单；
- 账户、持仓和盈亏全部使用 Decimal，费用、资金费率和滑点逐笔记录。

策略与风控通过协议注入，回测模块不复制 M03/M04/M07/M08 的业务逻辑。
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any, Protocol

from dquant.contracts import (
    Candle,
    CandleBar,
    Fill,
    Instrument,
    OrderType,
    Position,
    PositionSide,
    RiskDecision,
    Signal,
    SignalAction,
    SignalSide,
    TakeProfit,
)

from .config import (
    ENGINE_VERSION,
    FEE_MODEL_VERSION,
    FUNDING_MODEL_VERSION,
    SHANGHAI_TZ,
    SLIPPAGE_MODEL_VERSION,
    BacktestConfig,
    compute_config_hash,
    compute_run_id,
)
from .costs import FeeModel, SlippageModel, is_tick_aligned, round_to_tick
from .data import (
    BarEvent,
    build_bar_events,
    execution_time_range,
    fingerprint_market_data,
    missing_execution_streams,
    normalize_market_data,
    validate_market_data,
)
from .errors import BacktestDataError, BacktestExecutionError, BacktestSignalError
from .history import MarketHistory
from .interfaces import BacktestBarContext, FeatureRequest, StrategyFeatures
from .metrics import build_drawdown_curve, compute_metrics, compute_monthly_returns
from .models import (
    BacktestMetadata,
    BacktestResult,
    BacktestTrade,
    EquityPoint,
    ExitReason,
    FundingRatePoint,
    PendingOrderInfo,
    RejectedSignal,
    RejectionReason,
    RiskRequest,
    TradeExit,
)


class _StrategyLike(Protocol):
    name: str
    version: str

    def on_bar(self, context: BacktestBarContext) -> Sequence[Signal]: ...


class _RiskGateLike(Protocol):
    def evaluate(self, request: RiskRequest) -> RiskDecision: ...


@dataclass
class _OpenPosition:
    """引擎内部的未平仓持仓。"""

    inst_id: str
    side: SignalSide
    qty: Decimal
    initial_qty: Decimal
    entry_price: Decimal
    entry_ts_ms: int
    stop_loss: Decimal | None
    initial_stop: Decimal
    take_profits: tuple[TakeProfit, ...]
    tp_index: int
    strategy_name: str
    signal_id: str
    cl_ord_id: str
    entry_fee: Decimal
    entry_slippage_cost: Decimal
    funding: Decimal
    exits: list[TradeExit] = field(default_factory=list)
    gross_pnl: Decimal = Decimal("0")
    entry_exec_index: int = 0
    last_funding_ts_ms: int = -1


@dataclass
class _PendingOrder:
    """等待下一根执行周期开盘处理的订单。"""

    signal: Signal
    qty: Decimal
    remaining_bars: int
    cl_ord_id: str


def _direction(side: SignalSide) -> Decimal:
    return Decimal("1") if side is SignalSide.LONG else Decimal("-1")


def _is_buy(side: SignalSide, *, entering: bool) -> bool:
    if side is SignalSide.LONG:
        return entering
    return not entering


class BacktestEngine:
    """一个回测引擎实例只能运行一次。"""

    def __init__(
        self,
        *,
        data: Mapping[str, Mapping[CandleBar, Sequence[Candle]]],
        instruments: Mapping[str, Instrument],
        strategy: _StrategyLike,
        risk_gate: _RiskGateLike,
        config: BacktestConfig | None = None,
        funding_rates: Mapping[str, Sequence[FundingRatePoint]] | None = None,
        strategy_params: Mapping[str, Any] | None = None,
        feature_provider: Callable[[FeatureRequest], StrategyFeatures] | None = None,
        data_source: str | None = None,
        code_commit: str | None = None,
    ) -> None:
        self.config = config or BacktestConfig()
        self._data = normalize_market_data(data)
        self._instruments = dict(instruments)
        self._strategy = strategy
        self._risk_gate = risk_gate
        self._feature_provider = feature_provider
        self._strategy_params = dict(strategy_params or {})
        self._data_source = data_source
        self._code_commit = code_commit
        self._on_fill = getattr(strategy, "on_fill", None)
        self._validate_components()
        self._funding_rates, self._funding_ts = _normalize_funding_rates(funding_rates)

        self._quality, quality_warnings = validate_market_data(self._data)
        self._warnings = list(quality_warnings)
        missing = missing_execution_streams(self._data, self.config.execution_bar)
        for inst_id in missing:
            self._warnings.append(
                f"{inst_id} 缺少执行周期 {self.config.execution_bar.value} 行情，无法撮合"
            )

        self._data_start_close, self._data_end_close = execution_time_range(
            self._data, self.config.execution_bar
        )
        self._effective_start = (
            self.config.start_ts_ms
            if self.config.start_ts_ms is not None
            else self._data_start_close
        )
        self._effective_end = (
            self.config.end_ts_ms if self.config.end_ts_ms is not None else self._data_end_close
        )
        if self._effective_end < self._data_start_close:
            raise BacktestDataError("交易窗口结束时间早于第一根 K 线收盘时间")
        if self._effective_start > self._data_end_close:
            raise BacktestDataError("交易窗口开始时间晚于最后一根 K 线收盘时间")

        self._events = build_bar_events(self._data, max_close_ts=self._effective_end)
        if self._events[-1].close_ts_ms < self._effective_start:
            raise BacktestDataError("交易窗口内没有已收盘 K 线")

        self._config_hash = compute_config_hash(self.config)
        self._data_fingerprint = fingerprint_market_data(self._data)
        if self.config.run_id is not None:
            self._run_id = self.config.run_id
        else:
            self._run_id = compute_run_id(
                config_hash=self._config_hash,
                data_fingerprint=self._data_fingerprint,
                strategy_name=self._strategy.name,
                strategy_version=self._strategy.version,
                strategy_params=self._strategy_params,
            )
        self._ran = False

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def config_hash(self) -> str:
        return self._config_hash

    @property
    def data_fingerprint(self) -> str:
        return self._data_fingerprint

    def run(self) -> BacktestResult:
        """执行回测。"""

        if self._ran:
            raise RuntimeError("BacktestEngine 实例只能运行一次，请创建新实例或使用 run_backtest")
        self._ran = True
        return _Simulation(self).execute()

    def _validate_components(self) -> None:
        name = getattr(self._strategy, "name", None)
        version = getattr(self._strategy, "version", None)
        if not isinstance(name, str) or not name.strip():
            raise BacktestDataError("策略必须提供非空 name")
        if not isinstance(version, str) or not version.strip():
            raise BacktestDataError("策略必须提供非空 version")
        if not callable(getattr(self._strategy, "on_bar", None)):
            raise BacktestDataError("策略必须实现 on_bar")
        if self._on_fill is not None and not callable(self._on_fill):
            raise BacktestDataError("策略 on_fill 必须可调用")
        if not callable(getattr(self._risk_gate, "evaluate", None)):
            raise BacktestDataError("风控必须实现 evaluate")
        if self._feature_provider is not None and not callable(self._feature_provider):
            raise BacktestDataError("feature_provider 必须可调用")
        for inst_id in self._data:
            if inst_id not in self._instruments:
                raise BacktestDataError(f"缺少交易对元数据: {inst_id}")
            instrument = self._instruments[inst_id]
            if instrument.inst_id != inst_id:
                raise BacktestDataError(
                    f"交易对元数据 inst_id 不一致: {inst_id} != {instrument.inst_id}"
                )


class _Simulation:
    """单次回测的可变状态。"""

    def __init__(self, engine: BacktestEngine) -> None:
        self.engine = engine
        self.config = engine.config
        self.data = engine._data
        self.instruments = engine._instruments
        self.strategy = engine._strategy
        self.risk_gate = engine._risk_gate
        self.feature_provider = engine._feature_provider
        self.on_fill = engine._on_fill
        self.funding_rates = engine._funding_rates
        self.funding_ts = engine._funding_ts
        self.events = engine._events
        self.effective_start = engine._effective_start
        self.effective_end = engine._effective_end
        self.fee_model = FeeModel(self.config.fee_maker, self.config.fee_taker)
        self.slippage_model = SlippageModel(self.config.slippage)

        self.history = MarketHistory(self.data)
        self.wallet = self.config.initial_equity
        self.positions: dict[str, _OpenPosition] = {}
        self.pending: dict[str, list[_PendingOrder]] = {inst_id: [] for inst_id in self.data}
        self.trades: list[BacktestTrade] = []
        self.rejected: list[RejectedSignal] = []
        self.equity_curve: list[EquityPoint] = []
        self.warnings = list(engine._warnings)
        self.last_close: dict[str, Decimal] = {}
        self.exec_index: dict[str, int] = {inst_id: 0 for inst_id in self.data}
        self.signal_count = 0
        self.warmup_signal_count = 0
        self.total_fees = Decimal("0")
        self.total_funding = Decimal("0")
        self.fill_counter = 0
        self.trade_counter = 0
        self.order_counter = 0
        self.halted = False
        self.finalized = False
        self.last_event: BarEvent | None = None
        self.funding_warned: set[str] = set()
        self.feature_cache: dict[tuple[str, CandleBar, int], StrategyFeatures] = {}
        self.day_key: tuple[int, int, int] | None = None
        self.week_key: tuple[int, int] | None = None
        self.day_start_equity = self.config.initial_equity
        self.week_start_equity = self.config.initial_equity

    def execute(self) -> BacktestResult:
        index = 0
        while index < len(self.events):
            close_ts = self.events[index].close_ts_ms
            group: list[BarEvent] = []
            while index < len(self.events) and self.events[index].close_ts_ms == close_ts:
                group.append(self.events[index])
                index += 1
            self._handle_group(group, close_ts)
            if self.finalized:
                break
        if not self.finalized:
            final_ts = self.last_event.close_ts_ms if self.last_event else self.effective_end
            self._finalize(final_ts)
        return self._build_result()

    def _handle_group(self, group: Sequence[BarEvent], close_ts: int) -> None:
        self._roll_equity_anchors(close_ts)
        in_window = close_ts >= self.effective_start
        has_execution_bar = False
        for event in group:
            self.last_event = event
            self.history.mark_closed(event.inst_id, event.bar)
            self.last_close[event.inst_id] = event.candle.close
            if event.bar is self.config.execution_bar:
                has_execution_bar = True
                self.exec_index[event.inst_id] += 1
                self._apply_funding(
                    event.inst_id,
                    up_to_ts=event.candle.ts_ms,
                    mark_price=event.candle.open,
                )
                self._process_pending_orders(event)
                self._process_protective_exits(event)
                self._apply_funding(
                    event.inst_id,
                    up_to_ts=close_ts,
                    mark_price=event.candle.close,
                )
            if close_ts < self.effective_start:
                self._call_strategy(event, warmup=True, at_end=False)
            else:
                self._call_strategy(event, warmup=False, at_end=close_ts >= self.effective_end)

        if close_ts >= self.effective_end:
            self._finalize(close_ts)
            return

        if in_window:
            if self._current_equity() <= 0:
                if self.positions:
                    self.warnings.append("账户权益小于等于 0，按清算处理并强制平仓")
                    self._force_close_all(self.last_event, ExitReason.LIQUIDATION)
                self.halted = True
                self.warnings.append("账户权益小于等于 0，回测提前终止")
                self._finalize(close_ts)
                return
            if has_execution_bar:
                self._record_equity(close_ts)

    def _finalize(self, close_ts: int) -> None:
        if self.finalized:
            return
        if self.positions:
            if self.config.close_positions_at_end:
                self._force_close_all(self.last_event, ExitReason.END_OF_BACKTEST)
            else:
                self.warnings.append(
                    "回测结束时仍有未平仓持仓，权益包含未实现盈亏，费用与资金费率未完全计入"
                )
        for inst_id in sorted(self.pending):
            for order in self.pending[inst_id]:
                self._reject(
                    order.signal,
                    order.signal.created_ts_ms,
                    RejectionReason.UNFILLED_AT_END,
                    "回测结束时挂单仍未成交",
                )
            self.pending[inst_id] = []
        self._record_equity(close_ts, force=True)
        if self.warmup_signal_count:
            self.warnings.append(
                f"开始交易前有 {self.warmup_signal_count} 个信号被忽略，仅用于策略预热"
            )
        self.finalized = True

    def _call_strategy(self, event: BarEvent, *, warmup: bool, at_end: bool) -> None:
        context = self._build_context(event)
        try:
            raw_signals = self.strategy.on_bar(context)
        except Exception as exc:  # noqa: BLE001
            raise BacktestExecutionError(
                f"策略 {self.strategy.name} 在 {event.inst_id} {event.close_ts_ms} 调用 on_bar 失败"
            ) from exc
        if raw_signals is None:
            raise BacktestSignalError(
                f"策略 {self.strategy.name} 在 {event.inst_id} {event.close_ts_ms} "
                "返回 None，必须返回信号列表"
            )
        try:
            signals = list(raw_signals)
        except TypeError as exc:
            raise BacktestSignalError("策略 on_bar 必须返回可迭代的信号序列") from exc
        for signal in signals:
            self._validate_signal(signal, event)
            if warmup:
                self.warmup_signal_count += 1
                continue
            self.signal_count += 1
            if at_end:
                self._reject(
                    signal,
                    event.close_ts_ms,
                    RejectionReason.OUTSIDE_TRADING_WINDOW,
                    "交易窗口已结束，信号没有下一根执行 K 线可成交",
                )
                continue
            self._handle_signal(signal, event, context)

    def _validate_signal(self, signal: object, event: BarEvent) -> None:
        if not isinstance(signal, Signal):
            raise BacktestSignalError(
                f"策略 {self.strategy.name} 返回了非 Signal 对象: {type(signal).__name__}"
            )
        if signal.inst_id != event.inst_id:
            raise BacktestSignalError(
                f"信号 inst_id={signal.inst_id} 与当前处理 K 线 {event.inst_id} 不一致"
            )
        if signal.created_ts_ms != event.close_ts_ms:
            raise BacktestSignalError(
                f"信号 created_ts_ms={signal.created_ts_ms} 必须等于当前 K 线收盘时间 "
                f"{event.close_ts_ms}，否则会引入前视偏差"
            )
        instrument = self.instruments[signal.inst_id]
        if signal.action is SignalAction.OPEN:
            stop = signal.stop_loss
            if stop is None:
                raise BacktestSignalError("开仓信号必须包含 stop_loss")
            self._require_tick(stop, instrument, "stop_loss")
            if signal.entry_type is OrderType.LIMIT:
                self._require_tick(signal.price, instrument, "price")
            if not self._valid_stop_side(signal, stop):
                raise BacktestSignalError("开仓止损方向错误：多头止损必须低于参考价，空头反之")
            if not signal.take_profits:
                fraction_sum = Decimal("0")
            else:
                fraction_sum = sum(
                    (take_profit.fraction for take_profit in signal.take_profits), Decimal("0")
                )
            if fraction_sum > 1:
                raise BacktestSignalError("止盈 fraction 之和不能超过 1")
            for take_profit in signal.take_profits:
                self._require_tick(take_profit.price, instrument, "take_profit.price")
                if signal.side is SignalSide.LONG and take_profit.price <= signal.price:
                    raise BacktestSignalError("多头止盈价必须高于信号参考价")
                if signal.side is SignalSide.SHORT and take_profit.price >= signal.price:
                    raise BacktestSignalError("空头止盈价必须低于信号参考价")
        elif signal.action is SignalAction.MOVE_STOP:
            if signal.stop_loss is None:
                raise BacktestSignalError("move_stop 信号必须包含新的 stop_loss")
            self._require_tick(signal.stop_loss, instrument, "stop_loss")

    def _require_tick(self, price: Decimal, instrument: Instrument, field_name: str) -> None:
        if not is_tick_aligned(price, instrument.tick_sz):
            raise BacktestSignalError(
                f"{field_name}={price} 未按 {instrument.inst_id} tick_sz={instrument.tick_sz} 对齐"
            )

    @staticmethod
    def _valid_stop_side(signal: Signal, stop: Decimal) -> bool:
        if signal.side is SignalSide.LONG:
            return stop < signal.price
        return stop > signal.price

    def _handle_signal(
        self,
        signal: Signal,
        event: BarEvent,
        context: BacktestBarContext,
    ) -> None:
        inst_id = signal.inst_id
        if signal.action is SignalAction.OPEN:
            self._handle_open_signal(signal, event, context)
        elif signal.action is SignalAction.CLOSE:
            position = self.positions.get(inst_id)
            if position is None:
                self._reject(
                    signal,
                    event.close_ts_ms,
                    RejectionReason.NO_POSITION,
                    "收到 close 信号但当前没有持仓",
                )
                return
            if signal.side is not position.side:
                self._reject(
                    signal,
                    event.close_ts_ms,
                    RejectionReason.SIDE_MISMATCH,
                    f"信号方向 {signal.side} 与持仓方向 {position.side} 不一致",
                )
                return
            self._queue(
                signal,
                qty=position.qty,
                remaining_bars=1,
            )
        elif signal.action is SignalAction.REDUCE:
            self._handle_reduce_signal(signal, event, context)
        elif signal.action is SignalAction.MOVE_STOP:
            self._handle_move_stop_signal(signal, event)
        else:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.UNSUPPORTED_ACTION,
                f"不支持的信号动作: {signal.action}",
            )

    def _handle_open_signal(
        self,
        signal: Signal,
        event: BarEvent,
        context: BacktestBarContext,
    ) -> None:
        inst_id = signal.inst_id
        if inst_id in self.positions:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.POSITION_CONFLICT,
                "同一交易对最多持有一个方向，已有持仓时拒绝开仓",
            )
            return
        decision = self._evaluate_risk(signal, event, context)
        if not decision.approved:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.RISK_VETO,
                f"{decision.rule}: {decision.detail}",
            )
            return
        problem = self._quantity_problem(decision.qty, inst_id)
        if problem is not None:
            self._reject(signal, event.close_ts_ms, RejectionReason.INVALID_QUANTITY, problem)
            return
        self._queue(signal, qty=decision.qty, remaining_bars=self.config.limit_order_ttl_bars)

    def _handle_reduce_signal(
        self,
        signal: Signal,
        event: BarEvent,
        context: BacktestBarContext,
    ) -> None:
        inst_id = signal.inst_id
        position = self.positions.get(inst_id)
        if position is None:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.NO_POSITION,
                "收到 reduce 信号但当前没有持仓",
            )
            return
        if signal.side is not position.side:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.SIDE_MISMATCH,
                f"信号方向 {signal.side} 与持仓方向 {position.side} 不一致",
            )
            return
        decision = self._evaluate_risk(signal, event, context)
        if not decision.approved:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.RISK_VETO,
                f"{decision.rule}: {decision.detail}",
            )
            return
        problem = self._quantity_problem(decision.qty, inst_id)
        if problem is not None:
            self._reject(signal, event.close_ts_ms, RejectionReason.INVALID_QUANTITY, problem)
            return
        if decision.qty > position.qty:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.INVALID_QUANTITY,
                f"减仓数量 {decision.qty} 超过当前持仓 {position.qty}",
            )
            return
        self._queue(signal, qty=decision.qty, remaining_bars=1)

    def _handle_move_stop_signal(self, signal: Signal, event: BarEvent) -> None:
        inst_id = signal.inst_id
        position = self.positions.get(inst_id)
        if position is None:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.NO_POSITION,
                "收到 move_stop 信号但当前没有持仓",
            )
            return
        if signal.side is not position.side:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.SIDE_MISMATCH,
                f"信号方向 {signal.side} 与持仓方向 {position.side} 不一致",
            )
            return
        new_stop = signal.stop_loss
        if new_stop is None:
            raise BacktestSignalError("move_stop 信号必须包含新的 stop_loss")
        if not self._valid_stop_update(position, new_stop, signal.price):
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.INVALID_STOP_UPDATE,
                "移动止损只能收紧，且必须位于信号参考价的保护侧",
            )
            return
        self._queue(signal, qty=Decimal("0"), remaining_bars=1)

    @staticmethod
    def _valid_stop_update(
        position: _OpenPosition,
        new_stop: Decimal,
        reference_price: Decimal,
    ) -> bool:
        if position.side is SignalSide.LONG:
            lower_bound = position.stop_loss if position.stop_loss is not None else Decimal("0")
            return new_stop > lower_bound and new_stop < reference_price
        upper_bound = position.stop_loss if position.stop_loss is not None else Decimal("Infinity")
        return new_stop < upper_bound and new_stop > reference_price

    @staticmethod
    def _is_tighter_stop(position: _OpenPosition, new_stop: Decimal) -> bool:
        if position.stop_loss is None:
            return True
        if position.side is SignalSide.LONG:
            return new_stop > position.stop_loss
        return new_stop < position.stop_loss

    def _roll_equity_anchors(self, close_ts: int) -> None:
        """按北京时间滚动日/周起始权益，供 M08 计算日亏损和周亏损。"""

        local = datetime.fromtimestamp(close_ts / 1000, tz=SHANGHAI_TZ)
        day_key = (local.year, local.month, local.day)
        iso = local.isocalendar()
        week_key = (iso.year, iso.week)
        if self.day_key is None:
            self.day_key = day_key
        elif day_key != self.day_key:
            self.day_key = day_key
            self.day_start_equity = self._current_equity()
        if self.week_key is None:
            self.week_key = week_key
        elif week_key != self.week_key:
            self.week_key = week_key
            self.week_start_equity = self._current_equity()

    def _latest_funding_rate(self, inst_id: str, ts_ms: int) -> Decimal | None:
        """返回截至当前时刻已知的最近资金费率。"""

        timestamps = self.funding_ts.get(inst_id, ())
        if not timestamps:
            return None
        index = bisect.bisect_right(timestamps, ts_ms) - 1
        if index < 0:
            return None
        return self.funding_rates[inst_id][index].rate

    def _evaluate_risk(
        self,
        signal: Signal,
        event: BarEvent,
        context: BacktestBarContext,
    ) -> RiskDecision:
        pending_info: list[PendingOrderInfo] = []
        for inst_id in sorted(self.pending):
            for order in self.pending[inst_id]:
                pending_info.append(
                    PendingOrderInfo(
                        inst_id=order.signal.inst_id,
                        side=order.signal.side,
                        action=order.signal.action,
                        qty=order.qty,
                        created_ts_ms=order.signal.created_ts_ms,
                    )
                )
        pending_info.sort(key=lambda item: (item.inst_id, item.created_ts_ms, item.qty))
        request = RiskRequest(
            signal=signal,
            equity=context.equity,
            wallet_balance=context.wallet_balance,
            used_margin=context.used_margin,
            available_equity=context.equity - context.used_margin,
            positions=context.positions,
            pending_orders=tuple(pending_info),
            day_start_equity=self.day_start_equity,
            week_start_equity=self.week_start_equity,
            funding_rate=self._latest_funding_rate(signal.inst_id, event.close_ts_ms),
            ts_ms=event.close_ts_ms,
        )
        try:
            decision = self.risk_gate.evaluate(request)
        except Exception as exc:  # noqa: BLE001
            raise BacktestExecutionError(
                f"风控在 {signal.inst_id} {event.close_ts_ms} 审核信号失败"
            ) from exc
        if not isinstance(decision, RiskDecision):
            raise BacktestExecutionError("风控必须返回公共 RiskDecision 模型")
        if decision.signal_id != signal.signal_id:
            raise BacktestExecutionError(
                f"风控返回的 signal_id={decision.signal_id} 与请求 {signal.signal_id} 不一致"
            )
        return decision

    def _queue(self, signal: Signal, *, qty: Decimal, remaining_bars: int) -> None:
        self.order_counter += 1
        order = _PendingOrder(
            signal=signal,
            qty=qty,
            remaining_bars=remaining_bars,
            cl_ord_id=f"{self.engine.run_id[:10]}-{self.order_counter:06d}",
        )
        self.pending[signal.inst_id].append(order)

    def _process_pending_orders(self, event: BarEvent) -> None:
        inst_id = event.inst_id
        orders = self.pending[inst_id]
        if not orders:
            return
        self.pending[inst_id] = []
        kept: list[_PendingOrder] = []
        exits = [order for order in orders if order.signal.action is not SignalAction.OPEN]
        opens = [order for order in orders if order.signal.action is SignalAction.OPEN]
        for order in exits:
            if self._execute_exit_order(order, event):
                kept.append(order)
        for order in opens:
            if self._execute_open_order(order, event):
                kept.append(order)
        self.pending[inst_id] = kept

    def _execute_open_order(self, order: _PendingOrder, event: BarEvent) -> bool:
        signal = order.signal
        inst_id = signal.inst_id
        if inst_id in self.positions:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.POSITION_CONFLICT,
                "挂单成交前已出现同交易对持仓",
            )
            return False

        price_and_fee = self._entry_fill_price(order, event)
        if price_and_fee is None:
            return False
        if price_and_fee is _KEEP_PENDING:
            return True
        price, maker, reference_price = price_and_fee
        instrument = self.instruments[inst_id]
        fee = self.fee_model.fee(
            price=price,
            size=order.qty,
            ct_val=instrument.ct_val,
            maker=maker,
        )
        required_margin = price * order.qty * instrument.ct_val / self.config.leverage + fee
        available = self._current_equity() - self._used_margin()
        if required_margin > available:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.INSUFFICIENT_MARGIN,
                f"所需保证金 {required_margin} 超过可用权益 {available}",
            )
            return False

        self.wallet -= fee
        self.total_fees += fee
        slippage_cost = abs(price - reference_price) * order.qty * instrument.ct_val
        take_profits = tuple(
            sorted(
                signal.take_profits,
                key=lambda item: item.price,
                reverse=signal.side is SignalSide.SHORT,
            )
        )
        position = _OpenPosition(
            inst_id=inst_id,
            side=signal.side,
            qty=order.qty,
            initial_qty=order.qty,
            entry_price=price,
            entry_ts_ms=event.candle.ts_ms,
            stop_loss=signal.stop_loss,
            initial_stop=signal.stop_loss if signal.stop_loss is not None else price,
            take_profits=take_profits,
            tp_index=0,
            strategy_name=signal.strategy_name,
            signal_id=signal.signal_id,
            cl_ord_id=order.cl_ord_id,
            entry_fee=fee,
            entry_slippage_cost=slippage_cost,
            funding=Decimal("0"),
            entry_exec_index=self.exec_index[inst_id],
            last_funding_ts_ms=event.candle.ts_ms - 1,
        )
        self.positions[inst_id] = position
        if not self.funding_rates.get(inst_id) and inst_id not in self.funding_warned:
            self.funding_warned.add(inst_id)
            self.warnings.append(f"未提供 {inst_id} 资金费率数据，回测按 0 计")
        self._notify_fill(
            event,
            cl_ord_id=order.cl_ord_id,
            price=price,
            size=order.qty,
            fee=fee,
            ts_ms=event.candle.ts_ms,
        )
        return False

    def _entry_fill_price(
        self,
        order: _PendingOrder,
        event: BarEvent,
    ) -> tuple[Decimal, bool, Decimal] | None | object:
        signal = order.signal
        bar = event.candle
        if signal.entry_type is OrderType.MARKET:
            return (
                self._market_price(
                    bar.open,
                    is_buy=_is_buy(signal.side, entering=True),
                    inst_id=signal.inst_id,
                ),
                False,
                bar.open,
            )
        limit_price = signal.price
        if signal.side is SignalSide.LONG:
            if bar.open <= limit_price:
                return bar.open, False, bar.open
            if bar.low <= limit_price:
                return limit_price, True, limit_price
        else:
            if bar.open >= limit_price:
                return bar.open, False, bar.open
            if bar.high >= limit_price:
                return limit_price, True, limit_price
        order.remaining_bars -= 1
        if order.remaining_bars <= 0:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.LIMIT_ORDER_EXPIRED,
                f"限价单 {limit_price} 在 {self.config.limit_order_ttl_bars} 根执行 K 线内未成交",
            )
            return None
        return _KEEP_PENDING

    def _execute_exit_order(self, order: _PendingOrder, event: BarEvent) -> bool:
        signal = order.signal
        position = self.positions.get(signal.inst_id)
        if position is None:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.NO_POSITION,
                "挂单成交前持仓已经不存在",
            )
            return False
        if signal.action is SignalAction.MOVE_STOP:
            new_stop = signal.stop_loss
            if new_stop is None:
                raise BacktestSignalError("move_stop 信号必须包含新的 stop_loss")
            if signal.side is not position.side or not self._is_tighter_stop(position, new_stop):
                self._reject(
                    signal,
                    event.close_ts_ms,
                    RejectionReason.INVALID_STOP_UPDATE,
                    "执行时新止损没有收紧，或信号方向与持仓不一致",
                )
                return False
            position.stop_loss = new_stop
            return False

        qty = position.qty if signal.action is SignalAction.CLOSE else order.qty
        if qty <= 0 or qty > position.qty:
            self._reject(
                signal,
                event.close_ts_ms,
                RejectionReason.INVALID_QUANTITY,
                f"平仓数量 {qty} 无效，当前持仓 {position.qty}",
            )
            return False
        reference = event.candle.open
        price = self._market_price(
            reference,
            is_buy=_is_buy(position.side, entering=False),
            inst_id=signal.inst_id,
        )
        reason = (
            ExitReason.SIGNAL_CLOSE
            if signal.action is SignalAction.CLOSE
            else ExitReason.SIGNAL_REDUCE
        )
        self._close_position(
            position,
            qty=qty,
            price=price,
            reference_price=reference,
            ts_ms=event.candle.ts_ms,
            reason=reason,
            maker=False,
            event=event,
        )
        return False

    def _process_protective_exits(self, event: BarEvent) -> None:
        position = self.positions.get(event.inst_id)
        if position is None:
            return
        bar = event.candle
        stop = position.stop_loss
        if stop is not None:
            if position.side is SignalSide.LONG:
                stop_touched = bar.low <= stop
                reference = min(stop, bar.open)
                is_buy = False
            else:
                stop_touched = bar.high >= stop
                reference = max(stop, bar.open)
                is_buy = True
            if stop_touched:
                price = self._market_price(
                    reference,
                    is_buy=is_buy,
                    inst_id=event.inst_id,
                )
                self._close_position(
                    position,
                    qty=position.qty,
                    price=price,
                    reference_price=reference,
                    ts_ms=event.close_ts_ms,
                    reason=ExitReason.STOP_LOSS,
                    maker=False,
                    event=event,
                )
                return

        while position.tp_index < len(position.take_profits) and position.qty > 0:
            take_profit = position.take_profits[position.tp_index]
            if position.side is SignalSide.LONG:
                touched = bar.high >= take_profit.price
                reference = max(take_profit.price, bar.open)
                round_up = False
            else:
                touched = bar.low <= take_profit.price
                reference = min(take_profit.price, bar.open)
                round_up = True
            if not touched:
                break
            qty = self._take_profit_quantity(position, take_profit)
            position.tp_index += 1
            if qty <= 0:
                self.warnings.append(
                    f"{position.inst_id} 止盈 {take_profit.price} 按 lot_sz 取整后为 0，已跳过"
                )
                continue
            price = round_to_tick(
                reference,
                self.instruments[position.inst_id].tick_sz,
                round_up=round_up,
            )
            self._close_position(
                position,
                qty=qty,
                price=price,
                reference_price=reference,
                ts_ms=event.close_ts_ms,
                reason=ExitReason.TAKE_PROFIT,
                maker=True,
                event=event,
            )

    def _take_profit_quantity(self, position: _OpenPosition, take_profit: TakeProfit) -> Decimal:
        remaining_fraction = sum(
            (item.fraction for item in position.take_profits[position.tp_index :]),
            Decimal("0"),
        )
        lot = self.instruments[position.inst_id].lot_sz
        if position.tp_index == len(position.take_profits) - 1 and remaining_fraction >= 1:
            return position.qty
        raw = position.initial_qty * take_profit.fraction
        qty = (raw / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
        return min(qty, position.qty)

    def _close_position(
        self,
        position: _OpenPosition,
        *,
        qty: Decimal,
        price: Decimal,
        reference_price: Decimal,
        ts_ms: int,
        reason: ExitReason,
        maker: bool,
        event: BarEvent | None,
    ) -> None:
        if qty <= 0:
            raise BacktestExecutionError("平仓数量必须大于 0")
        if qty > position.qty:
            raise BacktestExecutionError("平仓数量超过持仓数量")
        instrument = self.instruments[position.inst_id]
        fee = self.fee_model.fee(
            price=price,
            size=qty,
            ct_val=instrument.ct_val,
            maker=maker,
        )
        direction = _direction(position.side)
        gross = (price - position.entry_price) * qty * instrument.ct_val * direction
        slippage_cost = abs(price - reference_price) * qty * instrument.ct_val
        net = gross - fee

        self.wallet += gross - fee
        self.total_fees += fee
        position.qty -= qty
        position.gross_pnl += gross
        exit_leg = TradeExit(
            ts_ms=ts_ms,
            price=price,
            qty=qty,
            fee=fee,
            reason=reason,
            gross_pnl=gross,
            net_pnl=net,
            slippage_cost=slippage_cost,
        )
        position.exits.append(exit_leg)
        self._notify_fill(
            event,
            cl_ord_id=position.cl_ord_id,
            price=price,
            size=qty,
            fee=fee,
            ts_ms=ts_ms,
        )
        if position.qty == 0:
            del self.positions[position.inst_id]
            self._finalize_trade(position, ts_ms)

    def _finalize_trade(self, position: _OpenPosition, exit_ts_ms: int) -> None:
        self.trade_counter += 1
        instrument = self.instruments[position.inst_id]
        entry_fee = position.entry_fee
        exit_fees = sum((leg.fee for leg in position.exits), Decimal("0"))
        total_fees = entry_fee + exit_fees
        gross = position.gross_pnl
        net = gross - total_fees - position.funding
        initial_notional = position.entry_price * position.initial_qty * instrument.ct_val
        average_exit = (
            sum((leg.price * leg.qty for leg in position.exits), Decimal("0"))
            / position.initial_qty
        )
        initial_risk = (
            abs(position.entry_price - position.initial_stop)
            * position.initial_qty
            * instrument.ct_val
        )
        holding_bars = max(
            1,
            self.exec_index[position.inst_id] - position.entry_exec_index + 1,
        )
        self.trades.append(
            BacktestTrade(
                trade_id=f"trade-{self.trade_counter:06d}",
                signal_id=position.signal_id,
                cl_ord_id=position.cl_ord_id,
                inst_id=position.inst_id,
                strategy_name=position.strategy_name,
                side=position.side,
                initial_qty=position.initial_qty,
                entry_price=position.entry_price,
                entry_ts_ms=position.entry_ts_ms,
                exit_ts_ms=exit_ts_ms,
                average_exit_price=average_exit,
                gross_pnl=gross,
                fees=total_fees,
                funding=position.funding,
                slippage_cost=position.entry_slippage_cost
                + sum((leg.slippage_cost for leg in position.exits), Decimal("0")),
                net_pnl=net,
                return_pct=float(net / initial_notional) if initial_notional > 0 else 0.0,
                r_multiple=float(net / initial_risk) if initial_risk > 0 else None,
                holding_bars=holding_bars,
                holding_ms=max(0, exit_ts_ms - position.entry_ts_ms),
                exits=tuple(position.exits),
            )
        )

    def _force_close_all(self, event: BarEvent | None, reason: ExitReason) -> None:
        if not self.positions:
            return
        for inst_id in sorted(list(self.positions)):
            position = self.positions[inst_id]
            reference = self.last_close.get(inst_id, position.entry_price)
            price = self._market_price(
                reference,
                is_buy=_is_buy(position.side, entering=False),
                inst_id=inst_id,
            )
            self._close_position(
                position,
                qty=position.qty,
                price=price,
                reference_price=reference,
                ts_ms=event.close_ts_ms if event is not None else self.effective_end,
                reason=reason,
                maker=False,
                event=event,
            )

    def _apply_funding(self, inst_id: str, *, up_to_ts: int, mark_price: Decimal) -> None:
        position = self.positions.get(inst_id)
        if position is None or position.qty <= 0:
            return
        timestamps = self.funding_ts.get(inst_id, ())
        if not timestamps:
            return
        rates = self.funding_rates[inst_id]
        upper = bisect.bisect_right(timestamps, up_to_ts)
        instrument = self.instruments[inst_id]
        for index in range(upper):
            point = rates[index]
            if point.ts_ms < position.entry_ts_ms:
                continue
            if point.ts_ms <= position.last_funding_ts_ms:
                continue
            notional = mark_price * position.qty * instrument.ct_val
            cost = notional * point.rate * _direction(position.side)
            self.wallet -= cost
            position.funding += cost
            self.total_funding += cost
            position.last_funding_ts_ms = point.ts_ms

    def _market_price(self, price: Decimal, *, is_buy: bool, inst_id: str) -> Decimal:
        slipped = self.slippage_model.apply(price, is_buy=is_buy)
        return round_to_tick(slipped, self.instruments[inst_id].tick_sz, round_up=is_buy)

    def _quantity_problem(self, qty: Decimal, inst_id: str) -> str | None:
        instrument = self.instruments[inst_id]
        if qty <= 0:
            return "风控数量必须大于 0"
        if qty % instrument.lot_sz != 0:
            return f"数量 {qty} 未按 lot_sz={instrument.lot_sz} 对齐"
        if qty < instrument.min_sz:
            return f"数量 {qty} 小于 min_sz={instrument.min_sz}"
        return None

    def _current_equity(self) -> Decimal:
        return self.wallet + self._unrealized_pnl()

    def _unrealized_pnl(self) -> Decimal:
        total = Decimal("0")
        for position in self.positions.values():
            mark = self.last_close.get(position.inst_id, position.entry_price)
            instrument = self.instruments[position.inst_id]
            total += (
                (mark - position.entry_price)
                * position.qty
                * instrument.ct_val
                * _direction(position.side)
            )
        return total

    def _used_margin(self) -> Decimal:
        total = Decimal("0")
        for position in self.positions.values():
            instrument = self.instruments[position.inst_id]
            total += position.entry_price * position.qty * instrument.ct_val / self.config.leverage
        return total

    def _positions_snapshot(self) -> tuple[Position, ...]:
        snapshots: list[Position] = []
        for inst_id in sorted(self.positions):
            position = self.positions[inst_id]
            instrument = self.instruments[inst_id]
            mark = self.last_close.get(inst_id, position.entry_price)
            upl = (
                (mark - position.entry_price)
                * position.qty
                * instrument.ct_val
                * _direction(position.side)
            )
            snapshots.append(
                Position(
                    inst_id=inst_id,
                    pos_side=(
                        PositionSide.LONG
                        if position.side is SignalSide.LONG
                        else PositionSide.SHORT
                    ),
                    size=position.qty,
                    avg_price=position.entry_price,
                    mark_price=mark,
                    upl=upl,
                    stop_loss=position.stop_loss,
                    strategy=position.strategy_name,
                    opened_ts_ms=position.entry_ts_ms,
                )
            )
        return tuple(snapshots)

    def _build_context(self, event: BarEvent) -> BacktestBarContext:
        history = self.history.snapshot()
        cache_key = (event.inst_id, event.bar, event.close_ts_ms)
        features = self.feature_cache.get(cache_key)
        if features is None:
            features = StrategyFeatures()
            if self.feature_provider is not None:
                request = FeatureRequest(
                    inst_id=event.inst_id,
                    bar=event.bar,
                    close_ts_ms=event.close_ts_ms,
                    history=history,
                )
                try:
                    features = self.feature_provider(request)
                except Exception as exc:  # noqa: BLE001
                    raise BacktestExecutionError(
                        f"特征提供器在 {event.inst_id} {event.close_ts_ms} 失败"
                    ) from exc
                if not isinstance(features, StrategyFeatures):
                    raise BacktestExecutionError("feature_provider 必须返回 StrategyFeatures")
            self.feature_cache[cache_key] = features
        return BacktestBarContext(
            inst_id=event.inst_id,
            bar=event.bar,
            candle=event.candle,
            close_ts_ms=event.close_ts_ms,
            equity=self._current_equity(),
            wallet_balance=self.wallet,
            used_margin=self._used_margin(),
            positions=self._positions_snapshot(),
            instrument=self.instruments[event.inst_id],
            history=history,
            features=features,
        )

    def _notify_fill(
        self,
        event: BarEvent | None,
        *,
        cl_ord_id: str,
        price: Decimal,
        size: Decimal,
        fee: Decimal,
        ts_ms: int,
    ) -> None:
        if self.on_fill is None or event is None:
            return
        self.fill_counter += 1
        fill = Fill(
            trade_id=f"fill-{self.fill_counter:08d}",
            cl_ord_id=cl_ord_id,
            price=price,
            size=size,
            fee=fee,
            ts_ms=ts_ms,
        )
        try:
            self.on_fill(self._build_context(event), fill)
        except Exception as exc:  # noqa: BLE001
            raise BacktestExecutionError(
                f"策略 {self.strategy.name} 处理成交回报 {fill.trade_id} 失败"
            ) from exc

    def _record_equity(self, ts_ms: int, *, force: bool = False) -> None:
        if not force and ts_ms < self.effective_start:
            return
        point = EquityPoint(
            ts_ms=ts_ms,
            equity=self._current_equity(),
            wallet_balance=self.wallet,
            unrealized_pnl=self._unrealized_pnl(),
            used_margin=self._used_margin(),
        )
        if self.equity_curve and self.equity_curve[-1].ts_ms == ts_ms:
            self.equity_curve[-1] = point
        else:
            self.equity_curve.append(point)

    def _reject(
        self,
        signal: Signal,
        ts_ms: int,
        reason: RejectionReason,
        detail: str,
    ) -> None:
        self.rejected.append(
            RejectedSignal(
                ts_ms=ts_ms,
                inst_id=signal.inst_id,
                signal_id=signal.signal_id,
                action=signal.action,
                reason=reason,
                detail=detail,
                strategy_name=signal.strategy_name,
            )
        )

    def _build_result(self) -> BacktestResult:
        equity_curve = tuple(self.equity_curve)
        trades = tuple(self.trades)
        metrics = compute_metrics(
            equity_curve=equity_curve,
            trades=trades,
            execution_bar=self.config.execution_bar,
            initial_equity=self.config.initial_equity,
            signal_count=self.signal_count,
            rejected_signal_count=len(self.rejected),
        )
        period_start = equity_curve[0].ts_ms if equity_curve else None
        period_end = equity_curve[-1].ts_ms if equity_curve else None
        metadata = BacktestMetadata(
            run_id=self.engine.run_id,
            strategy_name=self.strategy.name,
            strategy_version=self.strategy.version,
            strategy_params=dict(self.engine._strategy_params),
            engine_version=ENGINE_VERSION,
            fee_model_version=FEE_MODEL_VERSION,
            slippage_model_version=SLIPPAGE_MODEL_VERSION,
            funding_model_version=FUNDING_MODEL_VERSION,
            config_hash=self.engine.config_hash,
            data_fingerprint=self.engine.data_fingerprint,
            data_source=self.engine._data_source,
            code_commit=self.engine._code_commit,
            random_seed=self.config.random_seed,
            execution_bar=self.config.execution_bar,
            initial_equity=self.config.initial_equity,
            period_start_ts_ms=period_start,
            period_end_ts_ms=period_end,
            instruments=tuple(sorted(self.data)),
        )
        return BacktestResult(
            config=self.config,
            metadata=metadata,
            metrics=metrics,
            equity_curve=equity_curve,
            drawdown_curve=build_drawdown_curve(equity_curve),
            trades=trades,
            rejected_signals=tuple(self.rejected),
            monthly_returns=compute_monthly_returns(
                equity_curve,
                self.config.initial_equity,
                self.config.timezone,
            ),
            stream_quality=self.engine._quality,
            warnings=tuple(_deduplicate(self.warnings)),
        )


_KEEP_PENDING = object()


def _normalize_funding_rates(
    funding_rates: Mapping[str, Sequence[FundingRatePoint]] | None,
) -> tuple[dict[str, tuple[FundingRatePoint, ...]], dict[str, tuple[int, ...]]]:
    if not funding_rates:
        return {}, {}
    normalized: dict[str, tuple[FundingRatePoint, ...]] = {}
    timestamps: dict[str, tuple[int, ...]] = {}
    for inst_id, points in funding_rates.items():
        if not points:
            normalized[inst_id] = ()
            timestamps[inst_id] = ()
            continue
        ordered = sorted(points, key=lambda item: item.ts_ms)
        seen: set[int] = set()
        clean: list[FundingRatePoint] = []
        for point in ordered:
            if point.ts_ms < 0:
                raise BacktestDataError(f"{inst_id} 资金费率时间戳不能为负")
            if point.ts_ms in seen:
                raise BacktestDataError(f"{inst_id} 资金费率时间戳重复: {point.ts_ms}")
            seen.add(point.ts_ms)
            rate = point.rate
            if not isinstance(rate, Decimal):
                try:
                    rate = Decimal(str(rate))
                except (InvalidOperation, ValueError) as exc:
                    raise BacktestDataError(f"{inst_id} 资金费率非法: {point.rate}") from exc
            if not rate.is_finite() or abs(rate) >= 1:
                raise BacktestDataError(f"{inst_id} 资金费率非法: {point.rate}")
            clean.append(FundingRatePoint(ts_ms=point.ts_ms, rate=rate))
        normalized[inst_id] = tuple(clean)
        timestamps[inst_id] = tuple(point.ts_ms for point in clean)
    return normalized, timestamps


def _deduplicate(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def run_backtest(
    *,
    data: Mapping[str, Mapping[CandleBar, Sequence[Candle]]],
    instruments: Mapping[str, Instrument],
    strategy: _StrategyLike,
    risk_gate: _RiskGateLike,
    config: BacktestConfig | None = None,
    funding_rates: Mapping[str, Sequence[FundingRatePoint]] | None = None,
    strategy_params: Mapping[str, Any] | None = None,
    feature_provider: Callable[[FeatureRequest], StrategyFeatures] | None = None,
    data_source: str | None = None,
    code_commit: str | None = None,
) -> BacktestResult:
    """便捷入口：创建一次性引擎并执行回测。"""

    engine = BacktestEngine(
        data=data,
        instruments=instruments,
        strategy=strategy,
        risk_gate=risk_gate,
        config=config,
        funding_rates=funding_rates,
        strategy_params=strategy_params,
        feature_provider=feature_provider,
        data_source=data_source,
        code_commit=code_commit,
    )
    return engine.run()
