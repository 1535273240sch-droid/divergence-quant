"""交易所连接自检路由。"""

from __future__ import annotations

import os

from fastapi import APIRouter, Response, status

from dquant.adapters.errors import ExchangeError
from dquant.adapters.okx import OkxAdapter, OkxCredentials, UrllibTransport
from dquant.api.auth import CurrentUser
from dquant.api.errors import APIException
from dquant.api.schemas import ExchangeConnectionStatus, ExchangeConnectionTestRequest
from dquant.security import CredentialStore, CredentialStoreError

router = APIRouter(prefix="/api/v1/exchange", tags=["交易所连接"])
_active_credentials: OkxCredentials | None = None


def _key_hint(value: str) -> str:
    return f"***...{value[-4:]}" if len(value) >= 4 else "******"


def _credentials_from_env() -> OkxCredentials | None:
    values = (
        os.getenv("OKX_DEMO_API_KEY", "").strip(),
        os.getenv("OKX_DEMO_API_SECRET", "").strip(),
        os.getenv("OKX_DEMO_PASSPHRASE", "").strip(),
    )
    return OkxCredentials(*values) if all(values) else None


def _credential_store() -> CredentialStore | None:
    try:
        return CredentialStore()
    except CredentialStoreError:
        # 没有共享主密钥时仍允许一次性连接测试，但不落盘保存凭证。
        return None


def _configured_credentials() -> OkxCredentials | None:
    if _active_credentials is not None:
        return _active_credentials
    env_credentials = _credentials_from_env()
    if env_credentials is not None:
        return env_credentials
    store = _credential_store()
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
            balance = (await adapter.fetch_balance()).total_equity_usd
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


@router.get("/status", response_model=ExchangeConnectionStatus, summary="检查当前 OKX 配置")
async def exchange_status(user: CurrentUser) -> ExchangeConnectionStatus:
    del user
    return await _check(_configured_credentials())


@router.post("/test", response_model=ExchangeConnectionStatus, summary="测试 OKX 密钥")
async def test_exchange_connection(
    req: ExchangeConnectionTestRequest,
    user: CurrentUser,
) -> ExchangeConnectionStatus:
    del user
    credentials = OkxCredentials(
        api_key=req.api_key,
        secret=req.api_secret,
        passphrase=req.passphrase,
    )
    global _active_credentials
    result = await _check(credentials)
    if result.private_api:
        # 只在当前 API 进程内保留凭证，永不写入响应、日志或仓库。
        _active_credentials = credentials
        store = _credential_store()
        if store is not None:
            try:
                store.save(credentials)
            except CredentialStoreError as exc:
                raise APIException("CREDENTIAL_STORE_ERROR", str(exc), 503) from exc
    return result


@router.delete("/status", status_code=status.HTTP_204_NO_CONTENT, summary="断开当前 OKX 私有连接")
async def disconnect_exchange(user: CurrentUser) -> Response:
    """清除当前 API 进程内存中的临时凭证；服务器环境变量不受影响。"""

    del user
    global _active_credentials
    _active_credentials = None
    store = _credential_store()
    if store is not None:
        try:
            store.clear()
        except CredentialStoreError as exc:
            raise APIException("CREDENTIAL_STORE_ERROR", str(exc), 503) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
