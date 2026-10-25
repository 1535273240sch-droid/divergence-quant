"""AI 模块的异常类型。

异常类型用于让上层区分预算、网络、响应结构和配置错误，并据此执行分级降级。
"""


class AIGatewayError(RuntimeError):
    """AI 网关调用失败。"""


class AIGatewayTimeout(AIGatewayError):
    """模型调用超过超时限制。"""


class AIResponseError(AIGatewayError):
    """模型返回无法解析或不符合约定结构。"""


class AIBudgetExceeded(AIGatewayError):
    """当前 UTC 自然日预算已用尽。"""


class AIConfigurationError(ValueError):
    """AI 路由配置不合法。"""
