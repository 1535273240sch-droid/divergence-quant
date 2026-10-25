"""策略注册与实例化。"""

from __future__ import annotations

from typing import TypeVar

from .base import BaseStrategy


class StrategyRegistrationError(ValueError):
    """策略名称缺失或重复注册。"""


StrategyClass = TypeVar("StrategyClass", bound=type[BaseStrategy])


class StrategyRegistry:
    """进程内策略注册表。

    注册表只保存策略类，不保存策略实例和运行状态，因此回测与实时运行可以分别创建
    独立实例，避免不同任务之间共享可变参数。
    """

    def __init__(self) -> None:
        self._strategies: dict[str, type[BaseStrategy]] = {}

    def register(self, strategy_cls: StrategyClass) -> StrategyClass:
        name = getattr(strategy_cls, "name", "")
        if not name or name == "BASE":
            raise StrategyRegistrationError("strategy class must define a non-empty name")
        if name in self._strategies:
            raise StrategyRegistrationError(f"strategy {name!r} is already registered")
        self._strategies[name] = strategy_cls
        return strategy_cls

    def create(self, name: str, params: dict[str, object] | None = None) -> BaseStrategy:
        try:
            strategy_cls = self._strategies[name]
        except KeyError as exc:
            raise KeyError(f"unknown strategy: {name}") from exc
        return strategy_cls(params=params)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._strategies))

    def get(self, name: str) -> type[BaseStrategy]:
        try:
            return self._strategies[name]
        except KeyError as exc:
            raise KeyError(f"unknown strategy: {name}") from exc


registry = StrategyRegistry()


def register_strategy(strategy_cls: StrategyClass) -> StrategyClass:
    """把策略类注册到默认注册表，并支持装饰器写法。"""

    return registry.register(strategy_cls)


def create_strategy(name: str, params: dict[str, object] | None = None) -> BaseStrategy:
    """按名称创建策略实例。"""

    return registry.create(name, params=params)


def list_strategies() -> tuple[str, ...]:
    """返回稳定排序的已注册策略名称。"""

    return registry.names()


__all__ = [
    "StrategyRegistrationError",
    "create_strategy",
    "list_strategies",
    "register_strategy",
    "registry",
]
