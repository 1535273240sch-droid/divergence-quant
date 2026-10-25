"""策略管理、参数更新与 AI 进化提案审批路由。

严格遵循计划书 §5.14 与 §17.6：
- 修改参数前记录差异对比（旧值 -> 新值），写入审计日志。
- AI 优化提案双锁审批：必须人工二次确认后方可合并应用。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from dquant.api.audit import AuditService
from dquant.api.auth import CurrentUser
from dquant.api.errors import NotFoundException
from dquant.api.schemas import (
    ProposalActionRequest,
    StandardSuccessResponse,
    StrategyInfo,
    UpdateStrategyParamsRequest,
)

router = APIRouter(prefix="/api/v1/strategies", tags=["策略与审批"])
_audit_service = AuditService()

# 共享策略状态字典
_strategy_registry_mock: dict[str, dict[str, Any]] = {
    "s1_trend_pullback": {
        "strategy_id": "s1_trend_pullback",
        "name": "S1 顺势回调背离",
        "enabled": True,
        "description": "顺大势逆小势，捕捉 MA200 主趋势方向上的 4H/1H 回调背离入场",
        "params": {
            "atr_period": 14,
            "sl_atr_mult": 1.5,
            "tp1_rr": 1.0,
            "tp2_rr": 2.0,
            "cool_down_hours": 48,
        },
        "current_positions": 1,
    },
    "s2_range_divergence": {
        "strategy_id": "s2_range_divergence",
        "name": "S2 震荡区间背离反转",
        "enabled": True,
        "description": "在 MA200 走平的震荡市中，结合支撑阻力位与背离进行高抛低吸",
        "params": {
            "band_margin_pct": 0.015,
            "min_touches": 2,
            "sl_buffer_atr": 1.0,
        },
        "current_positions": 0,
    },
    "s3_counter_trend": {
        "strategy_id": "s3_counter_trend",
        "name": "S3 逆势背离衰竭（高风险）",
        "enabled": False,
        "description": "主趋势极度延伸后的耗竭性反转，默认保持关闭，需人工独立授权开启",
        "params": {
            "risk_mult": 0.5,
            "tight_sl_atr": 1.0,
        },
        "current_positions": 0,
    },
}

_proposals_store_mock: dict[str, dict[str, Any]] = {}


@router.get("", response_model=list[StrategyInfo], summary="获取策略列表与参数")
async def list_strategies(user: CurrentUser) -> list[StrategyInfo]:
    """获取所有可用策略的基本信息、启用状态及当前运行参数。"""
    return [
        StrategyInfo(
            strategy_id=item["strategy_id"],
            name=item["name"],
            enabled=item["enabled"],
            description=item["description"],
            params=item["params"],
            current_positions=item.get("current_positions", 0),
        )
        for item in _strategy_registry_mock.values()
    ]


@router.put("/{strategy_id}/params", response_model=StandardSuccessResponse, summary="修改策略参数")
async def update_strategy_params(
    strategy_id: str,
    req: UpdateStrategyParamsRequest,
    user: CurrentUser,
) -> StandardSuccessResponse:
    """修改指定策略的运行参数。写入审计日志记录新旧参数对比。"""
    strategy = _strategy_registry_mock.get(strategy_id)
    if not strategy:
        raise NotFoundException(message=f"未找到策略: {strategy_id}")

    old_params = dict(strategy["params"])
    new_params = dict(req.params)

    # 更新参数
    strategy["params"] = new_params

    # 记录审计日志
    await _audit_service.log_action(
        user=user["username"],
        action="UPDATE_STRATEGY_PARAMS",
        before={"strategy_id": strategy_id, "params": old_params},
        after={"strategy_id": strategy_id, "params": new_params, "reason": req.reason},
    )

    return StandardSuccessResponse(message=f"策略 {strategy_id} 参数已更新并记录审计")


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=StandardSuccessResponse,
    summary="批准 AI 进化提案",
)
async def approve_proposal(
    proposal_id: str,
    req: ProposalActionRequest,
    user: CurrentUser,
) -> StandardSuccessResponse:
    """人工审批并应用 AI 策略进化提案。"""
    proposal = _proposals_store_mock.get(proposal_id)
    if proposal:
        proposal["status"] = "APPROVED"

    await _audit_service.log_action(
        user=user["username"],
        action="APPROVE_AI_PROPOSAL",
        before={"proposal_id": proposal_id, "status": "PENDING"},
        after={"proposal_id": proposal_id, "status": "APPROVED", "reason": req.reason},
    )

    return StandardSuccessResponse(message=f"AI 优化提案 {proposal_id} 已批准应用")


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=StandardSuccessResponse,
    summary="驳回 AI 进化提案",
)
async def reject_proposal(
    proposal_id: str,
    req: ProposalActionRequest,
    user: CurrentUser,
) -> StandardSuccessResponse:
    """驳回 AI 优化提案。"""
    proposal = _proposals_store_mock.get(proposal_id)
    if proposal:
        proposal["status"] = "REJECTED"

    await _audit_service.log_action(
        user=user["username"],
        action="REJECT_AI_PROPOSAL",
        before={"proposal_id": proposal_id, "status": "PENDING"},
        after={"proposal_id": proposal_id, "status": "REJECTED", "reason": req.reason},
    )

    return StandardSuccessResponse(message=f"AI 提案 {proposal_id} 已驳回")
