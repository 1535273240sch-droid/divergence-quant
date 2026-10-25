"""订单执行模块的明确错误类型。"""

from __future__ import annotations


class ExecutionError(RuntimeError):
    """执行层基础异常。"""


class InvalidOrderTransition(ExecutionError):
    """订单状态不能按当前状态机规则迁移。"""


class DuplicateOrderConflict(ExecutionError):
    """同一个 clOrdId 被用于不同订单请求。"""


class OrderInFlight(ExecutionError):
    """订单仍在交易所处理，不能再次提交。"""


class OrderAlreadyFailed(ExecutionError):
    """订单已经失败，不能静默重试。"""


class UnknownOrder(ExecutionError):
    """本地执行存储中不存在指定订单。"""


class OrderRejected(ExecutionError):
    """交易所明确拒绝订单。"""


class SubmissionUnknown(ExecutionError):
    """请求可能已经到达交易所，必须先对账而不能重复下单。"""


class LiveTradingDisabled(ExecutionError):
    """实盘交易没有显式解锁。"""


class RiskRejected(ExecutionError):
    """风控结果没有批准该信号。"""


class OrderSizeTooSmall(ExecutionError):
    """按交易所步长取整后低于最小下单量。"""


class ProtectionUnavailable(ExecutionError):
    """已成交仓位无法立即建立交易所端保护单。"""


class OpeningBlocked(ExecutionError):
    """当前执行实例禁止新的开仓。"""


class StartupRecoveryRequired(OpeningBlocked):
    """进程启动后尚未完成交易所对账恢复。"""


class ConfirmationError(ExecutionError):
    """半自动确认令牌无效或不能使用。"""


class InvalidConfirmationToken(ConfirmationError):
    """确认令牌签名、格式或存储记录不匹配。"""


class ConfirmationAlreadyUsed(ConfirmationError):
    """确认令牌已经被确认、放弃或消费。"""


class TokenExpired(ConfirmationError):
    """确认令牌已经过期。"""


class SlippageExceeded(ConfirmationError):
    """确认时市场价格超过允许偏离。"""


class ReconciliationError(ExecutionError):
    """对账过程发生明确错误。"""
