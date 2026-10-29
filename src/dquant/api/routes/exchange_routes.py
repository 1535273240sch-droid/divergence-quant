"""交易所连接自检路由（按登录用户隔离）。

每个用户的 OKX 凭证单独加密落盘：
``data/credentials/<sha256(username)[:32]>.enc``。
任何接口都不返回密钥明文，也不存在跨用户的全局凭证缓存。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Response, status

from dquant.adapters.errors import ExchangeError
from dquant.adapters.okx import OkxAdapter, OkxCredentials, UrllibTransport
from dquant.api import users as user_store
from dquant.api.auth import CurrentUser
from dquant.api.config import load_api_settings
from dquant.api.errors import APIException, UnauthorizedException
from dquant.api.schemas import ExchangeConnectionStatus, ExchangeConnectionTestRequest
from dquant.security import CredentialStore, CredentialStoreError

router = APIRouter(prefix="/api/v1/exchange", tags=["交易所连接"])
_settings = load_api_settings()


def ensure_active_user(username: str) -> None:
    """JWT 可能比账号活得久：访问私有数据前确认账号仍然存在。"""

    if username == _settings.admin_username:
        return
    if user_store.get_user(username) is None:
        raise UnauthorizedException(message="账户不存在或已停用，请重新登录")


def _key_hint(value: str) -> str:
    return f"***...{value[-4:]}" if len(value) >= 4 else "******"


def credential_path_for(username: str) -> Path:
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()[:32]
    return Path.cwd() / "data" / "credentials" / f"{digest}.enc"


def user_credential_store(username: str) -> CredentialStore | None:
    try:
        return CredentialStore(path=credential_path_for(username))
    except CredentialStoreError:
        # 缺少共享主密钥时不落盘，也不允许持久化凭证。
        return None


def load_user_credentials(username: str) -> OkxCredentials | None:
    ensure_active_user(username)
    store = user_credential_store(username)
    if store is None:
        return None
    try:
        return store.load()
    except CredentialStoreError as exc:
        raise APIException("CREDENTIAL_STORE_ERROR", str(exc), 503) from exc


async def _check(credentials: OkxCredentials | None) -> ExchangeConnectionStatus:
    transport = UrllibTransport("https://www.okx.com", credentials, simulated_trading=True)
    adapter = OkxAdapter(transport, mode="demo")
    try:
        offset = await adapter.sync_time()
    except ExchangeError as exc:
        raise APIException("EXCHANGE_UNAVAILABLE", f"OKX 公共接口连接失败: {exc.msg}", 502) from exc

    balance = None
    private_api = False
    message = "OKX 公共行情接口已连接，未配置私有 API 密钥"
    if credentials is not None:
        try:
            balance_obj = await adapter.fetch_balance()
            usdt = balance_obj.details.get("USDT")
            balance = usdt.equity if usdt is not None else balance_obj.total_equity_usd
            private_api = True
            message = "OKX 模拟盘公共与私有接口均已连接"
        except ExchangeError as exc:
            message = f"公共接口已连接，但私有接口验证失败: {exc.msg}"

    return ExchangeConnectionStatus(
        public_api=True,
        private_api=private_api,
        time_offset_ms=offset,
        api_key_hint=_key_hint(credentials.api_key) if credentials else None,
        balance_usdt=balance,
        message=message,
    )


@router.get("/status", response_model=ExchangeConnectionStatus, summary="检查当前用户的 OKX 配置")
async def exchange_status(user: CurrentUser) -> ExchangeConnectionStatus:
    return await _check(load_user_credentials(user["username"]))


@router.post(
    "/test",
    response_model=ExchangeConnectionStatus,
    summary="测试并保存当前用户的 OKX 密钥",
)
async def test_exchange_connection(
    req: ExchangeConnectionTestRequest,
    user: CurrentUser,
) -> ExchangeConnectionStatus:
    credentials = OkxCredentials(
        api_key=req.api_key,
        secret=req.api_secret,
        passphrase=req.passphrase,
    )
    result = await _check(credentials)
    if result.private_api:
        store = user_credential_store(user["username"])
        if store is None:
            raise APIException(
                "CREDENTIAL_STORE_ERROR",
                "服务端缺少加密主密钥，无法安全保存凭证",
                503,
            )
        try:
            store.save(credentials)
        except CredentialStoreError as exc:
            raise APIException("CREDENTIAL_STORE_ERROR", str(exc), 503) from exc
    return result


@router.delete(
    "/status", status_code=status.HTTP_204_NO_CONTENT, summary="断开当前用户的 OKX 私有连接"
)
async def disconnect_exchange(user: CurrentUser) -> Response:
    """删除当前登录用户自己的加密凭证文件，不影响其他用户。"""

    store = user_credential_store(user["username"])
    if store is not None:
        try:
            store.clear()
        except CredentialStoreError as exc:
            raise APIException("CREDENTIAL_STORE_ERROR", str(exc), 503) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
