"""M08 风控单元测试。

测试只使用公共契约模型和模拟上下文，不连接交易所、不读取真实密钥。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from dquant.contracts import (
    AIAction,
    AIReview,
    Instrument,
    Position,
    PositionSide,
    RegimeState,
    Signal,
    SignalAction,
    SignalSide,
)
from dquant.risk import (
    KillSwitch,
    MarketRiskContext,
    PositionSizing,
    RiskConfig,
    RiskInputError,
    RiskManager,
    RiskRequest,
    calculate_position_size,
    max_drawdown,
)

INST = "ETH-USDT-SWAP"
BTC = "BTC-USDT-SWAP"


def instrument(
    inst_id: str = INST, *, ct_val: str = "1", lot_sz: str = "1", min_sz: str = "1"
) -> Instrument:
    return Instrument(
        inst_id=inst_id,
        base=inst_id.split("-")[0],
        quote="USDT",
        ct_val=Decimal(ct_val),
        lot_sz=Decimal(lot_sz),
        min_sz=Decimal(min_sz),
        tick_sz=Decimal("0.1"),
    )


def signal(
    *,
    side: SignalSide = SignalSide.LONG,
    inst_id: str = INST,
    price: str = "100",
    stop_loss: str = "95",
    action: SignalAction = SignalAction.OPEN,
) -> Signal:
    return Signal(
        signal_id=f"sig-{inst_id}-{side.value}-{action.value}",
        inst_id=inst_id,
        side=side,
        action=action,
        entry_type="market",
        price=Decimal(price),
        stop_loss=Decimal(stop_loss) if action is SignalAction.OPEN else None,
        reason="测试信号",
        strategy_name="test",
        created_ts_ms=1_800_000_000_000,
    )


def position(
    *,
    inst_id: str = INST,
    side: PositionSide = PositionSide.LONG,
    size: str = "1",
    mark_price: str = "100",
) -> Position:
    return Position(
        inst_id=inst_id,
        pos_side=side,
        size=Decimal(size),
        avg_price=Decimal("100"),
        mark_price=Decimal(mark_price),
        upl=Decimal("0"),
        strategy="test",
        opened_ts_ms=1_799_999_000_000,
    )


def request(
    sig: Signal | None = None,
    *,
    equity: str = "1000",
    instrument_: Instrument | None = None,
    positions: tuple[Position, ...] = (),
    daily_pnl: str = "0",
    weekly_pnl: str = "0",
    peak_equity: str | None = None,
    market: MarketRiskContext | None = None,
    pool_factor: str = "1",
) -> RiskRequest:
    return RiskRequest(
        signal=sig or signal(),
        equity=Decimal(equity),
        instrument=instrument_ or instrument(),
        positions=positions,
        daily_pnl=Decimal(daily_pnl),
        weekly_pnl=Decimal(weekly_pnl),
        peak_equity=None if peak_equity is None else Decimal(peak_equity),
        market=market or MarketRiskContext(),
        pool_factor=Decimal(pool_factor),
    )


def test_position_sizing_uses_decimal_contract_value_and_single_tp_fallback() -> None:
    sizing = calculate_position_size(
        equity=Decimal("1000"),
        risk_fraction=Decimal("0.0075"),
        entry_price=Decimal("100"),
        stop_loss_price=Decimal("95"),
        instrument=instrument(ct_val="10", lot_sz="0.1", min_sz="0.1"),
    )

    assert isinstance(sizing, PositionSizing)
    assert sizing.qty == Decimal("0.1")
    assert sizing.single_tp_mode is True
    assert sizing.rejected is False


def test_position_sizing_rejects_when_one_minimum_lot_is_not_affordable() -> None:
    sizing = calculate_position_size(
        equity=Decimal("10"),
        risk_fraction=Decimal("0.01"),
        entry_price=Decimal("100"),
        stop_loss_price=Decimal("90"),
        instrument=instrument(ct_val="10", lot_sz="1", min_sz="1"),
    )

    assert sizing.qty == Decimal("0")
    assert sizing.rejected is True
    assert sizing.single_tp_mode is False


def test_max_drawdown_rejects_empty_or_non_positive_curves() -> None:
    with pytest.raises(RiskInputError):
        max_drawdown([])
    with pytest.raises(RiskInputError):
        max_drawdown([Decimal("100"), Decimal("0")])
    assert max_drawdown([Decimal("100"), Decimal("120"), Decimal("90")]) == Decimal("0.25")


def test_small_and_standard_profiles_have_different_default_risk() -> None:
    assert RiskConfig(profile="small_account").risk_per_trade == Decimal("0.015")
    assert RiskConfig(profile="standard").risk_per_trade == Decimal("0.01")


def test_manager_approves_normal_open_and_preserves_public_risk_decision() -> None:
    manager = RiskManager()
    decision = manager.evaluate(request())

    assert decision.approved is True
    assert decision.signal_id == "sig-ETH-USDT-SWAP-long-open"
    assert decision.qty > 0
    assert decision.risk_fraction == Decimal("0.015")


def test_manager_rejects_daily_weekly_and_drawdown_limits_and_latches_breaker() -> None:
    manager = RiskManager()

    daily = manager.evaluate(request(daily_pnl="-30"))
    assert daily.approved is False
    assert daily.rule == "DAILY_LOSS_LIMIT"
    assert manager.kill_switch.state.opening_enabled is False

    manager.resume_opening()
    weekly = manager.evaluate(request(weekly_pnl="-60"))
    assert weekly.approved is False
    assert weekly.rule == "WEEKLY_LOSS_LIMIT"

    manager.resume_opening()
    drawdown = manager.evaluate(request(peak_equity="1200"))
    assert drawdown.approved is False
    assert drawdown.rule == "MAX_DRAWDOWN"
    assert manager.kill_switch.state.circuit_breaker_active is True
    assert manager.kill_switch.state.close_all_requested is True


def test_manager_applies_btc_filter_weekend_and_funding_rules() -> None:
    saturday_ts = int(datetime(2026, 10, 24, 12, tzinfo=UTC).timestamp() * 1000)
    market = MarketRiskContext(
        btc_regime=RegimeState.DOWNTREND,
        funding_rate=Decimal("0.002"),
    )
    manager = RiskManager()
    weekend = manager.evaluate(request(market=market, sig=signal(), equity="10000"))
    assert weekend.approved is False
    assert weekend.rule == "FUNDING_FILTER"

    market = MarketRiskContext(btc_regime=RegimeState.DOWNTREND)
    weekend = manager.evaluate(
        RiskRequest(
            signal=signal(),
            equity=Decimal("10000"),
            instrument=instrument(),
            ts_ms=saturday_ts,
            market=market,
        )
    )
    assert weekend.approved is True
    assert weekend.qty == Decimal("10")
    assert "weekend_factor=0.7" in weekend.detail


def test_manager_blocks_btc_spike_for_two_hours_and_is_idempotent() -> None:
    ts_ms = 1_800_000_000_000
    manager = RiskManager()
    context = MarketRiskContext(btc_one_hour_return=Decimal("-0.05"), btc_move_ts_ms=ts_ms)
    first = manager.evaluate(
        RiskRequest(
            signal=signal(),
            equity=Decimal("1000"),
            instrument=instrument(),
            ts_ms=ts_ms,
            market=context,
        )
    )
    second = manager.evaluate(
        RiskRequest(
            signal=signal(),
            equity=Decimal("1000"),
            instrument=instrument(),
            ts_ms=ts_ms,
            market=context,
        )
    )
    assert first.approved is False
    assert first.rule == "BTC_SPIKE_LONG_BLOCK"
    assert second == first


def test_manager_applies_ai_veto_and_half_size() -> None:
    veto = AIReview(
        review_id="review-1",
        inst_id=INST,
        action=AIAction.VETO,
        score_delta=0,
        comment_cn="重大利空",
        model_used="mock",
        cost_usd=Decimal("0"),
        created_ts_ms=1,
    )
    manager = RiskManager()
    rejected = manager.evaluate(request(market=MarketRiskContext(ai_review=veto)))
    assert rejected.approved is False
    assert rejected.rule == "AI_VETO"

    half = AIReview(
        review_id="review-2",
        inst_id=INST,
        action=AIAction.HALF_SIZE,
        score_delta=0,
        comment_cn="波动较大",
        model_used="mock",
        cost_usd=Decimal("0"),
        created_ts_ms=1,
    )
    approved = manager.evaluate(request(market=MarketRiskContext(ai_review=half), equity="10000"))
    assert approved.approved is True
    assert approved.qty == Decimal("15")


def test_manager_rejects_position_conflict_and_limits() -> None:
    manager = RiskManager(config=RiskConfig(max_positions=1, max_same_direction=1))
    existing = position()
    same_symbol = manager.evaluate(request(positions=(existing,)))
    assert same_symbol.approved is False
    assert same_symbol.rule == "SAME_SYMBOL_POSITION"

    full = RiskManager(config=RiskConfig(max_positions=1))
    other = position(inst_id="BTC-USDT-SWAP")
    limited = full.evaluate(request(positions=(other,)))
    assert limited.approved is False
    assert limited.rule == "MAX_POSITIONS"


def test_manager_allows_management_signals_even_when_opening_is_stopped() -> None:
    manager = RiskManager()
    manager.stop_opening("人工停止开仓")
    close = manager.evaluate(request(sig=signal(action=SignalAction.CLOSE)))
    assert close.approved is True
    assert close.qty == Decimal("0")
    assert close.rule == "MANAGEMENT_ALLOWED"


def test_kill_switch_separates_stop_opening_from_manual_breaker_release() -> None:
    switch = KillSwitch()
    first = switch.stop_opening("测试")
    second = switch.stop_opening("测试")
    assert first == second
    assert switch.can_open is False

    switch.trigger_circuit_breaker("回撤", close_all=True)
    switch.release_circuit_breaker()
    assert switch.state.circuit_breaker_active is False
    assert switch.can_open is False

    switch.resume_opening()
    assert switch.can_open is True
