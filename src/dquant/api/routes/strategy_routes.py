"""策略管理、参数热更新与 AI 进化提案审批路由。

策略参数写入 ``data/strategy_runtime.json``，引擎每 30 秒检测文件版本并热加载。
修改参数前记录旧值/新值到审计日志；所有参数先经过策略类构造校验，避免把坏参数
推给交易进程。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter

from dquant.api import live_data
from dquant.api.audit import AuditService
from dquant.api.auth import CurrentUser
from dquant.api.errors import APIException, NotFoundException
from dquant.api.schemas import (
    ProposalActionRequest,
    StandardSuccessResponse,
    StrategyInfo,
    UpdateStrategyParamsRequest,
)
from dquant.runtime import StrategyRuntimeConfig
from dquant.settings import load_settings
from dquant.strategies import create_strategy

router = APIRouter(prefix="/api/v1/strategies", tags=["策略与审批"])
_audit_service = AuditService()


def _runtime_config() -> StrategyRuntimeConfig:
    """每次按当前工作目录/环境变量创建，便于测试隔离运行时文件。"""

    return StrategyRuntimeConfig()

# strategy_id 是前端稳定标识；class_name 是注册器中的策略类名。
_STRATEGY_META: dict[str, dict[str, str]] = {
    "mhd_divergence": {
        "class_name": "MHD",
        "name": "MHD 摆动点 MACD 柱背离",
        "description": (
            "在摆动高点/低点比较价格与 MACD 柱的同价不同势；"
            "D_pair 与上一个同类摆动点比较，D_peak 与回看窗口内柱子最极端的摆动点比较，取更强值。"
        ),
        "formula": (
            "pm=(price_b-price_ref)/ATR14_ref; "
            "hm=(hist_b-hist_ref)/stdev(hist,100)_b; "
            "D=pm-hm; D=max(D_pair,D_peak)"
        ),
    },
    "s1_trend_pullback": {
        "class_name": "S1",
        "name": "S1 顺势回调背离",
        "description": "顺大势逆小势，捕捉 MA200 主趋势方向上的 4H/1H 回调背离入场",
        "formula": "",
    },
    "s2_range_divergence": {
        "class_name": "S2",
        "name": "S2 震荡区间背离反转",
        "description": "在 MA200 走平的震荡市中，结合支撑阻力位与背离进行高抛低吸",
        "formula": "",
    },
    "s3_counter_trend": {
        "class_name": "S3",
        "name": "S3 逆势背离衰竭（高风险）",
        "description": "主趋势极度延伸后的耗竭性反转，默认保持关闭，需人工独立授权开启",
        "formula": "",
    },
}

_proposals_store_mock: dict[str, dict[str, Any]] = {}


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _active_names() -> list[str]:
    try:
        return list(load_settings().strategies.active)
    except Exception:  # noqa: BLE001 - 配置读取失败时仍允许查看策略
        return ["MHD"]


def _effective_params(class_name: str) -> dict[str, Any]:
    strategy = create_strategy(class_name)
    params = {name: _json_value(value) for name, value in strategy.params.items()}
    try:
        configured = load_settings().strategies.params.get(class_name, {})
        params.update({name: _json_value(value) for name, value in configured.items()})
    except Exception:  # noqa: BLE001 - 配置读取失败时使用策略默认值
        pass
    params.update(
        {
            name: _json_value(value)
            for name, value in _runtime_config().params_for(class_name).items()
        }
    )
    return params


def _position_count(class_name: str) -> int:
    try:
        return sum(
            1
            for row in live_data.open_positions()
            if str(row.get("strategy", "")) == class_name
        )
    except Exception:  # noqa: BLE001 - 前端展示不应因数据库忙失败
        return 0


@router.get("", response_model=list[StrategyInfo], summary="获取策略列表与参数")
async def list_strategies(user: CurrentUser) -> list[StrategyInfo]:
    """获取所有已注册策略、启用状态、当前运行参数与模拟持仓数量。"""

    del user
    active = set(_active_names())
    items: list[StrategyInfo] = []
    for strategy_id, meta in _STRATEGY_META.items():
        class_name = meta["class_name"]
        params = _effective_params(class_name)
        enabled = bool(params.get("enabled", True)) and class_name in active
        items.append(
            StrategyInfo(
                strategy_id=strategy_id,
                name=meta["name"],
                enabled=enabled,
                description=meta["description"],
                params=params,
                current_positions=_position_count(class_name),
                formula=meta["formula"] or None,
            )
        )
    return items


@router.put("/{strategy_id}/params", response_model=StandardSuccessResponse, summary="修改策略参数")
async def update_strategy_params(
    strategy_id: str,
    req: UpdateStrategyParamsRequest,
    user: CurrentUser,
) -> StandardSuccessResponse:
    """修改指定策略参数并写入运行时文件；引擎 30 秒内热加载。"""

    meta = _STRATEGY_META.get(strategy_id)
    if meta is None:
        raise NotFoundException(message=f"未找到策略: {strategy_id}")
    class_name = meta["class_name"]
    old_effective = _effective_params(class_name)
    merged = dict(create_strategy(class_name).params)
    merged.update(_runtime_config().params_for(class_name))
    merged.update(req.params)
    try:
        create_strategy(class_name, merged)
    except Exception as exc:  # noqa: BLE001 - 参数错误返回 400，不写坏配置
        raise APIException("INVALID_STRATEGY_PARAMS", str(exc), 400) from exc

    _runtime_config().update(class_name, req.params)
    new_effective = _effective_params(class_name)
    await _audit_service.log_action(
        user=user["username"],
        action="UPDATE_STRATEGY_PARAMS",
        before={"strategy_id": strategy_id, "params": old_effective},
        after={"strategy_id": strategy_id, "params": new_effective, "reason": req.reason},
    )
    return StandardSuccessResponse(
        message=(
            f"策略 {strategy_id} 参数已保存并写入运行时配置，"
            "引擎将在 30 秒内热加载（模拟仓位同步调整）"
        )
    )


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
