from decimal import Decimal

import pytest

from dquant.contracts import RiskDecision, Signal, SignalAction, SignalSide
from dquant.runtime import StrategyRuntimeConfig
from dquant.scheduler.engine import EngineOptions, TradingEngine


@pytest.mark.asyncio
async def test_engine_paper_position_rebalances_after_runtime_change(tmp_path) -> None:
    engine = TradingEngine(
        EngineOptions(
            source="synthetic",
            db_path=str(tmp_path / "engine.db"),
            once=True,
        )
    )
    await engine.collect_once()
    engine._equity = Decimal("10000")

    signal = Signal(
        signal_id="MHD:e2e:BTC-USDT-SWAP:long:1",
        inst_id="BTC-USDT-SWAP",
        side=SignalSide.LONG,
        action=SignalAction.OPEN,
        entry_type="market",
        price=Decimal("100"),
        stop_loss=Decimal("90"),
        take_profits=[],
        reason="e2e paper open",
        strategy_name="MHD",
        created_ts_ms=1_000_000,
    )
    decision = RiskDecision(
        signal_id=signal.signal_id,
        approved=True,
        rule="APPROVED",
        detail="e2e",
        qty=Decimal("10"),
        risk_fraction=Decimal("0.5"),
    )
    assert engine._apply_paper_signal(signal, decision, None) == "FILLED"
    before = engine.paper_broker.list_positions()
    assert len(before) == 1

    runtime = StrategyRuntimeConfig(tmp_path / "strategy_runtime.json")
    runtime.update("MHD", {"position_pct": 0.6, "position_multiplier": 1.0})
    engine.runtime_config = runtime
    engine._runtime_revision = -1
    engine._reload_strategies_if_needed()
    engine._applied_sizing = None
    engine._apply_runtime_position_changes()

    after = engine.paper_broker.list_positions()
    assert len(after) == 1
    assert after[0].size != before[0].size
    assert after[0].strategy == "MHD"
