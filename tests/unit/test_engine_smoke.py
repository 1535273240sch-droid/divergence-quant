"""引擎整合冒烟测试: 保护 dquant.scheduler.engine 全链路接线不被回归破坏。"""

from __future__ import annotations

import importlib

import pytest


def test_compose_entry_modules_importable() -> None:
    """docker-compose/Dockerfile 引用的 4 个入口模块必须真实存在 (整合回归护栏)。"""
    for module_name in (
        "dquant.scheduler.engine",
        "dquant.data.collector",
        "dquant.api.main",
        "dquant.backtest.run",
    ):
        spec = importlib.util.find_spec(module_name)
        assert spec is not None, f"{module_name} 不存在, docker-compose 将无法启动"


def test_api_main_exposes_uvicorn_app() -> None:
    from dquant.api.main import app
    from dquant.api.app import app as orig

    assert app is orig


def test_collector_has_cli_main() -> None:
    from dquant.data.collector import main

    assert callable(main)


def test_engine_builds_order_request_with_okx_safe_cl_ord_id() -> None:
    """订单准备阶段必须重写 clOrdId (signal_id 含冒号且超 32 字符, OKX 会拒单)。"""
    import dataclasses
    from decimal import Decimal

    from dquant.contracts import Instrument, Signal
    from dquant.contracts.types import (
        CandleBar,
        InstrumentState,
        InstrumentType,
        OrderType,
        SignalAction,
        SignalSide,
    )
    from dquant.execution.oms import ExecutionConfig, OMS
    from dquant.execution.state import build_client_order_id
    from dquant.risk.manager import RiskDecision
    from dquant.scheduler.engine import NullGateway

    instrument = Instrument(
        inst_id="BTC-USDT-SWAP",
        inst_type=InstrumentType.SWAP,
        base="BTC",
        quote="USDT",
        state=InstrumentState.LIVE,
        ct_val=Decimal("0.01"),
        lot_sz=Decimal("1"),
        min_sz=Decimal("1"),
        tick_sz=Decimal("0.0001"),
    )
    signal = Signal(
        signal_id="S1:1.0.0:BTC-USDT-SWAP:long:1750000000000",
        inst_id="BTC-USDT-SWAP",
        side=SignalSide.LONG,
        action=SignalAction.OPEN,
        entry_type=OrderType.LIMIT,
        price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profits=[],
        reason="smoke",
        strategy_name="S1",
        created_ts_ms=1750000000000,
    )
    decision = RiskDecision(
        signal_id=signal.signal_id,
        approved=True,
        rule="APPROVED",
        detail="test",
        qty=Decimal("2"),
        risk_fraction=Decimal("0.01"),
    )
    oms = OMS(NullGateway(), config=ExecutionConfig(mode="demo"))  # type: ignore[arg-type]
    order = oms.build_order_request(signal, decision, instrument)
    fixed = order.model_copy(update={"cl_ord_id": build_client_order_id("S1", signal.created_ts_ms, 1)})
    assert len(fixed.cl_ord_id) <= 32
    assert ":" not in fixed.cl_ord_id
    assert fixed.cl_ord_id != signal.signal_id


@pytest.mark.asyncio
async def test_engine_full_chain_synthetic(tmp_path) -> None:  # noqa: ANN001
    """synthetic 数据一轮全链路: 采集→币池→趋势→背离→选币(持久化)→策略上下文。"""
    from dquant.scheduler.engine import EngineOptions, TradingEngine
    from dquant.scheduler.persistence import ScreenerRunStore
    from dquant.scheduler.engine import _SessionFactoryShim

    options = EngineOptions(
        source="synthetic",
        once=True,
        db_path=str(tmp_path / "engine.db"),
        top_n=8,
        seed=7,
    )
    engine = TradingEngine(options)
    await engine.run()

    store = ScreenerRunStore(_SessionFactoryShim(engine._session_factory))  # type: ignore[arg-type]
    record = await store.load_latest_run()
    assert record is not None, "选币结果未持久化"
    assert record.universe_size == 8
    assert len(record.candidates) == 8
    pools = {c.pool for c in record.candidates}
    # 8 个币种、有涨有跌的合成数据应覆盖多个候选池, 且全部在 A~F 合法范围
    assert pools <= set("ABCDEF")
    assert len(pools) >= 3, f"候选池分布异常: {pools}"

    from dquant.contracts.types import CandleBar
    from dquant.data.store import count_candles
    with engine._session_factory() as session:
        total = sum(
            count_candles(session, inst_id, bar)
            for inst_id in engine.instruments
            for bar in (CandleBar.ONE_DAY, CandleBar.FOUR_HOUR, CandleBar.ONE_HOUR)
        )
    assert total >= 8 * (260 + 320 + 420) * 0.99
