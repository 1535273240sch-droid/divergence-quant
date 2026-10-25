"""DivergenceQuant 可插拔策略框架。"""

from .base import BaseStrategy, StrategyContext, StrategyInputError
from .registry import (
    StrategyRegistrationError,
    create_strategy,
    list_strategies,
    register_strategy,
    registry,
)
from .runner import generate_signals
from .s1 import S1TrendPullbackStrategy
from .s2 import S2RangeReversalStrategy
from .s3 import S3CounterTrendStrategy
from .template_strategy import TemplateStrategy

__all__ = [
    "BaseStrategy",
    "S1TrendPullbackStrategy",
    "S2RangeReversalStrategy",
    "S3CounterTrendStrategy",
    "StrategyContext",
    "StrategyInputError",
    "StrategyRegistrationError",
    "TemplateStrategy",
    "create_strategy",
    "generate_signals",
    "list_strategies",
    "register_strategy",
    "registry",
]
