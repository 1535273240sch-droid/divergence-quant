"""M09 对账、启动恢复、确认令牌和持仓保护集成测试。"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from dquant.contracts import (
    DivergenceKind,
    DivergenceMethod,
    DivergenceSignal,
    OrderAck,
    OrderRequest,
    OrderState,
    OrderType,
    Position,
    PositionSide,
    SignalSide,
)
from dquant.execution import (
    OMS,
    ConfirmationAlreadyUsed,
    ConfirmationService,
    ExecutionConfig,
    InMemoryExecutionStore,
    SlippageExceeded,
    StartupRecoveryRequired,
    TokenExpired,
    build_position_actions,
)
from dquant.execution.ports import BalanceSnapshot, ProtectionRequest, RemoteOrder
from dquant.execution.reconcile import Reconciler


def run(coro):
    return asyncio.run(coro)


class FakeReconcileGateway:
    def __init__(self) -> None:
        self.open_orders: list[RemoteOrder] = []
        self.order_history: dict[str, RemoteOrder] = {}
        self.positions: list[Position] = []
        self.balance = BalanceSnapshot(
            equity=Decimal("1000"), available=Decimal("900"), upl=Decimal("0")
        )
        self.place_calls: list[OrderRequest] = []

    async def place_order(self, request: OrderRequest) -> OrderAck:
        self.place_calls.append(request)
        return OrderAck(
            cl_ord_id=request.cl_ord_id,
            ord_id=f"remote-{len(self.place_calls)}",
            state=OrderState.SUBMITTED,
            accepted_ts_ms=100,
            message="ok",
        )

    async def cancel_order(self, inst_id: str, cl_ord_id: str) -> None:
        del inst_id, cl_ord_id

    async def place_protection_order(self, request: ProtectionRequest) -> str:
        del request
        return "protection-1"

    async def fetch_open_orders(self) -> list[RemoteOrder]:
        return list(self.open_orders)

    async def fetch_order(self, cl_ord_id: str) -> RemoteOrder | None:
        return self.order_history.get(cl_ord_id)

    async def fetch_positions(self) -> list[Position]:
        return list(self.positions)

    async def fetch_balance(self) -> BalanceSnapshot:
        return self.balance


def make_request(cl_ord_id: str = "S117000000000001") -> OrderRequest:
    return OrderRequest(
        cl_ord_id=cl_ord_id,
        inst_id="BTC-USDT-SWAP",
        side=SignalSide.LONG,
        pos_side=PositionSide.LONG,
        order_type=OrderType.MARKET,
        size=Decimal("1"),
        stop_loss=Decimal("95"),
        mode="demo",
    )


def make_position(*, side: PositionSide = PositionSide.LONG, upl: str = "10") -> Position:
    return Position(
        inst_id="BTC-USDT-SWAP",
        pos_side=side,
        size=Decimal("1"),
        avg_price=Decimal("100"),
        mark_price=Decimal("110" if side is PositionSide.LONG else "90"),
        upl=Decimal(upl),
        stop_loss=Decimal("94" if side is PositionSide.LONG else "106"),
        strategy="S1",
        opened_ts_ms=1_000,
    )


def make_divergence(kind: DivergenceKind) -> DivergenceSignal:
    return DivergenceSignal(
        inst_id="BTC-USDT-SWAP",
        bar="4H",
        kind=kind,
        method=DivergenceMethod.MACD_SEGMENT,
        seg1_start_ts=1,
        seg1_end_ts=2,
        seg1_extreme_ts=2,
        seg1_extreme_price=Decimal("100"),
        seg1_peak=10,
        seg1_area=20,
        seg1_avg_vol=100,
        seg2_start_ts=3,
        seg2_end_ts=4,
        seg2_extreme_ts=4,
        seg2_extreme_price=Decimal("99"),
        seg2_peak=5,
        seg2_area=8,
        seg2_avg_vol=60,
        height_div=True,
        area_div=True,
        volume_confirm=True,
        close_confirm=True,
        divergence_order=1,
        confirmed_ts=4,
        score=80,
    )


def test_reconcile_adopts_remote_order_and_replaces_positions() -> None:
    gateway = FakeReconcileGateway()
    store = InMemoryExecutionStore()
    local_request = make_request()
    store.add_submitted_order(local_request, ord_id="local-1", accepted_ts_ms=1)
    remote_request = make_request("S117000000000002")
    gateway.open_orders = [
        RemoteOrder(request=remote_request, ord_id="remote-2", state=OrderState.SUBMITTED)
    ]
    gateway.order_history[local_request.cl_ord_id] = RemoteOrder(
        request=local_request,
        ord_id="local-1",
        state=OrderState.FILLED,
    )
    gateway.positions = [make_position()]

    report = run(Reconciler(gateway, store).run())

    assert report.safe_to_resume is True
    assert report.adopted_remote_orders == (remote_request.cl_ord_id,)
    assert report.state_changes[local_request.cl_ord_id] == (
        OrderState.SUBMITTED,
        OrderState.FILLED,
    )
    assert store.get_order(local_request.cl_ord_id).state is OrderState.FILLED
    assert store.get_order(remote_request.cl_ord_id).ord_id == "remote-2"
    assert store.list_positions() == [gateway.positions[0]]
    assert store.get_balance() == gateway.balance


def test_startup_recovery_blocks_new_open_until_reconcile_is_safe() -> None:
    gateway = FakeReconcileGateway()
    store = InMemoryExecutionStore()
    oms = OMS(gateway, store=store, config=ExecutionConfig(mode="demo"))
    request = make_request()

    with pytest.raises(StartupRecoveryRequired, match="对账"):
        run(oms.submit(request))

    gateway.open_orders = []
    report = run(oms.recover(Reconciler(gateway, store)))
    assert report.safe_to_resume is True
    run(oms.submit(request))
    assert len(gateway.place_calls) == 1


def test_unresolved_missing_remote_order_keeps_opening_disabled() -> None:
    gateway = FakeReconcileGateway()
    store = InMemoryExecutionStore()
    request = make_request()
    store.add_submitted_order(request, ord_id="local-1", accepted_ts_ms=1)
    report = run(Reconciler(gateway, store).run())

    assert report.safe_to_resume is False
    assert report.unresolved_orders == (request.cl_ord_id,)


def test_confirmation_token_is_single_use_and_checks_expiry_and_price() -> None:
    gateway = FakeReconcileGateway()
    oms = OMS(
        gateway,
        store=InMemoryExecutionStore(),
        config=ExecutionConfig(mode="live", require_recovery=False, live_trading_enabled=True),
    )
    request = make_request().model_copy(update={"mode": "live"})
    service = ConfirmationService(
        secret="test-only-secret", ttl_sec=900, max_slippage=Decimal("0.01")
    )
    signal = make_request_signal()
    ticket = service.create(signal, request, now_ts_ms=1_000)

    ack = run(
        service.confirm(
            ticket.token, current_price=Decimal("100.5"), now_ts_ms=2_000, submit=oms.submit
        )
    )

    assert ack.state is OrderState.SUBMITTED
    with pytest.raises(ConfirmationAlreadyUsed):
        run(
            service.confirm(
                ticket.token, current_price=Decimal("100.5"), now_ts_ms=2_001, submit=oms.submit
            )
        )

    expired_ticket = service.create(
        make_request_signal(signal_id="signal-expired"),
        request.model_copy(update={"cl_ord_id": "S117000000000003"}),
        now_ts_ms=10_000,
    )
    with pytest.raises(TokenExpired):
        run(
            service.confirm(
                expired_ticket.token,
                current_price=Decimal("100"),
                now_ts_ms=910_001,
                submit=oms.submit,
            )
        )


def make_request_signal(*, signal_id: str = "signal-live"):
    from dquant.contracts import Signal

    return Signal(
        signal_id=signal_id,
        inst_id="BTC-USDT-SWAP",
        side=SignalSide.LONG,
        action="open",
        entry_type=OrderType.MARKET,
        price=Decimal("100"),
        stop_loss=Decimal("95"),
        reason="等待实盘确认",
        strategy_name="S1",
        created_ts_ms=1_000,
    )


def test_confirmation_rejects_price_slippage() -> None:
    service = ConfirmationService(
        secret="test-only-secret", ttl_sec=900, max_slippage=Decimal("0.01")
    )
    ticket = service.create(
        make_request_signal(signal_id="signal-slippage"),
        make_request("S117000000000004").model_copy(update={"mode": "live"}),
        now_ts_ms=1_000,
    )

    with pytest.raises(Exception) as error:
        run(
            service.confirm(
                ticket.token,
                current_price=Decimal("102"),
                now_ts_ms=2_000,
                submit=lambda request: asyncio.sleep(
                    0,
                    result=OrderAck(
                        cl_ord_id=request.cl_ord_id,
                        ord_id="unused",
                        state=OrderState.SUBMITTED,
                        accepted_ts_ms=2_000,
                    ),
                ),
            )
        )
    assert isinstance(error.value, SlippageExceeded)


def test_position_management_exits_on_reverse_divergence_and_moves_profitable_stop() -> None:
    long_actions = build_position_actions(
        make_position(),
        reverse_divergence=make_divergence(DivergenceKind.BEAR_REGULAR),
        daily_close=Decimal("90"),
        daily_ma200=Decimal("100"),
        mode="demo",
    )
    assert len(long_actions) == 1
    assert long_actions[0].kind == "REVERSE_DIVERGENCE_EXIT"
    assert long_actions[0].order_request.side is SignalSide.SHORT
    assert long_actions[0].order_request.reduce_only is True

    stop_actions = build_position_actions(
        make_position(),
        reverse_divergence=None,
        daily_close=Decimal("90"),
        daily_ma200=Decimal("100"),
        mode="demo",
    )
    assert len(stop_actions) == 1
    assert stop_actions[0].kind == "MOVE_STOP_TO_BREAKEVEN"
    assert stop_actions[0].new_stop_loss == Decimal("99.8800")
    assert stop_actions[0].order_request.stop_loss == Decimal("99.8800")
