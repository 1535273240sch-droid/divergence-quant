"""回测成本模型：手续费、滑点和 tick 对齐。

所有资金、价格和数量使用 Decimal。滑点只向不利方向施加；tick 对齐同样按不利方向取整，
避免回测因为浮点或精度处理得到偏乐观的成交价。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from .config import FEE_MODEL_VERSION, SLIPPAGE_MODEL_VERSION


@dataclass(frozen=True)
class FeeModel:
    """按成交名义价值收取手续费。"""

    maker_rate: Decimal
    taker_rate: Decimal
    version: str = FEE_MODEL_VERSION

    def fee(
        self,
        *,
        price: Decimal,
        size: Decimal,
        ct_val: Decimal,
        maker: bool,
    ) -> Decimal:
        rate = self.maker_rate if maker else self.taker_rate
        return price * size * ct_val * rate


@dataclass(frozen=True)
class SlippageModel:
    """固定比例滑点模型。"""

    rate: Decimal
    version: str = SLIPPAGE_MODEL_VERSION

    def apply(self, price: Decimal, *, is_buy: bool) -> Decimal:
        if is_buy:
            return price * (Decimal("1") + self.rate)
        return price * (Decimal("1") - self.rate)


def apply_slippage(price: Decimal, rate: Decimal, *, is_buy: bool) -> Decimal:
    """无状态滑点函数，便于单测和报告口径复用。"""

    if rate < 0 or rate >= 1:
        raise ValueError("slippage rate must be in [0, 1)")
    return SlippageModel(rate).apply(price, is_buy=is_buy)


def round_to_tick(price: Decimal, tick_sz: Decimal, *, round_up: bool) -> Decimal:
    """把价格对齐到交易对 tick；按不利方向取整。"""

    if tick_sz <= 0:
        raise ValueError("tick_sz must be positive")
    rounding = ROUND_CEILING if round_up else ROUND_FLOOR
    ticks = (price / tick_sz).to_integral_value(rounding=rounding)
    return ticks * tick_sz


def is_tick_aligned(price: Decimal, tick_sz: Decimal) -> bool:
    """判断价格是否已经落在 tick 网格上。"""

    if tick_sz <= 0:
        raise ValueError("tick_sz must be positive")
    return price % tick_sz == 0
