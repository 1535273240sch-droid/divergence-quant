"""M02 适配层内部的数据快照模型。

这些对象不属于公共契约（`协作/00_总控/接口契约.md` 清单中没有它们），因此只能在
适配层内部定义和使用；需要跨模块共享时必须先走契约变更流程，禁止悄悄当公共对象用。
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class AdapterModel(BaseModel):
    """适配层内部模型的基础配置，与公共契约保持同样的严格风格。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=True)


class CurrencyBalance(AdapterModel):
    """单个币种的账户余额快照。"""

    currency: str = Field(min_length=1)
    equity: Decimal = Field(ge=0)
    available: Decimal = Field(ge=0)
    frozen: Decimal = Field(ge=0)


class Balance(AdapterModel):
    """账户权益快照。"""

    ts_ms: int = Field(ge=0)
    total_equity_usd: Decimal = Field(ge=0)
    details: dict[str, CurrencyBalance] = Field(default_factory=dict)


class FundingRate(AdapterModel):
    """永续合约资金费率快照。"""

    inst_id: str = Field(min_length=1)
    funding_rate: Decimal
    funding_time_ms: int = Field(ge=0)
    next_funding_time_ms: int = Field(ge=0)


class OpenOrderSnapshot(AdapterModel):
    """交易所当前挂单快照（公共契约没有 Order 模型，此处为适配层内部表示）。"""

    ord_id: str = Field(min_length=1)
    cl_ord_id: str | None = None
    inst_id: str = Field(min_length=1)
    # buy/sell 是 OKX 原始方向语义，与公共契约的 long/short 持仓方向不同，禁止混用。
    side: str = Field(pattern="^(buy|sell)$")
    pos_side: str = Field(pattern="^(long|short)$")
    order_type: str = Field(min_length=1)
    price: Decimal | None = Field(default=None, gt=0)
    size: Decimal = Field(gt=0)
    state: str = Field(pattern="^(live|partially_filled)$")
