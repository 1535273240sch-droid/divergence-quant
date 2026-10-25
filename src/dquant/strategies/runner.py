"""策略运行辅助入口。"""

from __future__ import annotations

from collections.abc import Iterable

from dquant.contracts import Signal

from .base import BaseStrategy, StrategyContext


def generate_signals(strategy: BaseStrategy, contexts: Iterable[StrategyContext]) -> list[Signal]:
    """按上下文顺序运行策略，供回测和实时调度共享。

    该入口不负责撮合、风控或数据库写入；调用方可以在回测和实盘中复用完全相同的
    ``on_bar`` 逻辑。
    """

    signals: list[Signal] = []
    for context in contexts:
        signals.extend(strategy.on_bar(context))
    return signals
