"""M09 模拟盘执行、订单状态和确认流程集成测试。"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

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
from dquant.execution import (
    OMS,
    ExecutionConfig,
    InMemoryExecutionStore,
    LiveTradingDisabled,
    ProtectionUnavailable,
    RiskRejected,
    build_client_order_id,
)
from dquant.execution.ports import ProtectionRequest


def run(coro):
    """在不依赖 pytest-asyncio 的情况下运行异步执行接口。"""

    return asyncio.run(coro)


class FakeDemoGateway:
    """只用于本模块测试的交易所网关，不连接真实 OKX。"""

    def __init__(self) -> None:
        self.place_calls: list[OrderRequest] = []
        self.cancel_calls: list[tuple[str, str]] = []
        self.protection_calls: list[ProtectionRequest] = []
        self.fail_protection = False
        self._next_order_number = 1

    async def place_order(self, request: OrderRequest) -> OrderAck:
        self.place_calls.append(request)
        ord_id = f"demo-{self._next_order_number}"
        self._next_order_number += 1
        return OrderAck(
            cl_ord_id=request.cl_ord_id,
            ord_id=ord_id,
            state=OrderState.SUBMITTED,
            accepted_ts_ms=1_000,
            message="模拟盘已接收",
        )

    async def cancel_order(self, inst_id: str, cl_ord_id: str) -> None:
        self.cancel_calls.append((inst_id, cl_ord_id))

    async def place_protection_order(self, request: ProtectionRequest) -> str:
        if self.fail_protection:
            raise RuntimeError("模拟保护单创建失败")
        self.protection_calls.append(request)
        return f"protect-{len(self.protection_calls)}"


def make_instrument() -> Instrument:
    return Instrument(
        inst_id="BTC-USDT-SWAP",
        base="BTC",
        quote="USDT",
        ct_val=Decimal("1"),
        lot_sz=Decimal("0.5"),
        min_sz=Decimal("1"),
        tick_sz=Decimal("0.1"),
    )


def make_request(
    *,
    cl_ord_id: str = "S11234567890123",
    order_type: OrderType = OrderType.LIMIT,
    size: str = "2",
    reduce_only: bool = False,
) -> OrderRequest:
    return OrderRequest(
        cl_ord_id=cl_ord_id,
        inst_id="BTC-USDT-SWAP",
        side=SignalSide.LONG if not reduce_only else SignalSide.SHORT,
        pos_side=PositionSide.LONG,
        order_type=order_type,
        price=None if order_type is OrderType.MARKET else Decimal("100"),
        size=Decimal(size),
        stop_loss=None if reduce_only else Decimal("95"),
        take_profits=[]
        if reduce_only
        else [TakeProfit(price=Decimal("110"), fraction=Decimal("1"))],
        reduce_only=reduce_only,
        mode="demo",
    )


def make_signal(*, action: SignalAction = SignalAction.OPEN, price: str = "100") -> Signal:
    return Signal(
        signal_id="signal-1",
        inst_id="BTC-USDT-SWAP",
        side=SignalSide.LONG,
        action=action,
        entry_type=OrderType.LIMIT,
        price=Decimal(price),
        stop_loss=Decimal("95") if action is SignalAction.OPEN else None,
        take_profits=[TakeProfit(price=Decimal("110"), fraction=Decimal("1"))]
        if action is SignalAction.OPEN
        else [],
        reason="集成测试信号",
        strategy_name="S1",
        created_ts_ms=1_000,
    )


def test_demo_order_is_idempotent_and_partial_fill_is_protected() -> None:
    gateway = FakeDemoGateway()
    store = InMemoryExecutionStore()
    oms = OMS(
        gateway,
        store=store,
        config=ExecutionConfig(mode="demo", require_recovery=False),
    )
    request = make_request()

    first = run(oms.submit(request))
    second = run(oms.submit(request))

    assert first == second
    assert len(gateway.place_calls) == 1
    assert store.get_order(request.cl_ord_id).state is OrderState.SUBMITTED

    accepted = run(
        oms.handle_fill(
            Fill(
                trade_id="trade-1",
                cl_ord_id=request.cl_ord_id,
                price=Decimal("100"),
                size=Decimal("1"),
                fee=Decimal("0.01"),
                ts_ms=2_000,
            )
        )
    )
    duplicate = run(
        oms.handle_fill(
            Fill(
                trade_id="trade-1",
                cl_ord_id=request.cl_ord_id,
                price=Decimal("100"),
                size=Decimal("1"),
                fee=Decimal("0.01"),
                ts_ms=2_000,
            )
        )
    )

    assert accepted is True
    assert duplicate is False
    assert store.get_order(request.cl_ord_id).state is OrderState.PARTIAL
    assert store.get_order(request.cl_ord_id).protected_size == Decimal("1")
    assert len(gateway.protection_calls) == 1

    run(
        oms.handle_fill(
            Fill(
                trade_id="trade-2",
                cl_ord_id=request.cl_ord_id,
                price=Decimal("99.8"),
                size=Decimal("1"),
                fee=Decimal("0.01"),
                ts_ms=3_000,
            )
        )
    )

    assert store.get_order(request.cl_ord_id).state is OrderState.FILLED
    assert store.get_order(request.cl_ord_id).protected_size == Decimal("2")
    assert [call.size for call in gateway.protection_calls] == [Decimal("1"), Decimal("1")]


def test_protection_failure_blocks_new_open_but_does_not_hide_the_error() -> None:
    gateway = FakeDemoGateway()
    gateway.fail_protection = True
    oms = OMS(
        gateway,
        store=InMemoryExecutionStore(),
        config=ExecutionConfig(mode="demo", require_recovery=False),
    )
    request = make_request()
    run(oms.submit(request))

    with pytest.raises(ProtectionUnavailable, match="保护"):
        run(
            oms.handle_fill(
                Fill(
                    trade_id="trade-failed-protection",
                    cl_ord_id=request.cl_ord_id,
                    price=Decimal("100"),
                    size=Decimal("1"),
                    fee=Decimal("0"),
                    ts_ms=2_000,
                )
            )
        )

    with pytest.raises(ProtectionUnavailable, match="保护"):
        run(oms.submit(make_request(cl_ord_id="S11234567890124")))


def test_limit_timeout_only_uses_market_fallback_within_slippage_limit() -> None:
    gateway = FakeDemoGateway()
    oms = OMS(
        gateway,
        store=InMemoryExecutionStore(),
        config=ExecutionConfig(
            mode="demo",
            require_recovery=False,
            max_market_fallback_slippage=Decimal("0.005"),
        ),
    )
    request = make_request()
    run(oms.submit(request))

    outcome = run(oms.handle_timeout(request.cl_ord_id, current_price=Decimal("100.4")))

    assert outcome.fallback_submitted is True
    assert len(gateway.cancel_calls) == 1
    assert len(gateway.place_calls) == 2
    assert gateway.place_calls[-1].order_type is OrderType.MARKET
    assert gateway.place_calls[-1].cl_ord_id == request.cl_ord_id

    gateway2 = FakeDemoGateway()
    oms2 = OMS(
        gateway2,
        store=InMemoryExecutionStore(),
        config=ExecutionConfig(
            mode="demo",
            require_recovery=False,
            max_market_fallback_slippage=Decimal("0.005"),
        ),
    )
    request2 = make_request(cl_ord_id="S11234567890125")
    run(oms2.submit(request2))
    skipped = run(oms2.handle_timeout(request2.cl_ord_id, current_price=Decimal("101")))

    assert skipped.fallback_submitted is False
    assert "偏离" in skipped.reason
    assert len(gateway2.place_calls) == 1


def test_signal_and_risk_decision_are_converted_to_precision_safe_order_request() -> None:
    oms = OMS(
        FakeDemoGateway(),
        store=InMemoryExecutionStore(),
        config=ExecutionConfig(mode="demo", require_recovery=False),
    )
    decision = RiskDecision(
        signal_id="signal-1",
        approved=True,
        rule="TEST",
        detail="放行",
        qty=Decimal("2.49"),
        risk_fraction=Decimal("0.01"),
    )

    request = oms.build_order_request(make_signal(), decision, make_instrument())

    assert request.size == Decimal("2.0")
    assert request.price == Decimal("100.0")
    assert request.stop_loss == Decimal("95.0")
    assert request.mode == "demo"

    with pytest.raises(RiskRejected, match="未通过"):
        oms.build_order_request(
            make_signal(),
            decision.model_copy(update={"approved": False}),
            make_instrument(),
        )


def test_live_submission_is_disabled_by_default_and_client_order_id_is_deterministic() -> None:
    oms = OMS(
        FakeDemoGateway(),
        store=InMemoryExecutionStore(),
        config=ExecutionConfig(mode="live", require_recovery=False),
    )
    with pytest.raises(LiveTradingDisabled, match="模拟盘"):
        run(oms.submit(make_request().model_copy(update={"mode": "live"})))

    assert build_client_order_id("S1", 1_700_000_000_000, 3) == "S117000000000000003"
