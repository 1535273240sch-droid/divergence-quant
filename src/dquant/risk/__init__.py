"""风险管理与紧急开关。

本包只审核策略信号，不负责创建订单或访问交易所。
"""

from .calculator import (
    PositionSizing,
    RiskInputError,
    calculate_position_size,
    max_drawdown,
    position_size,
    round_down_to_step,
)
from .killswitch import KillSwitch, KillSwitchState
from .manager import MarketRiskContext, RiskConfig, RiskManager, RiskRequest

__all__ = [
    "KillSwitch",
    "KillSwitchState",
    "MarketRiskContext",
    "PositionSizing",
    "RiskConfig",
    "RiskInputError",
    "RiskManager",
    "RiskRequest",
    "calculate_position_size",
    "max_drawdown",
    "position_size",
    "round_down_to_step",
]
