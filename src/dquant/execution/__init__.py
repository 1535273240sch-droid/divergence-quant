"""订单执行、OMS、对账和半自动确认。"""

from .confirm import ConfirmationService, ConfirmationTicket
from .errors import (
    ConfirmationAlreadyUsed,
    DuplicateOrderConflict,
    InvalidConfirmationToken,
    InvalidOrderTransition,
    LiveTradingDisabled,
    OpeningBlocked,
    OrderAlreadyFailed,
    OrderInFlight,
    OrderRejected,
    OrderSizeTooSmall,
    ProtectionUnavailable,
    RiskRejected,
    SlippageExceeded,
    StartupRecoveryRequired,
    SubmissionUnknown,
    TokenExpired,
    UnknownOrder,
)
from .management import PositionAction, build_position_actions
from .oms import OMS, ExecutionConfig, TimeoutOutcome
from .state import build_client_order_id, transition_order_state
from .store import InMemoryExecutionStore, StoredOrder

__all__ = [
    "ConfirmationAlreadyUsed",
    "ConfirmationService",
    "ConfirmationTicket",
    "DuplicateOrderConflict",
    "ExecutionConfig",
    "InMemoryExecutionStore",
    "InvalidConfirmationToken",
    "InvalidOrderTransition",
    "LiveTradingDisabled",
    "OMS",
    "OpeningBlocked",
    "OrderAlreadyFailed",
    "OrderInFlight",
    "OrderRejected",
    "OrderSizeTooSmall",
    "PositionAction",
    "ProtectionUnavailable",
    "RiskRejected",
    "SlippageExceeded",
    "StartupRecoveryRequired",
    "StoredOrder",
    "SubmissionUnknown",
    "TimeoutOutcome",
    "TokenExpired",
    "UnknownOrder",
    "build_client_order_id",
    "build_position_actions",
    "transition_order_state",
]
