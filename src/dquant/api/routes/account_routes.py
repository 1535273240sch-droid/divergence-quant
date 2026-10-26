"""当前登录用户的私有账户数据（按 JWT 隔离）。

只读取当前用户自己的加密凭证，绝不接收用户名/用户 ID 参数。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from dquant.adapters.errors import ExchangeError
from dquant.adapters.okx import OkxAdapter, UrllibTransport
from dquant.api.auth import CurrentUser
from dquant.api.errors import APIException
from dquant.api.routes.exchange_routes import load_user_credentials

router = APIRouter(prefix="/api/v1/account", tags=["账户"])


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


@router.get("/summary", summary="当前用户的账户概览（余额 + 持仓）")
async def account_summary(user: CurrentUser) -> dict[str, Any]:
    credentials = load_user_credentials(user["username"])
    if credentials is None:
        return {
            "connected": False,
            "message": "尚未绑定交易所密钥",
            "balance_usdt": None,
            "available_usdt": None,
            "positions": [],
        }

    transport = UrllibTransport("https://www.okx.com", credentials, simulated_trading=True)
    adapter = OkxAdapter(transport, mode="demo")
    try:
        balance = await adapter.fetch_balance()
    except ExchangeError as exc:
        raise APIException("EXCHANGE_UNAVAILABLE", f"OKX 账户读取失败: {exc.msg}", 502) from exc

    warning = None
    positions: list[dict[str, Any]] = []
    try:
        for p in await adapter.fetch_positions():
            positions.append(
                {
                    "inst_id": p.inst_id,
                    "pos_side": _enum_value(p.pos_side),
                    "size": float(p.size),
                    "avg_price": float(p.avg_price),
                    "mark_price": float(p.mark_price),
                    "upl": float(p.upl),
                    "stop_loss": float(p.stop_loss) if p.stop_loss is not None else None,
                    "strategy": p.strategy,
                }
            )
    except ExchangeError as exc:
        warning = f"余额已读取，但持仓读取失败: {exc.msg}"

    usdt = balance.details.get("USDT")
    return {
        "connected": True,
        "message": warning or "OKX 模拟盘账户已连接",
        "balance_usdt": float(balance.total_equity_usd),
        "available_usdt": float(usdt.available) if usdt is not None else None,
        "positions": positions,
    }
