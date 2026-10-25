"""公共枚举类型。

枚举值是跨模块接口的一部分。修改枚举值前必须先更新契约版本和迁移说明。
"""

from enum import StrEnum


class InstrumentType(StrEnum):
    """交易品种类型。"""

    SWAP = "SWAP"


class InstrumentState(StrEnum):
    """交易品种状态。"""

    LIVE = "live"
    SUSPENDED = "suspended"
    EXPIRED = "expired"


class CandleBar(StrEnum):
    """系统支持的标准 K 线周期。"""

    ONE_HOUR = "1H"
    FOUR_HOUR = "4H"
    ONE_DAY = "1D"


class RegimeState(StrEnum):
    """市场趋势状态。"""

    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGE = "RANGE"


class DivergenceKind(StrEnum):
    """背离类型。"""

    BULL_REGULAR = "BULL_REGULAR"
    BEAR_REGULAR = "BEAR_REGULAR"
    BULL_HIDDEN = "BULL_HIDDEN"
    BEAR_HIDDEN = "BEAR_HIDDEN"


class DivergenceMethod(StrEnum):
    """背离检测方法。"""

    MACD_SEGMENT = "MACD_SEGMENT"
    PIVOT_RSI = "PIVOT_RSI"
    PIVOT_DIF = "PIVOT_DIF"


class SignalSide(StrEnum):
    """交易方向。"""

    LONG = "long"
    SHORT = "short"


class SignalAction(StrEnum):
    """策略信号动作。"""

    OPEN = "open"
    CLOSE = "close"
    REDUCE = "reduce"
    MOVE_STOP = "move_stop"


class PositionSide(StrEnum):
    """OKX 双向持仓方向。"""

    LONG = "long"
    SHORT = "short"


class OrderType(StrEnum):
    """订单类型。"""

    LIMIT = "limit"
    MARKET = "market"


class OrderState(StrEnum):
    """订单状态机状态。"""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class AIAction(StrEnum):
    """AI 对交易候选的建议动作。"""

    PASS = "PASS"
    HALF_SIZE = "HALF_SIZE"
    VETO = "VETO"
