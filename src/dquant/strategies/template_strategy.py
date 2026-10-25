"""用户自定义策略模板。

复制本文件后修改 ``name``、``accepts`` 和 ``on_bar``，即可通过注册器启用新策略。
模板本身明确拒绝所有候选，不会在未实现业务规则时产生假信号。
"""

from __future__ import annotations

from dquant.contracts import ScreenerCandidate, Signal

from .base import BaseStrategy, StrategyContext


class TemplateStrategy(BaseStrategy):
    """可复制的最小策略模板。"""

    name = "TEMPLATE"
    version = "1.0.0"

    def accepts(self, candidate: ScreenerCandidate) -> bool:
        """模板不默认接管任何候选，避免误交易。"""

        del candidate
        return False

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        """模板不产生信号；实现者应替换为明确的交易规则。"""

        del ctx
        return []
