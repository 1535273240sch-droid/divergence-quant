"""交易所适配器（M02）：抽象接口、OKX 模拟盘适配器、限流、重试与统一错误。"""

from dquant.adapters.base import CandleCallback, ExchangeAdapter, OrderCallback
from dquant.adapters.errors import (
    ExchangeError,
    classify_okx_code,
    http_status_to_retriable,
)
from dquant.adapters.models import Balance, CurrencyBalance, FundingRate, OpenOrderSnapshot
from dquant.adapters.okx import (
    MAX_CANDLES_LIMIT,
    MAX_HISTORY_CANDLES_LIMIT,
    OkxAdapter,
    OkxCredentials,
    OkxTransport,
    UrllibTransport,
    build_request_headers,
    build_signature,
    iso_timestamp_ms,
)
from dquant.adapters.ratelimit import TokenBucketLimiter
from dquant.adapters.retry import RetryPolicy

__all__ = [
    "MAX_CANDLES_LIMIT",
    "MAX_HISTORY_CANDLES_LIMIT",
    "Balance",
    "CandleCallback",
    "CurrencyBalance",
    "ExchangeAdapter",
    "ExchangeError",
    "FundingRate",
    "OkxAdapter",
    "OkxCredentials",
    "OkxTransport",
    "OpenOrderSnapshot",
    "OrderCallback",
    "RetryPolicy",
    "TokenBucketLimiter",
    "UrllibTransport",
    "build_request_headers",
    "build_signature",
    "classify_okx_code",
    "http_status_to_retriable",
    "iso_timestamp_ms",
]
