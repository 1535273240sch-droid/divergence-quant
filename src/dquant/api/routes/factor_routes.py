"""因子注册、预览与求值路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field

from dquant.api import factors
from dquant.api.auth import CurrentUser
from dquant.api.errors import APIException

router = APIRouter(prefix="/api/v1/factors", tags=["因子"])

FIELDS = sorted(factors.SERIES_FIELDS)
FUNCTIONS = [
    "ma(x, n)",
    "ema(x, n)",
    "std(x, n)",
    "zscore(x, n)",
    "rank(x, n)",
    "roc(x, n)",
    "rsi(x, n)",
    "delay(x, n)",
    "abs(x)",
    "log(x)",
    "sqrt(x)",
    "maximum(a, b)",
    "minimum(a, b)",
    "macd(x, fast, slow)",
    "corr(a, b, n)",
    "atr(high, low, close, n)",
]


class FactorCreate(BaseModel):
    name: str = Field(min_length=2, max_length=40)
    formula: str = Field(min_length=1, max_length=factors.MAX_FORMULA_LEN)
    weight: float = Field(default=0.5, ge=-1.0, le=1.0)


class FactorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=40)
    formula: str | None = Field(default=None, min_length=1, max_length=factors.MAX_FORMULA_LEN)
    weight: float | None = Field(default=None, ge=-1.0, le=1.0)
    enabled: bool | None = None


class PreviewRequest(BaseModel):
    formula: str = Field(min_length=1, max_length=factors.MAX_FORMULA_LEN)
    inst_id: str = "BTC-USDT-SWAP"
    bar: str = "4H"
    limit: int = Field(default=300, ge=50, le=1000)


def _bad(exc: Exception) -> APIException:
    return APIException("INVALID_FACTOR", str(exc), 400)


@router.get("/meta", summary="因子表达式支持的字段与函数")
async def factor_meta() -> dict[str, Any]:
    return {"fields": FIELDS, "functions": FUNCTIONS, "max_window": factors.MAX_WINDOW}


@router.get("", summary="因子列表")
async def list_factors(enabled_only: bool = Query(False)) -> dict[str, Any]:
    rows = factors.list_factors(enabled_only=enabled_only)
    return {"count": len(rows), "factors": rows}


@router.post("", summary="注册因子")
async def create_factor(req: FactorCreate, user: CurrentUser) -> dict[str, Any]:
    try:
        return factors.create_factor(req.name, req.formula, req.weight, user["username"])
    except factors.FactorError as exc:
        raise _bad(exc) from exc


@router.post("/preview", summary="预览因子在指定标的上的值")
async def preview_factor(req: PreviewRequest, user: CurrentUser) -> dict[str, Any]:
    del user
    if req.bar not in {"15m", "1H", "4H", "1D"}:
        raise APIException("INVALID_PARAM", f"不支持的周期: {req.bar}", 400)
    try:
        return factors.preview_factor(req.formula, req.inst_id, req.bar, req.limit)
    except factors.FactorError as exc:
        raise _bad(exc) from exc


@router.get("/evaluate", summary="按启用因子计算某标的的综合分")
async def evaluate_factors(
    inst_id: str = Query("BTC-USDT-SWAP"),
    bar: str = Query("4H"),
) -> dict[str, Any]:
    if bar not in {"15m", "1H", "4H", "1D"}:
        raise APIException("INVALID_PARAM", f"不支持的周期: {bar}", 400)
    try:
        return factors.evaluate_enabled(inst_id, bar)
    except factors.FactorError as exc:
        raise _bad(exc) from exc


@router.put("/{factor_id}", summary="修改因子")
async def update_factor(
    factor_id: int, req: FactorUpdate, user: CurrentUser
) -> dict[str, Any]:
    del user
    try:
        updated = factors.update_factor(
            factor_id,
            name=req.name,
            formula=req.formula,
            weight=req.weight,
            enabled=req.enabled,
        )
    except factors.FactorError as exc:
        raise _bad(exc) from exc
    if updated is None:
        raise APIException("NOT_FOUND", "因子不存在", 404)
    return updated


@router.delete(
    "/{factor_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除因子"
)
async def delete_factor(factor_id: int, user: CurrentUser) -> Response:
    del user
    if not factors.delete_factor(factor_id):
        raise APIException("NOT_FOUND", "因子不存在", 404)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
