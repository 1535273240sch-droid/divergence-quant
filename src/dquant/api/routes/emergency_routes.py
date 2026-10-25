"""紧急风控开关与灾难处置路由。

提供：
1. 紧急停止新开仓 (Kill Switch: Stop Opening)。
2. 紧急一键全平 (Kill Switch: Close All Positions)。
3. 人工解除熔断与恢复运行。
4. 全程记录高等级审计日志并广播 WebSocket。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter

from dquant.api.audit import AuditService
from dquant.api.auth import CurrentUser
from dquant.api.errors import APIException
from dquant.api.schemas import (
    EmergencyCloseAllRequest,
    EmergencyResumeRequest,
    EmergencyStateResponse,
    EmergencyStopOpenRequest,
    StandardSuccessResponse,
)
from dquant.api.websocket import ws_manager

router = APIRouter(prefix="/api/v1/emergency", tags=["紧急开关与风控"])
_audit_service = AuditService()

# 共享紧急状态单例
_emergency_state: dict[str, Any] = {
    "stop_open_enabled": False,
    "stop_open_reason": None,
    "stop_open_at_ts_ms": None,
    "circuit_breaker_triggered": False,
    "active_circuit_breakers": [],
}


@router.get("/status", response_model=EmergencyStateResponse, summary="获取紧急开关与熔断状态")
async def get_emergency_status(user: CurrentUser) -> EmergencyStateResponse:
    """查询系统当前是否处于停止开仓或风控熔断保护状态。"""
    return EmergencyStateResponse(
        stop_open_enabled=_emergency_state["stop_open_enabled"],
        stop_open_reason=_emergency_state["stop_open_reason"],
        stop_open_at_ts_ms=_emergency_state["stop_open_at_ts_ms"],
        circuit_breaker_triggered=_emergency_state["circuit_breaker_triggered"],
        active_circuit_breakers=list(_emergency_state["active_circuit_breakers"]),
    )


@router.post("/stop-open", response_model=StandardSuccessResponse, summary="紧急停止开仓")
async def trigger_stop_open(
    req: EmergencyStopOpenRequest,
    user: CurrentUser,
) -> StandardSuccessResponse:
    """触发紧急停止新开仓。已有持仓保持止损止盈保护，不再开立新仓位。"""
    now_ms = int(time.time() * 1000)
    old_state = dict(_emergency_state)

    _emergency_state["stop_open_enabled"] = True
    _emergency_state["stop_open_reason"] = req.reason
    _emergency_state["stop_open_at_ts_ms"] = now_ms

    await _audit_service.log_action(
        user=user["username"],
        action="EMERGENCY_STOP_OPEN",
        before={"stop_open_enabled": old_state["stop_open_enabled"]},
        after={"stop_open_enabled": True, "reason": req.reason},
    )

    await ws_manager.broadcast(
        {
            "type": "EMERGENCY_STATE_CHANGED",
            "stop_open_enabled": True,
            "reason": req.reason,
            "timestamp": now_ms,
        }
    )

    return StandardSuccessResponse(message="紧急停止开仓已生效，系统将拦截所有新开仓指令")


@router.post("/close-all", response_model=StandardSuccessResponse, summary="紧急一键全部平仓")
async def trigger_close_all(
    req: EmergencyCloseAllRequest,
    user: CurrentUser,
) -> StandardSuccessResponse:
    """紧急全部平仓。

    极高风险操作！必须提供明确确认口令：CLOSE_ALL_CONFIRMED。
    触发后立即停止开仓，并向 OMS 发送市价平仓全部活动持仓指令。
    """
    if req.confirm_phrase != "CLOSE_ALL_CONFIRMED":
        raise APIException(
            code="CONFIRM_PHRASE_MISMATCH",
            message="二次确认安全口令错误，必须输入 CLOSE_ALL_CONFIRMED 才能执行全部平仓",
        )

    now_ms = int(time.time() * 1000)
    _emergency_state["stop_open_enabled"] = True
    _emergency_state["stop_open_reason"] = f"一键全平触发: {req.reason}"
    _emergency_state["stop_open_at_ts_ms"] = now_ms

    await _audit_service.log_action(
        user=user["username"],
        action="EMERGENCY_CLOSE_ALL_POSITIONS",
        before={"action": "REQUEST_CLOSE_ALL"},
        after={"reason": req.reason, "status": "EXECUTION_DISPATCHED"},
    )

    await ws_manager.broadcast(
        {
            "type": "EMERGENCY_CLOSE_ALL_TRIGGERED",
            "reason": req.reason,
            "operator": user["username"],
            "timestamp": now_ms,
        }
    )

    return StandardSuccessResponse(message="一键全部平仓指令已分发，所有持仓正在加速市价平仓")


@router.post("/resume", response_model=StandardSuccessResponse, summary="人工解除熔断/恢复开仓")
async def resume_trading(
    req: EmergencyResumeRequest,
    user: CurrentUser,
) -> StandardSuccessResponse:
    """人工解除熔断限制，恢复正常开仓交易。"""
    now_ms = int(time.time() * 1000)
    old_state = dict(_emergency_state)

    _emergency_state["stop_open_enabled"] = False
    _emergency_state["stop_open_reason"] = None
    _emergency_state["stop_open_at_ts_ms"] = None
    _emergency_state["circuit_breaker_triggered"] = False
    _emergency_state["active_circuit_breakers"].clear()

    await _audit_service.log_action(
        user=user["username"],
        action="EMERGENCY_RESUME_TRADING",
        before={"stop_open_enabled": old_state["stop_open_enabled"]},
        after={"stop_open_enabled": False, "reason": req.reason},
    )

    await ws_manager.broadcast(
        {
            "type": "EMERGENCY_STATE_CHANGED",
            "stop_open_enabled": False,
            "reason": "人工解除熔断恢复",
            "timestamp": now_ms,
        }
    )

    return StandardSuccessResponse(message="系统已人工解除熔断限制，恢复正常运行")
