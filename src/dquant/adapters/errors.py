"""交易所适配器的统一错误封装（计划书 §5.1.2 错误处理）。

所有交易所交互失败都必须包装为 :class:`ExchangeError`，并通过 ``retriable``
标记调用方是否允许重试：网络超时、HTTP 5xx、429 以及 OKX 频率限制错误码可重试；
参数错误、鉴权失败、余额不足等不可重试。
"""

from __future__ import annotations

# OKX 明确表示"稍后重试即可"的业务错误码（以官方文档为准，仅收录已确认的）。
# 50011: 频率限制；50013: 系统繁忙。
RETRIABLE_OKX_CODES: frozenset[str] = frozenset({"50011", "50013"})


class ExchangeError(Exception):
    """交易所交互失败的统一异常。"""

    def __init__(
        self,
        code: str,
        msg: str,
        *,
        retriable: bool,
        http_status: int | None = None,
    ) -> None:
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg
        self.retriable = retriable
        self.http_status = http_status


def classify_okx_code(code: str) -> bool:
    """判断 OKX 业务错误码是否可重试。未收录的码默认不可重试。"""

    return code in RETRIABLE_OKX_CODES


def http_status_to_retriable(status: int) -> bool:
    """按 HTTP 状态码判断是否可重试：429 与 5xx 可重试，其余不可。"""

    return status == 429 or status >= 500
