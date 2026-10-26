"""交易信号与确认下单路由。

严格遵循安全交付原则：
1. GET 请求绝不直接产生交易动作。
2. 确认下单必须为 POST，需要登录权限 + 一次性有效令牌 (Token) + 价格偏离滑点校验。
3. 严格防范重放攻击与 CSRF。
4. 全程记录审计日志与 WebSocket 状态广播。
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter

from dquant.api import live_data
from dquant.api.audit import AuditService
from dquant.api.auth import CurrentUser, OptionalUser, confirmation_token_manager
from dquant.api.errors import APIException, NotFoundException
from dquant.api.schemas import (
    ConfirmOrderRequest,
    ConfirmOrderResponse,
    PendingSignalItem,
    RejectSignalRequest,
    StandardSuccessResponse,
    TakeProfitItem,
)
from dquant.api.websocket import ws_manager
from dquant.contracts.types import OrderType, SignalAction, SignalSide

router = APIRouter(prefix="/api/v1/signals", tags=["信号与下单"])
_audit_service = AuditService()

# 模拟共享内存待确认信号存储池（供测试与前后端联调）
_pending_signals_store: dict[str, dict[str, Any]] = {}


def populate_mock_signal(signal_data: dict[str, Any]) -> None:
    """工具函数：注入模拟待确认信号（供开发与集成测试调用）。"""
    sig_id = signal_data["signal_id"]
    _pending_signals_store[sig_id] = signal_data


def _confirmation_token(signal_id: str, user: dict[str, Any] | None, now: float) -> str | None:
    """只向已登录用户发放确认令牌，匿名只读不能获得交易凭证。"""

    return confirmation_token_manager.create_token(signal_id, now=now) if user else None


def _created_ms(value: Any, fallback: int) -> int:
    if hasattr(value, "timestamp"):
        return int(value.timestamp() * 1000)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            pass
    return fallback


@router.get("/pending", response_model=list[PendingSignalItem], summary="获取待确认交易信号列表")
async def list_pending_signals(user: OptionalUser) -> list[PendingSignalItem]:
    """获取系统当前待人工确认的信号列表。

    数据来源：引擎库 ``strategy_signals``（status=PENDING）与进程内注入池的并集。
    每个信号都会动态挂载一份带有数字签名的一次性 confirmation_token。
    """
    now = time.time()
    results: list[PendingSignalItem] = []

    # 1) 引擎落库的真实待确认信号
    for row in live_data.pending_signals():
        sig_id = row["signal_id"]
        if sig_id in _pending_signals_store:
            continue
        try:
            signal_side = SignalSide(row["side"])
            signal_action = SignalAction(row["action"])
        except ValueError:
            continue
        created_dt = row.get("created_at")
        created_ms = (
            int(created_dt.timestamp() * 1000)
            if hasattr(created_dt, "timestamp")
            else int(now * 1000)
        )
        tps = [
            TakeProfitItem(price=Decimal(str(tp["price"])), fraction=Decimal(str(tp["fraction"])))
            for tp in (row.get("take_profits") or [])
            if isinstance(tp, dict) and "price" in tp and "fraction" in tp
        ]
        results.append(
            PendingSignalItem(
                signal_id=sig_id,
                inst_id=row["inst_id"],
                side=signal_side,
                action=signal_action,
                entry_type=OrderType.LIMIT,
                price=Decimal(str(row["price"])),
                stop_loss=Decimal(str(row["stop_loss"])) if row["stop_loss"] is not None else None,
                take_profits=tps,
                reason=row["reason"],
                strategy_name=row["strategy_name"],
                score=0.0,
                created_ts_ms=_created_ms(created_dt, created_ms),
                expires_at_ts_ms=created_ms + 900_000,
                confirmation_token=_confirmation_token(sig_id, user, now),
                ai_comment=None,
                ai_action=None,
            )
        )

    # 2) 进程内注入池（联调 / 测试用）
    for sig_id, data in list(_pending_signals_store.items()):
        if data.get("status") != "PENDING":
            continue

        created_ms = data.get("created_ts_ms", int(now * 1000))
        expires_ms = data.get("expires_at_ts_ms", created_ms + 900_000)


        # 生成一次性确认令牌
        token = _confirmation_token(sig_id, user, now)

        tps = [
            TakeProfitItem(price=tp["price"], fraction=tp["fraction"])
            for tp in data.get("take_profits", [])
        ]

        item = PendingSignalItem(
            signal_id=sig_id,
            inst_id=data["inst_id"],
            side=SignalSide(data["side"]),
            action=SignalAction(data["action"]),
            entry_type=OrderType(data.get("entry_type", "limit")),
            price=Decimal(str(data["price"])),
            stop_loss=Decimal(str(data["stop_loss"])) if data.get("stop_loss") else None,
            take_profits=tps,
            reason=data.get("reason", "4H 周期背离触发"),
            strategy_name=data.get("strategy_name", "S1 顺势回调"),
            score=data.get("score", 85.0),
            created_ts_ms=created_ms,
            expires_at_ts_ms=expires_ms,
            confirmation_token=token,
            ai_comment=data.get("ai_comment"),
            ai_action=data.get("ai_action"),
        )
        results.append(item)

    return results


@router.get("/{signal_id}", response_model=PendingSignalItem, summary="获取单个信号详情")
async def get_signal_detail(signal_id: str, user: OptionalUser) -> PendingSignalItem:
    """获取指定信号的详细信息。仅读取，不产生任何交易副作用。"""
    data = _pending_signals_store.get(signal_id) or live_data.pending_signal(signal_id)
    if not data:
        raise NotFoundException(message=f"未找到信号 ID: {signal_id}")

    now = time.time()
    token = _confirmation_token(signal_id, user, now)
    tps = [
        TakeProfitItem(price=tp["price"], fraction=tp["fraction"])
        for tp in data.get("take_profits", [])
    ]

    return PendingSignalItem(
        signal_id=signal_id,
        inst_id=data["inst_id"],
        side=SignalSide(data["side"]),
        action=SignalAction(data["action"]),
        entry_type=OrderType(data.get("entry_type", "limit")),
        price=Decimal(str(data["price"])),
        stop_loss=Decimal(str(data["stop_loss"])) if data.get("stop_loss") else None,
        take_profits=tps,
        reason=data.get("reason", "4H 周期背离触发"),
        strategy_name=data.get("strategy_name", "S1 顺势回调"),
        score=data.get("score", 85.0),
        created_ts_ms=_created_ms(
            data.get("created_at"), data.get("created_ts_ms", int(now * 1000))
        ),
        expires_at_ts_ms=data.get("expires_at_ts_ms", int(now * 1000) + 900_000),
        confirmation_token=token,
        ai_comment=data.get("ai_comment"),
        ai_action=data.get("ai_action"),
    )


@router.post(
    "/{signal_id}/confirm", response_model=ConfirmOrderResponse, summary="二次确认下单 (POST)"
)
async def confirm_signal_order(
    signal_id: str,
    req: ConfirmOrderRequest,
    user: CurrentUser,
) -> ConfirmOrderResponse:
    """手机端 / 控制台二次确认下单。

    安全约束：
    1. 必须使用 POST 请求（禁止 GET 触发生效）。
    2. 校验一次性确认令牌 (Token)，过期或已被使用即刻拦截。
    3. 滑点偏离保护：当前价偏离信号基准价超过最大允许阈值时拒绝执行。
    4. 产生唯一客户订单号 (cl_ord_id) 并记入审计日志。
    """
    data = _pending_signals_store.get(signal_id)
    persisted = False
    if data is None:
        data = live_data.pending_signal(signal_id)
        persisted = data is not None
    if not data:
        raise NotFoundException(message=f"待确认信号不存在或已过期: {signal_id}")

    if data.get("status") != "PENDING":
        raise APIException(
            code="SIGNAL_NOT_PENDING",
            message=f"信号当前状态为 {data.get('status')}，无法重复确认下单",
        )

    # 1. 验证并单次消费令牌
    valid_token = confirmation_token_manager.verify_and_consume(
        token=req.confirmation_token,
        expected_signal_id=signal_id,
    )
    if not valid_token:
        raise APIException(
            code="INVALID_CONFIRMATION_TOKEN",
            message="确认令牌无效、已过期或已被使用，请刷新页面重新获取",
        )

    # 2. 价格偏离滑点校验
    base_price = Decimal(str(data["price"]))
    if req.current_price is not None and base_price > 0:
        price_diff_ratio = abs(req.current_price - base_price) / base_price
        if price_diff_ratio > req.max_slippage_pct:
            max_ratio = float(req.max_slippage_pct)
            cur_ratio = float(price_diff_ratio)
            raise APIException(
                code="SLIPPAGE_EXCEEDED",
                message=(
                    f"当前市场价 {req.current_price} 偏离信号价 {base_price} "
                    f"比例 ({cur_ratio:.2%}) 超出安全阈值 ({max_ratio:.2%})"
                ),
            )

    # 3. 标记信号状态为 CONFIRMED
    if persisted:
        if not live_data.update_signal_status(signal_id, "CONFIRMED"):
            raise APIException("SIGNAL_UPDATE_FAILED", "引擎信号状态更新失败，请重试")
    else:
        data["status"] = "CONFIRMED"
    cl_ord_id = f"ord_{uuid.uuid4().hex[:12]}"
    data["cl_ord_id"] = cl_ord_id

    # 4. 写入审计日志
    await _audit_service.log_action(
        user=user["username"],
        action="SIGNAL_CONFIRM_ORDER",
        before={"signal_id": signal_id, "status": "PENDING"},
        after={
            "signal_id": signal_id,
            "status": "CONFIRMED",
            "cl_ord_id": cl_ord_id,
            "inst_id": data["inst_id"],
            "side": data["side"],
            "price": str(data["price"]),
        },
    )

    # 5. 广播 WebSocket 状态
    await ws_manager.broadcast(
        {
            "type": "SIGNAL_CONFIRMED",
            "signal_id": signal_id,
            "cl_ord_id": cl_ord_id,
            "inst_id": data["inst_id"],
            "side": data["side"],
            "timestamp": int(time.time() * 1000),
        }
    )

    return ConfirmOrderResponse(
        success=True,
        signal_id=signal_id,
        inst_id=data["inst_id"],
        side=data["side"],
        cl_ord_id=cl_ord_id,
        status="CONFIRMED" if persisted else "SUBMITTED",
        message=(
            "信号已确认并记录到引擎状态；当前 API 不直接调用 OMS"
            if persisted
            else "信号已确认，订单已成功提交执行"
        ),
    )


@router.post("/{signal_id}/reject", response_model=StandardSuccessResponse, summary="人工驳回信号")
async def reject_signal(
    signal_id: str,
    req: RejectSignalRequest,
    user: CurrentUser,
) -> StandardSuccessResponse:
    """人工驳回信号。将其标记为 REJECTED。"""
    data = _pending_signals_store.get(signal_id)
    persisted = False
    if data is None:
        data = live_data.pending_signal(signal_id)
        persisted = data is not None
    if not data:
        raise NotFoundException(message=f"信号未找到: {signal_id}")

    if persisted:
        if not live_data.update_signal_status(signal_id, "REJECTED"):
            raise APIException("SIGNAL_UPDATE_FAILED", "引擎信号状态更新失败，请重试")
    else:
        data["status"] = "REJECTED"
        data["reject_reason"] = req.reason

    await _audit_service.log_action(
        user=user["username"],
        action="SIGNAL_REJECT",
        before={"signal_id": signal_id, "status": "PENDING"},
        after={"signal_id": signal_id, "status": "REJECTED", "reason": req.reason},
    )

    await ws_manager.broadcast(
        {
            "type": "SIGNAL_REJECTED",
            "signal_id": signal_id,
            "timestamp": int(time.time() * 1000),
        }
    )

    return StandardSuccessResponse(message=f"信号 {signal_id} 已成功驳回")
