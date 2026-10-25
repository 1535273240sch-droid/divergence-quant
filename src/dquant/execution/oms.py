"""订单管理系统（OMS）和模拟盘执行流程。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Literal

from dquant.contracts import (
    Fill,
    Instrument,
    OrderAck,
    OrderRequest,
    OrderState,
    OrderType,
    PositionSide,
    RiskDecision,
    Signal,
    SignalAction,
    SignalSide,
    TakeProfit,
)

from .errors import (
    DuplicateOrderConflict,
    LiveTradingDisabled,
    OrderAlreadyFailed,
    OrderInFlight,
    OrderRejected,
    OrderSizeTooSmall,
    ProtectionUnavailable,
    RiskRejected,
    StartupRecoveryRequired,
    SubmissionUnknown,
    UnknownOrder,
)
from .ports import ExchangeGateway, ProtectionRequest
from .state import TERMINAL_STATES, transition_order_state
from .store import ExecutionStore, InMemoryExecutionStore, StoredOrder


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    """执行侧安全参数。

    生产配置最终由 M01 的配置模型提供；这里保留 M09 需要的最小显式边界，避免读取全局配置。
    """

    mode: Literal["demo", "live"] = "demo"
    require_recovery: bool = True
    live_trading_enabled: bool = False
    max_market_fallback_slippage: Decimal = Decimal("0.005")

    def __post_init__(self) -> None:
        if self.max_market_fallback_slippage < 0:
            raise ValueError("max_market_fallback_slippage must not be negative")
        if self.mode == "live" and self.live_trading_enabled is False:
            return


@dataclass(frozen=True, slots=True)
class TimeoutOutcome:
    """限价单超时处理结果。"""

    canceled: bool
    fallback_submitted: bool
    reason: str


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """按交易所步长向下取整，避免超出可交易数量。"""

    if step <= 0:
        raise ValueError("step must be greater than zero")
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


def _opposite_side(side: SignalSide) -> SignalSide:
    return SignalSide.SHORT if side is SignalSide.LONG else SignalSide.LONG


class OMS:
    """统一处理信号、风控、订单和成交回报。

    OMS 不直接访问数据库或 OKX；交易所和存储均通过端口注入，便于模拟盘故障注入和回测复用。
    """

    def __init__(
        self,
        gateway: ExchangeGateway,
        *,
        store: ExecutionStore | None = None,
        config: ExecutionConfig | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.gateway = gateway
        self.store = store or InMemoryExecutionStore()
        self.config = config or ExecutionConfig()
        self._clock_ms = clock_ms or (lambda: 0)
        self._recovered = not self.config.require_recovery
        self._protection_fault = False
        self._submit_lock = asyncio.Lock()

    @property
    def can_open(self) -> bool:
        """当前是否允许新的开仓。"""

        return self._recovered and not self._protection_fault

    def mark_recovered(self) -> None:
        """在安全对账完成后允许恢复开仓。"""

        self._recovered = True

    def clear_protection_fault(self) -> None:
        """保护单恢复并人工确认后解除开仓阻断。"""

        self._protection_fault = False

    def build_order_request(
        self,
        signal: Signal,
        decision: RiskDecision,
        instrument: Instrument,
    ) -> OrderRequest:
        """把策略信号和风控结果转换为交易所订单请求。"""

        if decision.signal_id != signal.signal_id:
            raise RiskRejected("风控结果与策略信号不匹配")
        if not decision.approved:
            raise RiskRejected(f"信号未通过风控：{decision.rule} - {decision.detail}")
        if decision.qty <= 0:
            raise RiskRejected("风控批准数量必须大于零")

        size = _floor_to_step(decision.qty, instrument.lot_sz)
        if size < instrument.min_sz:
            raise OrderSizeTooSmall(
                f"rounded size {size} is below min size {instrument.min_sz} "
                f"for {instrument.inst_id}"
            )

        reduce_only = signal.action is not SignalAction.OPEN
        position_side = PositionSide.LONG if signal.side is SignalSide.LONG else PositionSide.SHORT
        order_side = signal.side if not reduce_only else _opposite_side(signal.side)
        if signal.action is SignalAction.MOVE_STOP and signal.stop_loss is None:
            raise RiskRejected("移动止损信号必须包含新的 stop_loss")

        price = (
            None
            if signal.entry_type is OrderType.MARKET
            else _floor_to_step(signal.price, instrument.tick_sz)
        )
        stop_loss = signal.stop_loss
        if stop_loss is not None:
            stop_loss = _floor_to_step(stop_loss, instrument.tick_sz)
        take_profits = [
            TakeProfit(
                price=_floor_to_step(take_profit.price, instrument.tick_sz),
                fraction=take_profit.fraction,
            )
            for take_profit in signal.take_profits
        ]

        return OrderRequest(
            cl_ord_id=signal.signal_id,
            inst_id=signal.inst_id,
            side=order_side,
            pos_side=position_side,
            order_type=signal.entry_type,
            price=price,
            size=size,
            stop_loss=stop_loss if signal.action is not SignalAction.CLOSE else None,
            take_profits=take_profits if signal.action is SignalAction.OPEN else [],
            reduce_only=reduce_only,
            mode=self.config.mode,
        )

    async def submit(self, request: OrderRequest) -> OrderAck:
        """提交订单并执行 clOrdId 幂等检查。"""

        self._ensure_submission_allowed(request)
        async with self._submit_lock:
            existing = self.store.get_order(request.cl_ord_id)
            if existing is not None:
                return self._handle_existing_submission(existing, request)

            pending = StoredOrder(request=request, state=OrderState.PENDING)
            self.store.save_order(pending)
            try:
                ack = await self.gateway.place_order(request)
            except TimeoutError as exc:
                raise SubmissionUnknown(
                    f"order submission timed out; reconcile before retrying: {request.cl_ord_id}"
                ) from exc
            except Exception:
                self.store.update_order(
                    StoredOrder(request=request, state=OrderState.FAILED, message="交易所调用失败")
                )
                raise

            if ack.cl_ord_id != request.cl_ord_id:
                self.store.update_order(
                    StoredOrder(
                        request=request,
                        state=OrderState.FAILED,
                        message="交易所返回 clOrdId 不一致",
                    )
                )
                raise SubmissionUnknown("exchange returned a different clOrdId")
            if ack.state is OrderState.REJECTED:
                self.store.update_order(
                    StoredOrder(
                        request=request,
                        state=OrderState.REJECTED,
                        ord_id=ack.ord_id,
                        accepted_ts_ms=ack.accepted_ts_ms,
                        message=ack.message,
                    )
                )
                raise OrderRejected(ack.message or request.cl_ord_id)

            target_state = transition_order_state(OrderState.PENDING, ack.state)
            self.store.update_order(
                StoredOrder(
                    request=request,
                    state=target_state,
                    ord_id=ack.ord_id,
                    accepted_ts_ms=ack.accepted_ts_ms,
                    message=ack.message,
                )
            )
            return ack

    def _ensure_submission_allowed(self, request: OrderRequest) -> None:
        if request.mode != self.config.mode:
            raise LiveTradingDisabled("订单 mode 与执行实例配置不一致")
        if request.mode == "live" and not self.config.live_trading_enabled:
            raise LiveTradingDisabled("默认只允许模拟盘，未显式解锁实盘")
        if not request.reduce_only and not self._recovered:
            raise StartupRecoveryRequired("启动后必须先完成交易所对账恢复")
        if not request.reduce_only and self._protection_fault:
            raise ProtectionUnavailable("保护单故障未解除，禁止新的开仓")

    def _handle_existing_submission(self, existing: StoredOrder, request: OrderRequest) -> OrderAck:
        if existing.request != request:
            raise DuplicateOrderConflict(f"same clOrdId has different request: {request.cl_ord_id}")
        if existing.state in {OrderState.PENDING, OrderState.SUBMITTED, OrderState.PARTIAL}:
            if existing.ord_id is None:
                raise OrderInFlight(f"order has no final acknowledgement: {request.cl_ord_id}")
            return OrderAck(
                cl_ord_id=request.cl_ord_id,
                ord_id=existing.ord_id,
                state=existing.state,
                accepted_ts_ms=existing.accepted_ts_ms or self._clock_ms(),
                message=existing.message,
            )
        if existing.state is OrderState.FAILED:
            raise OrderAlreadyFailed(request.cl_ord_id)
        return OrderAck(
            cl_ord_id=request.cl_ord_id,
            ord_id=existing.ord_id or request.cl_ord_id,
            state=existing.state,
            accepted_ts_ms=existing.accepted_ts_ms or self._clock_ms(),
            message=existing.message,
        )

    async def cancel(self, cl_ord_id: str) -> None:
        """撤销订单；重复撤单到已取消状态是幂等的。"""

        existing = self.store.get_order(cl_ord_id)
        if existing is None:
            raise UnknownOrder(cl_ord_id)
        if existing.state is OrderState.CANCELED:
            return
        if existing.state in TERMINAL_STATES:
            return
        await self.gateway.cancel_order(existing.request.inst_id, cl_ord_id)
        self.store.update_order(
            StoredOrder(
                request=existing.request,
                state=transition_order_state(existing.state, OrderState.CANCELED),
                ord_id=existing.ord_id,
                accepted_ts_ms=existing.accepted_ts_ms,
                message="已撤单",
                filled_size=existing.filled_size,
                protected_size=existing.protected_size,
            )
        )

    async def handle_fill(self, fill: Fill) -> bool:
        """处理成交回报，并在部分成交后立即建立增量保护单。"""

        existing = self.store.get_order(fill.cl_ord_id)
        if existing is None:
            raise UnknownOrder(fill.cl_ord_id)
        if existing.state in TERMINAL_STATES and existing.state is not OrderState.FILLED:
            raise OrderRejected(f"terminal order cannot receive fill: {fill.cl_ord_id}")

        accepted = self.store.record_fill(fill)
        if not accepted:
            return False
        current = self.store.get_order(fill.cl_ord_id)
        if current is None:
            raise UnknownOrder(fill.cl_ord_id)
        target = (
            OrderState.FILLED if current.filled_size >= current.request.size else OrderState.PARTIAL
        )
        next_state = transition_order_state(current.state, target)

        if not current.request.reduce_only:
            if current.request.stop_loss is None:
                self._protection_fault = True
                raise ProtectionUnavailable("开仓成交缺少 stop_loss，无法建立保护单")
            incremental_size = current.filled_size - current.protected_size
            if incremental_size > 0:
                protection = ProtectionRequest(
                    source_cl_ord_id=current.request.cl_ord_id,
                    inst_id=current.request.inst_id,
                    pos_side=current.request.pos_side,
                    size=incremental_size,
                    stop_loss=current.request.stop_loss,
                    take_profits=tuple(current.request.take_profits),
                    mode=current.request.mode,
                )
                try:
                    await self.gateway.place_protection_order(protection)
                except Exception as exc:
                    self._protection_fault = True
                    self.store.update_order(
                        StoredOrder(
                            request=current.request,
                            state=next_state,
                            ord_id=current.ord_id,
                            accepted_ts_ms=current.accepted_ts_ms,
                            message="保护单创建失败",
                            filled_size=current.filled_size,
                            protected_size=current.protected_size,
                        )
                    )
                    raise ProtectionUnavailable("成交后保护单创建失败") from exc
                current = StoredOrder(
                    request=current.request,
                    state=next_state,
                    ord_id=current.ord_id,
                    accepted_ts_ms=current.accepted_ts_ms,
                    message=current.message,
                    filled_size=current.filled_size,
                    protected_size=current.filled_size,
                )
            else:
                current = StoredOrder(
                    request=current.request,
                    state=next_state,
                    ord_id=current.ord_id,
                    accepted_ts_ms=current.accepted_ts_ms,
                    message=current.message,
                    filled_size=current.filled_size,
                    protected_size=current.protected_size,
                )
        else:
            current = StoredOrder(
                request=current.request,
                state=next_state,
                ord_id=current.ord_id,
                accepted_ts_ms=current.accepted_ts_ms,
                message=current.message,
                filled_size=current.filled_size,
                protected_size=current.protected_size,
            )
        self.store.update_order(current)
        return True

    async def handle_timeout(self, cl_ord_id: str, *, current_price: Decimal) -> TimeoutOutcome:
        """限价单超时后撤单，并按滑点上限决定是否用同一 clOrdId 改市价补单。"""

        existing = self.store.get_order(cl_ord_id)
        if existing is None:
            raise UnknownOrder(cl_ord_id)
        if existing.state not in {OrderState.SUBMITTED, OrderState.PARTIAL}:
            return TimeoutOutcome(False, False, f"订单状态为 {existing.state.value}，无需超时处理")
        if existing.state is OrderState.PARTIAL:
            return TimeoutOutcome(False, False, "部分成交订单不自动追价，先保持保护状态")
        if existing.request.order_type is not OrderType.LIMIT or existing.request.price is None:
            return TimeoutOutcome(False, False, "非限价单不执行限价超时补单")

        await self.cancel(cl_ord_id)
        deviation = abs(current_price - existing.request.price) / existing.request.price
        if deviation > self.config.max_market_fallback_slippage:
            return TimeoutOutcome(True, False, "当前价格偏离超过市价补单上限")

        fallback = OrderRequest(
            cl_ord_id=existing.request.cl_ord_id,
            inst_id=existing.request.inst_id,
            side=existing.request.side,
            pos_side=existing.request.pos_side,
            order_type=OrderType.MARKET,
            price=None,
            size=existing.request.size,
            stop_loss=existing.request.stop_loss,
            take_profits=existing.request.take_profits,
            reduce_only=existing.request.reduce_only,
            mode=existing.request.mode,
        )
        self.store.delete_order(cl_ord_id)
        await self.submit(fallback)
        return TimeoutOutcome(True, True, "已在滑点上限内改市价补单")

    async def configure_account(self, *, inst_id: str, leverage: int, margin_mode: str) -> None:
        """启动时设置逐仓和双向持仓；仍由适配器负责真正的 OKX 请求。"""

        await self.gateway.set_position_mode("long_short_mode")
        await self.gateway.set_leverage(inst_id, leverage, margin_mode)

    async def recover(self, reconciler):
        """先对账，只有安全结果才解除启动恢复锁。"""

        report = await reconciler.run()
        if report.safe_to_resume:
            self.mark_recovered()
        return report
