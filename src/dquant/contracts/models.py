"""DivergenceQuant 的跨模块业务数据模型。

本文件只描述数据，不执行网络访问、数据库写入或交易动作。业务模块通过这些模型交换数据，
从而保证回测、实盘、API 和前端使用同一套字段语义。
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .types import (
    AIAction,
    CandleBar,
    DivergenceKind,
    DivergenceMethod,
    InstrumentState,
    InstrumentType,
    OrderState,
    OrderType,
    PositionSide,
    RegimeState,
    SignalAction,
    SignalSide,
)


class ContractModel(BaseModel):
    """所有公共模型的基础配置。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=True)


class Instrument(ContractModel):
    """交易所交易对元数据。"""

    inst_id: str = Field(min_length=1)
    inst_type: InstrumentType = InstrumentType.SWAP
    base: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    ct_val: Decimal = Field(gt=0)
    lot_sz: Decimal = Field(gt=0)
    min_sz: Decimal = Field(gt=0)
    tick_sz: Decimal = Field(gt=0)
    state: InstrumentState = InstrumentState.LIVE
    list_time_ms: int | None = Field(default=None, ge=0)


class Ticker(ContractModel):
    """最新行情快照。"""

    inst_id: str = Field(min_length=1)
    last: Decimal = Field(gt=0)
    bid: Decimal | None = Field(default=None, gt=0)
    ask: Decimal | None = Field(default=None, gt=0)
    quote_volume_24h: Decimal = Field(ge=0)
    ts_ms: int = Field(ge=0)


class Candle(ContractModel):
    """一根已经存储或展示的 K 线。"""

    inst_id: str = Field(min_length=1)
    bar: CandleBar
    ts_ms: int = Field(ge=0)
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    quote_volume: Decimal = Field(ge=0)
    confirmed: bool

    @model_validator(mode="after")
    def validate_ohlc(self) -> "Candle":
        """保证 OHLC 结构有效。"""

        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be greater than or equal to open, close and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be less than or equal to open, close and high")
        return self


class RegimeResult(ContractModel):
    """MA200 趋势分类结果。"""

    inst_id: str = Field(min_length=1)
    bar: CandleBar
    ts_ms: int = Field(ge=0)
    state: RegimeState
    ma_type: str = Field(pattern="^(SMA|EMA)$")
    dist: float
    slope: float
    above_ratio: float = Field(ge=0, le=1)
    cross_count: int = Field(ge=0)
    bars_in_state: int = Field(ge=0)


class DivergenceSignal(ContractModel):
    """背离检测器输出的完整信号。"""

    inst_id: str = Field(min_length=1)
    bar: CandleBar
    kind: DivergenceKind
    method: DivergenceMethod = DivergenceMethod.MACD_SEGMENT
    seg1_start_ts: int = Field(ge=0)
    seg1_end_ts: int = Field(ge=0)
    seg1_extreme_ts: int = Field(ge=0)
    seg1_extreme_price: Decimal = Field(gt=0)
    seg1_peak: float = Field(ge=0)
    seg1_area: float = Field(ge=0)
    seg1_avg_vol: float = Field(ge=0)
    seg2_start_ts: int = Field(ge=0)
    seg2_end_ts: int = Field(ge=0)
    seg2_extreme_ts: int = Field(ge=0)
    seg2_extreme_price: Decimal = Field(gt=0)
    seg2_peak: float = Field(ge=0)
    seg2_area: float = Field(ge=0)
    seg2_avg_vol: float = Field(ge=0)
    height_div: bool
    area_div: bool
    volume_confirm: bool
    close_confirm: bool
    divergence_order: int = Field(ge=1)
    confirmed_ts: int = Field(ge=0)
    score: float = Field(ge=0, le=100)
    preliminary: bool = False


class ScreenerCandidate(ContractModel):
    """选币器交给策略层的候选对象。"""

    inst_id: str = Field(min_length=1)
    pool: str = Field(pattern="^[A-F]$")
    regime: RegimeState
    divergence_id: str | None = None
    divergence_kind: DivergenceKind | None = None
    score: float = Field(ge=0, le=100)
    rank: int = Field(ge=1)
    updated_ts_ms: int = Field(ge=0)


class TakeProfit(ContractModel):
    """单个止盈目标。"""

    price: Decimal = Field(gt=0)
    fraction: Decimal = Field(gt=0, le=1)


class Signal(ContractModel):
    """策略层生成的交易信号，不代表已经下单。"""

    signal_id: str = Field(min_length=1)
    inst_id: str = Field(min_length=1)
    side: SignalSide
    action: SignalAction
    entry_type: OrderType
    price: Decimal = Field(gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profits: list[TakeProfit] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    strategy_name: str = Field(min_length=1)
    created_ts_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_open_signal_protection(self) -> "Signal":
        """开仓信号必须带止损，保护资金优先。"""

        if self.action == SignalAction.OPEN and self.stop_loss is None:
            raise ValueError("open signals must include stop_loss")
        return self


class RiskDecision(ContractModel):
    """风控审核结果。"""

    signal_id: str = Field(min_length=1)
    approved: bool
    rule: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    qty: Decimal = Field(ge=0)
    risk_fraction: Decimal = Field(ge=0, le=1)


class OrderRequest(ContractModel):
    """OMS 交给交易所适配器的订单请求。"""

    cl_ord_id: str = Field(min_length=1)
    inst_id: str = Field(min_length=1)
    side: SignalSide
    pos_side: PositionSide
    order_type: OrderType
    price: Decimal | None = Field(default=None, gt=0)
    size: Decimal = Field(gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profits: list[TakeProfit] = Field(default_factory=list)
    reduce_only: bool = False
    mode: str = Field(pattern="^(demo|live)$")

    @model_validator(mode="after")
    def validate_limit_price(self) -> "OrderRequest":
        """限价单必须有价格，市价单可以省略价格。"""

        if self.order_type == OrderType.LIMIT and self.price is None:
            raise ValueError("limit orders must include price")
        return self


class OrderAck(ContractModel):
    """交易所返回的订单确认。"""

    cl_ord_id: str = Field(min_length=1)
    ord_id: str = Field(min_length=1)
    state: OrderState
    accepted_ts_ms: int = Field(ge=0)
    message: str = ""


class Position(ContractModel):
    """本地持仓快照。"""

    inst_id: str = Field(min_length=1)
    pos_side: PositionSide
    size: Decimal = Field(ge=0)
    avg_price: Decimal = Field(gt=0)
    mark_price: Decimal = Field(gt=0)
    upl: Decimal
    stop_loss: Decimal | None = Field(default=None, gt=0)
    strategy: str = Field(min_length=1)
    opened_ts_ms: int = Field(ge=0)


class Fill(ContractModel):
    """成交回报。"""

    trade_id: str = Field(min_length=1)
    cl_ord_id: str = Field(min_length=1)
    price: Decimal = Field(gt=0)
    size: Decimal = Field(gt=0)
    fee: Decimal = Field(ge=0)
    ts_ms: int = Field(ge=0)


class AIReview(ContractModel):
    """AI 对候选信号的结构化审阅结果。"""

    review_id: str = Field(min_length=1)
    inst_id: str = Field(min_length=1)
    action: AIAction
    score_delta: int = Field(ge=-15, le=15)
    comment_cn: str = Field(max_length=120)
    model_used: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    cost_usd: Decimal = Field(ge=0)
    created_ts_ms: int = Field(ge=0)
