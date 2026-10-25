"""回测模块异常定义。

回测错误必须显式暴露，不允许通过空结果或静默跳过掩盖数据、策略或风控问题。
"""

from __future__ import annotations


class BacktestError(Exception):
    """回测模块基础异常。"""


class BacktestDataError(BacktestError, ValueError):
    """行情数据、交易对元数据或交易窗口不合法。"""


class BacktestSignalError(BacktestError, ValueError):
    """策略返回的信号违反公共契约或回测时序约束。"""


class BacktestExecutionError(BacktestError, RuntimeError):
    """回测执行过程中出现无法继续的账户或接口错误。"""
