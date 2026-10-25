"""Pure functions for portfolio risk calculations."""

from collections.abc import Sequence


def max_drawdown(equity_curve: Sequence[float]) -> float:
    """Return the largest peak-to-trough decline as a decimal fraction.

    For example, a decline from 120 to 90 returns ``0.25`` (25%).
    The input must contain at least one positive equity value.
    """

    if not equity_curve:
        raise ValueError("equity_curve must contain at least one value")

    if any(equity <= 0 for equity in equity_curve):
        raise ValueError("equity values must be positive")

    peak = equity_curve[0]
    largest_drawdown = 0.0

    for equity in equity_curve:
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak
        largest_drawdown = max(largest_drawdown, drawdown)

    return largest_drawdown


def position_size(
    equity: float,
    risk_fraction: float,
    entry_price: float,
    stop_loss_price: float,
    *,
    contract_value: float = 1.0,
    pool_factor: float = 1.0,
    market_factor: float = 1.0,
    weekend_factor: float = 1.0,
) -> float:
    """Calculate position size in contracts/units from a fixed-risk rule.

    The calculation follows the project's risk formula::

        position = (
            equity * risk_fraction * pool_factor * market_factor * weekend_factor
        ) / (abs(entry_price - stop_loss_price) * contract_value)

    All factors are pure inputs, so callers can apply strategy-pool, market,
    and weekend adjustments without mutating state.
    """

    if equity <= 0:
        raise ValueError("equity must be positive")
    if not 0 < risk_fraction <= 1:
        raise ValueError("risk_fraction must be greater than 0 and at most 1")
    if entry_price <= 0 or stop_loss_price <= 0:
        raise ValueError("entry_price and stop_loss_price must be positive")
    if entry_price == stop_loss_price:
        raise ValueError("entry_price and stop_loss_price must be different")
    if contract_value <= 0:
        raise ValueError("contract_value must be positive")
    if any(factor < 0 for factor in (pool_factor, market_factor, weekend_factor)):
        raise ValueError("position sizing factors cannot be negative")

    risk_amount = equity * risk_fraction * pool_factor * market_factor * weekend_factor
    risk_per_unit = abs(entry_price - stop_loss_price) * contract_value
    return risk_amount / risk_per_unit
