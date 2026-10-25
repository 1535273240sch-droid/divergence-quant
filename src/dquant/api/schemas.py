"""API 接口数据请求与响应模型 (Pydantic Schemas)。

对接前端控制台与移动端，保持强类型校验。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dquant.contracts.types import OrderType, SignalAction, SignalSide


class BaseSchema(BaseModel):
    """基础 Schema 配置。"""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)


# ---------------------------------------------------------------------------
# 认证相关
# ---------------------------------------------------------------------------


class LoginRequest(BaseSchema):
    """用户登录请求（密码 + 强制 TOTP 二次验证）。"""

    username: str = Field(min_length=1, description="用户名")
    password: str = Field(min_length=1, description="登录密码")
    totp_code: str = Field(min_length=6, max_length=6, description="6 位 TOTP 动态验证码")


class TokenResponse(BaseSchema):
    """JWT 登录响应。"""

    access_token: str
    token_type: str = "bearer"
    expires_in_sec: int
    username: str


class TotpSetupResponse(BaseSchema):
    """TOTP 绑定初始化响应。"""

    secret: str = Field(description="Base32 格式 TOTP Secret")
    otpauth_url: str = Field(description="扫码 URI (otpauth://)")


class TotpVerifyRequest(BaseSchema):
    """TOTP 验证请求。"""

    secret: str
    totp_code: str


class UserInfo(BaseSchema):
    """当前用户信息。"""

    username: str
    role: str
    permissions: list[str] = Field(default_factory=lambda: ["admin"])


class ExchangeConnectionTestRequest(BaseSchema):
    """OKX 模拟盘连接测试请求；密钥只用于本次请求，不写入响应。"""

    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)
    passphrase: str = Field(min_length=1)


class ExchangeConnectionStatus(BaseSchema):
    """交易所连接状态摘要。"""

    exchange: str = "okx"
    mode: str = "demo"
    public_api: bool
    private_api: bool
    time_offset_ms: int | None = None
    api_key_hint: str | None = None
    balance_usdt: Decimal | None = None
    message: str


# ---------------------------------------------------------------------------
# 信号与确认下单
# ---------------------------------------------------------------------------


class TakeProfitItem(BaseSchema):
    """止盈明细。"""

    price: Decimal
    fraction: Decimal


class PendingSignalItem(BaseSchema):
    """待确认信号项目。"""

    signal_id: str
    inst_id: str
    side: SignalSide
    action: SignalAction
    entry_type: OrderType
    price: Decimal
    stop_loss: Decimal | None
    take_profits: list[TakeProfitItem] = Field(default_factory=list)
    reason: str
    strategy_name: str
    score: float
    created_ts_ms: int
    expires_at_ts_ms: int
    confirmation_token: str | None = None
    ai_comment: str | None = None
    ai_action: str | None = None


class ConfirmOrderRequest(BaseSchema):
    """确认下单请求体（POST 二次确认）。"""

    confirmation_token: str = Field(min_length=1, description="一次性有效确认令牌")
    current_price: Decimal | None = Field(
        default=None, description="前端当前最新价格（用于滑点偏离校验）"
    )
    max_slippage_pct: Decimal = Field(
        default=Decimal("0.01"), description="最大允许价格偏离比例（默认 1%）"
    )


class ConfirmOrderResponse(BaseSchema):
    """确认下单响应。"""

    success: bool
    signal_id: str
    inst_id: str
    side: str
    cl_ord_id: str
    status: str
    message: str


class RejectSignalRequest(BaseSchema):
    """人工驳回信号请求。"""

    reason: str = Field(default="人工复核驳回", description="驳回原因")


# ---------------------------------------------------------------------------
# 策略管理与参数审批
# ---------------------------------------------------------------------------


class StrategyInfo(BaseSchema):
    """策略状态与参数摘要。"""

    strategy_id: str
    name: str
    enabled: bool
    description: str
    params: dict[str, Any]
    current_positions: int = 0


class UpdateStrategyParamsRequest(BaseSchema):
    """修改策略参数请求。"""

    params: dict[str, Any] = Field(description="新参数字典")
    reason: str = Field(min_length=1, description="修改原因（写入审计日志）")


class ProposalActionRequest(BaseSchema):
    """AI 进化提案审批/驳回请求。"""

    reason: str = Field(default="管理员审批", description="处置理由")


# ---------------------------------------------------------------------------
# 紧急控制
# ---------------------------------------------------------------------------


class EmergencyStopOpenRequest(BaseSchema):
    """紧急停止开仓请求。"""

    reason: str = Field(min_length=1, description="停止开仓原因")


class EmergencyCloseAllRequest(BaseSchema):
    """紧急全部平仓请求。"""

    reason: str = Field(min_length=1, description="一键全平原因")
    confirm_phrase: str = Field(
        min_length=1,
        description="二次安全确认口令（必须输入 CLOSE_ALL_CONFIRMED）",
    )


class EmergencyResumeRequest(BaseSchema):
    """人工解除熔断恢复开仓请求。"""

    reason: str = Field(min_length=1, description="恢复开仓审核说明")


class EmergencyStateResponse(BaseSchema):
    """紧急开关当前状态。"""

    stop_open_enabled: bool = False
    stop_open_reason: str | None = None
    stop_open_at_ts_ms: int | None = None
    circuit_breaker_triggered: bool = False
    active_circuit_breakers: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 系统监控与通用响应
# ---------------------------------------------------------------------------


class HealthResponse(BaseSchema):
    """系统健康检查响应。"""

    status: str = "ok"
    version: str = "0.1.0"
    mode: str = "demo"
    uptime_seconds: float
    timestamp: int


class StandardSuccessResponse(BaseSchema):
    """通用成功响应。"""

    success: bool = True
    message: str = "操作成功"
    data: Any = None
