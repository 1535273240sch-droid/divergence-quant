"""风控计算基础函数。

本模块只做确定性的 Decimal 计算，不访问网络、不写数据库，也不改变全局状态。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from dquant.contracts import Instrument


class RiskInputError(ValueError):
    """风控输入不满足安全边界。"""


def _as_decimal(value: Decimal | int | float | str, field_name: str) -> Decimal:
    """把外部数值转换为有限 Decimal。"""

    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RiskInputError(f"{field_name} must be a valid decimal") from exc
    if not result.is_finite():
        raise RiskInputError(f"{field_name} must be finite")
    return result


def round_down_to_step(
    value: Decimal | int | float | str, step: Decimal | int | float | str
) -> Decimal:
    """按交易所步长向下取整，避免超出固定风险预算。"""

    amount = _as_decimal(value, "value")
    increment = _as_decimal(step, "step")
    if amount < 0:
        raise RiskInputError("value must not be negative")
    if increment <= 0:
        raise RiskInputError("step must be positive")
    units = (amount / increment).to_integral_value(rounding=ROUND_DOWN)
    return units * increment


def max_drawdown(equity_curve: Sequence[Decimal | int | float | str]) -> Decimal:
    """返回权益曲线的最大峰值到谷值回撤比例。"""

    if not equity_curve:
        raise RiskInputError("equity_curve must contain at least one value")

    values = [_as_decimal(value, "equity") for value in equity_curve]
    if any(value <= 0 for value in values):
        raise RiskInputError("equity values must be positive")

    peak = values[0]
    largest_drawdown = Decimal("0")
    for equity in values:
        peak = max(peak, equity)
        largest_drawdown = max(largest_drawdown, (peak - equity) / peak)
    return largest_drawdown


@dataclass(frozen=True, slots=True)
class PositionSizing:
    """仓位计算结果，包含小资金不拆单模式标记。"""

    qty: Decimal
    raw_qty: Decimal
    risk_amount: Decimal
    risk_per_contract: Decimal
    single_tp_mode: bool
    rejected: bool


def calculate_position_size(
    *,
    equity: Decimal | int | float | str,
    risk_fraction: Decimal | int | float | str,
    entry_price: Decimal | int | float | str,
    stop_loss_price: Decimal | int | float | str,
    instrument: Instrument,
    pool_factor: Decimal | int | float | str = Decimal("1"),
    market_factor: Decimal | int | float | str = Decimal("1"),
    weekend_factor: Decimal | int | float | str = Decimal("1"),
) -> PositionSizing:
    """按合约面值、风险比例和各级仓位系数计算下单张数。

    结果先按 ``lot_sz`` 向下取整，再检查 ``min_sz``。小资金账户若不足以拆成两半，
    返回 ``single_tp_mode=True``；若连一份最小下单量都不足，则返回 ``rejected=True``。
    """

    equity_decimal = _as_decimal(equity, "equity")
    risk_decimal = _as_decimal(risk_fraction, "risk_fraction")
    entry_decimal = _as_decimal(entry_price, "entry_price")
    stop_decimal = _as_decimal(stop_loss_price, "stop_loss_price")
    pool_decimal = _as_decimal(pool_factor, "pool_factor")
    market_decimal = _as_decimal(market_factor, "market_factor")
    weekend_decimal = _as_decimal(weekend_factor, "weekend_factor")

    if equity_decimal <= 0:
        raise RiskInputError("equity must be positive")
    if not 0 < risk_decimal <= 1:
        raise RiskInputError("risk_fraction must be greater than 0 and at most 1")
    if entry_decimal <= 0 or stop_decimal <= 0:
        raise RiskInputError("entry_price and stop_loss_price must be positive")
    if entry_decimal == stop_decimal:
        raise RiskInputError("entry_price and stop_loss_price must be different")
    if any(factor < 0 for factor in (pool_decimal, market_decimal, weekend_decimal)):
        raise RiskInputError("position sizing factors cannot be negative")

    if instrument.ct_val <= 0 or instrument.lot_sz <= 0 or instrument.min_sz <= 0:
        raise RiskInputError("instrument contract value and size steps must be positive")

    risk_amount = equity_decimal * risk_decimal * pool_decimal * market_decimal * weekend_decimal
    risk_per_contract = abs(entry_decimal - stop_decimal) * instrument.ct_val
    raw_qty = risk_amount / risk_per_contract
    qty = round_down_to_step(raw_qty, instrument.lot_sz)
    rejected = qty < instrument.min_sz
    single_tp_mode = not rejected and (qty < instrument.lot_sz * 2 or qty < instrument.min_sz * 2)
    return PositionSizing(
        qty=Decimal("0") if rejected else qty,
        raw_qty=raw_qty,
        risk_amount=risk_amount,
        risk_per_contract=risk_per_contract,
        single_tp_mode=single_tp_mode,
        rejected=rejected,
    )


def calculate_fixed_fraction_size(
    *,
    equity: Decimal | int | float | str,
    position_pct: Decimal | int | float | str,
    position_multiplier: Decimal | int | float | str,
    leverage: Decimal | int | float | str = Decimal("1"),
    entry_price: Decimal | int | float | str,
    instrument: Instrument,
) -> PositionSizing:
    """按账户权益固定比例计算合约张数（MHD 默认 50% 保证金仓位）。

    ``position_pct`` 是保证金占权益的比例，``position_multiplier`` 是前端可调的
    仓位倍数，``leverage`` 是合约杠杆；三者共同决定目标名义价值：

        notional = equity × position_pct × position_multiplier × leverage
    """

    equity_decimal = _as_decimal(equity, "equity")
    pct_decimal = _as_decimal(position_pct, "position_pct")
    multiplier_decimal = _as_decimal(position_multiplier, "position_multiplier")
    leverage_decimal = _as_decimal(leverage, "leverage")
    entry_decimal = _as_decimal(entry_price, "entry_price")

    if equity_decimal <= 0:
        raise RiskInputError("equity must be positive")
    if not 0 < pct_decimal <= 1:
        raise RiskInputError("position_pct must be greater than 0 and at most 1")
    if not 0 < multiplier_decimal <= 3:
        raise RiskInputError("position_multiplier must be greater than 0 and at most 3")
    if not 1 <= leverage_decimal <= 20:
        raise RiskInputError("leverage must be within [1, 20]")
    if entry_decimal <= 0:
        raise RiskInputError("entry_price must be positive")
    if instrument.ct_val <= 0 or instrument.lot_sz <= 0 or instrument.min_sz <= 0:
        raise RiskInputError("instrument contract value and size steps must be positive")

    notional = equity_decimal * pct_decimal * multiplier_decimal * leverage_decimal
    risk_per_contract = entry_decimal * instrument.ct_val
    raw_qty = notional / risk_per_contract
    qty = round_down_to_step(raw_qty, instrument.lot_sz)
    rejected = qty < instrument.min_sz
    single_tp_mode = not rejected and (qty < instrument.lot_sz * 2 or qty < instrument.min_sz * 2)
    return PositionSizing(
        qty=Decimal("0") if rejected else qty,
        raw_qty=raw_qty,
        risk_amount=notional,
        risk_per_contract=risk_per_contract,
        single_tp_mode=single_tp_mode,
        rejected=rejected,
    )


def position_size(
    equity: Decimal | int | float | str,
    risk_fraction: Decimal | int | float | str,
    entry_price: Decimal | int | float | str,
    stop_loss_price: Decimal | int | float | str,
    *,
    contract_value: Decimal | int | float | str = Decimal("1"),
    pool_factor: Decimal | int | float | str = Decimal("1"),
    market_factor: Decimal | int | float | str = Decimal("1"),
    weekend_factor: Decimal | int | float | str = Decimal("1"),
) -> Decimal:
    """兼容旧原型的仓位计算入口，返回未按交易所步长取整的张数。"""

    contract_decimal = _as_decimal(contract_value, "contract_value")
    if contract_decimal <= 0:
        raise RiskInputError("contract_value must be positive")
    equity_decimal = _as_decimal(equity, "equity")
    risk_decimal = _as_decimal(risk_fraction, "risk_fraction")
    entry_decimal = _as_decimal(entry_price, "entry_price")
    stop_decimal = _as_decimal(stop_loss_price, "stop_loss_price")
    if equity_decimal <= 0:
        raise RiskInputError("equity must be positive")
    if not 0 < risk_decimal <= 1:
        raise RiskInputError("risk_fraction must be greater than 0 and at most 1")
    if entry_decimal <= 0 or stop_decimal <= 0 or entry_decimal == stop_decimal:
        raise RiskInputError("entry_price and stop_loss_price must be positive and different")
    factors = tuple(
        _as_decimal(value, name)
        for name, value in (
            ("pool_factor", pool_factor),
            ("market_factor", market_factor),
            ("weekend_factor", weekend_factor),
        )
    )
    if any(factor < 0 for factor in factors):
        raise RiskInputError("position sizing factors cannot be negative")
    return (
        equity_decimal
        * risk_decimal
        * factors[0]
        * factors[1]
        * factors[2]
        / (abs(entry_decimal - stop_decimal) * contract_decimal)
    )
