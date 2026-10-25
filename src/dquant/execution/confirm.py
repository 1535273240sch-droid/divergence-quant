"""实盘半自动确认令牌流程。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from decimal import Decimal

from dquant.contracts import OrderAck, OrderRequest, Signal

from .errors import (
    ConfirmationAlreadyUsed,
    InvalidConfirmationToken,
    SlippageExceeded,
    TokenExpired,
)


@dataclass(frozen=True, slots=True)
class ConfirmationTicket:
    """创建确认链接时返回给上层的短期令牌。"""

    signal_id: str
    cl_ord_id: str
    expires_ts_ms: int
    token: str


@dataclass(frozen=True, slots=True)
class ConfirmationRecord:
    """本地确认记录；只保存令牌哈希，不保存原始令牌。"""

    signal_id: str
    request: OrderRequest
    reference_price: Decimal
    token_hash: str
    expires_ts_ms: int
    status: str = "PENDING"
    confirmed_ts_ms: int | None = None
    confirm_price: Decimal | None = None


class ConfirmationService:
    """实现一次性、带签名、可过期的实盘确认。"""

    already_used_error = ConfirmationAlreadyUsed

    def __init__(
        self, *, secret: str | bytes, ttl_sec: int = 900, max_slippage: Decimal = Decimal("0.01")
    ) -> None:
        secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
        if len(secret_bytes) < 16:
            raise ValueError("confirmation secret must contain at least 16 bytes")
        if ttl_sec < 1:
            raise ValueError("ttl_sec must be greater than zero")
        if not 0 <= max_slippage <= 1:
            raise ValueError("max_slippage must be between zero and one")
        self._secret = secret_bytes
        self.ttl_sec = ttl_sec
        self.max_slippage = max_slippage
        self._records: dict[str, ConfirmationRecord] = {}

    def create(
        self, signal: Signal, request: OrderRequest, *, now_ts_ms: int
    ) -> ConfirmationTicket:
        """为实盘开仓信号创建一个待确认令牌。"""

        if request.mode != "live":
            raise ValueError("confirmation is only available for live requests")
        if signal.action.value != "open":
            raise ValueError("only open signals can require confirmation")
        if signal.signal_id in {
            record.signal_id for record in self._records.values() if record.status == "PENDING"
        }:
            raise ConfirmationAlreadyUsed(
                f"pending confirmation already exists: {signal.signal_id}"
            )

        expires_ts_ms = now_ts_ms + self.ttl_sec * 1_000
        nonce = secrets.token_urlsafe(18)
        payload = {
            "signal_id": signal.signal_id,
            "cl_ord_id": request.cl_ord_id,
            "exp": expires_ts_ms,
            "nonce": nonce,
        }
        encoded_payload = _encode_payload(payload)
        signature = _sign(self._secret, encoded_payload)
        token = f"{encoded_payload}.{signature}"
        token_hash = _hash_token(token)
        self._records[token_hash] = ConfirmationRecord(
            signal_id=signal.signal_id,
            request=request,
            reference_price=signal.price,
            token_hash=token_hash,
            expires_ts_ms=expires_ts_ms,
        )
        return ConfirmationTicket(
            signal_id=signal.signal_id,
            cl_ord_id=request.cl_ord_id,
            expires_ts_ms=expires_ts_ms,
            token=token,
        )

    async def confirm(
        self,
        token: str,
        *,
        current_price: Decimal,
        now_ts_ms: int,
        submit: Callable[[OrderRequest], Awaitable[OrderAck]],
    ) -> OrderAck:
        """验证令牌和价格后提交订单；令牌无论成功还是失败都不可再次使用。"""

        record = self._find_record(token)
        if record.status != "PENDING":
            raise ConfirmationAlreadyUsed("confirmation token is no longer pending")
        if now_ts_ms > record.expires_ts_ms:
            self._records[record.token_hash] = replace(record, status="EXPIRED")
            raise TokenExpired("confirmation token has expired")

        reference_price = record.request.price or record.reference_price
        if reference_price is None or reference_price <= 0:
            raise SlippageExceeded("live confirmation requires a positive reference price")
        deviation = abs(current_price - reference_price) / reference_price
        if deviation > self.max_slippage:
            self._records[record.token_hash] = replace(record, status="INVALIDATED")
            raise SlippageExceeded("确认时价格偏离超过配置上限")

        self._records[record.token_hash] = replace(record, status="CONSUMING")
        try:
            ack = await submit(record.request)
        except Exception:
            self._records[record.token_hash] = replace(record, status="FAILED")
            raise
        self._records[record.token_hash] = replace(
            record,
            status="CONFIRMED",
            confirmed_ts_ms=now_ts_ms,
            confirm_price=current_price,
        )
        return ack

    def abandon(self, token: str, *, now_ts_ms: int) -> None:
        """用户明确放弃待确认订单。"""

        record = self._find_record(token)
        if record.status != "PENDING":
            raise ConfirmationAlreadyUsed("confirmation token is no longer pending")
        status = "EXPIRED" if now_ts_ms > record.expires_ts_ms else "ABANDONED"
        self._records[record.token_hash] = replace(record, status=status)

    def expire_pending(self, *, now_ts_ms: int) -> int:
        """批量标记过期令牌，返回本次变更数量。"""

        expired = 0
        for token_hash, record in list(self._records.items()):
            if record.status == "PENDING" and now_ts_ms > record.expires_ts_ms:
                self._records[token_hash] = replace(record, status="EXPIRED")
                expired += 1
        return expired

    def get_record(self, token: str) -> ConfirmationRecord:
        """返回脱敏后的确认记录。"""

        return self._find_record(token)

    def _find_record(self, token: str) -> ConfirmationRecord:
        try:
            encoded_payload, signature = token.split(".", 1)
            expected_signature = _sign(self._secret, encoded_payload)
            if not hmac.compare_digest(signature, expected_signature):
                raise InvalidConfirmationToken("confirmation signature mismatch")
            payload = _decode_payload(encoded_payload)
            if not isinstance(payload, dict):
                raise InvalidConfirmationToken("confirmation payload must be an object")
            token_hash = _hash_token(token)
            record = self._records[token_hash]
        except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise InvalidConfirmationToken("invalid confirmation token") from exc
        if (
            payload.get("signal_id") != record.signal_id
            or payload.get("cl_ord_id") != record.request.cl_ord_id
        ):
            raise InvalidConfirmationToken("confirmation payload does not match stored request")
        if payload.get("exp") != record.expires_ts_ms:
            raise InvalidConfirmationToken("confirmation expiry does not match stored request")
        return record


def _encode_payload(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_payload(encoded_payload: str) -> object:
    padding = "=" * (-len(encoded_payload) % 4)
    raw = base64.urlsafe_b64decode((encoded_payload + padding).encode("ascii"))
    return json.loads(raw.decode("utf-8"))


def _sign(secret: bytes, payload: str) -> str:
    digest = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()
