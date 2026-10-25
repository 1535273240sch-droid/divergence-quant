"""API 统一错误响应与异常处理机制。

格式规范：
{
  "code": "ERR_CODE",
  "message": "错误描述",
  "detail": null | dict,
  "timestamp": 1729814400000
}
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class APIException(Exception):
    """API 基础业务异常。"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail


class UnauthorizedException(APIException):
    """未认证或凭证无效异常 (401)。"""

    def __init__(self, message: str = "未提供认证凭证或令牌已过期", detail: Any = None) -> None:
        super().__init__(
            code="UNAUTHORIZED",
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
        )


class ForbiddenException(APIException):
    """无权限操作异常 (403)。"""

    def __init__(self, message: str = "无权限执行此操作", detail: Any = None) -> None:
        super().__init__(
            code="FORBIDDEN",
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


class NotFoundException(APIException):
    """资源不存在异常 (404)。"""

    def __init__(self, message: str = "请求的资源未找到", detail: Any = None) -> None:
        super().__init__(
            code="NOT_FOUND",
            message=message,
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        )


class AccountLockedException(APIException):
    """账号被安全锁定异常 (423 / 429)。"""

    def __init__(
        self, message: str = "密码连续错误次数过多，账号已被锁定 15 分钟", detail: Any = None
    ) -> None:
        super().__init__(
            code="ACCOUNT_LOCKED",
            message=message,
            status_code=status.HTTP_423_LOCKED,
            detail=detail,
        )


def make_error_payload(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    """生成统一错误响应字典。"""
    return {
        "code": code,
        "message": message,
        "detail": detail,
        "timestamp": int(time.time() * 1000),
    }


async def api_exception_handler(request: Request, exc: APIException) -> JSONResponse:
    """处理 APIException。"""
    payload = make_error_payload(code=exc.code, message=exc.message, detail=exc.detail)
    return JSONResponse(status_code=exc.status_code, content=payload)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理 Pydantic 参数校验异常。"""
    payload = make_error_payload(
        code="VALIDATION_ERROR",
        message="请求参数验证失败",
        detail=exc.errors(),
    )
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=payload)


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理未捕获的全局异常。"""
    payload = make_error_payload(
        code="INTERNAL_SERVER_ERROR",
        message="服务器内部错误",
        detail=str(exc) if not isinstance(exc, SystemExit) else "Server exiting",
    )
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=payload)
