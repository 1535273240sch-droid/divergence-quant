"""OpenAI 兼容模型网关。

网关只负责模型请求、备用路由、预算、调用账本和幂等缓存；它不直接产生交易动作，
也不把密钥写入日志。网络传输采用标准库实现，测试可以注入无网络的 transport。
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import uuid4

from .errors import (
    AIBudgetExceeded,
    AIConfigurationError,
    AIGatewayError,
    AIGatewayTimeout,
    AIResponseError,
)


class JSONTransport(Protocol):
    """OpenAI 兼容 JSON POST 传输协议。"""

    async def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_sec: float,
    ) -> dict[str, object]:
        """向模型服务发送一次 JSON 请求。"""

        ...


class CallLogSink(Protocol):
    """调用账本持久化边界，由 M01/M11 注入数据库实现。"""

    def record(self, record: AICallRecord) -> None:
        """保存一条脱敏调用记录。"""

        ...


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """一个模型槽位的主路由和备用路由。"""

    provider: str
    base_url: str
    model: str
    api_key: str | None = None
    prompt_cost_per_1k: Decimal = Decimal("0")
    completion_cost_per_1k: Decimal = Decimal("0")
    fallback_provider: str | None = None
    fallback_base_url: str | None = None
    fallback_model: str | None = None
    fallback_api_key: str | None = None
    fallback_prompt_cost_per_1k: Decimal | None = None
    fallback_completion_cost_per_1k: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise AIConfigurationError("provider and model must not be empty")
        if not self.base_url.startswith(("http://", "https://")):
            raise AIConfigurationError("base_url must be an HTTP(S) URL")
        if self.prompt_cost_per_1k < 0 or self.completion_cost_per_1k < 0:
            raise AIConfigurationError("model costs must not be negative")
        has_fallback = any(
            value is not None
            for value in (self.fallback_provider, self.fallback_base_url, self.fallback_model)
        )
        if has_fallback and not (
            self.fallback_provider and self.fallback_base_url and self.fallback_model
        ):
            raise AIConfigurationError("fallback route requires provider, base_url and model")
        if self.fallback_base_url and not self.fallback_base_url.startswith(
            ("http://", "https://")
        ):
            raise AIConfigurationError("fallback_base_url must be an HTTP(S) URL")
        if self.fallback_prompt_cost_per_1k is not None and self.fallback_prompt_cost_per_1k < 0:
            raise AIConfigurationError("fallback prompt cost must not be negative")
        if (
            self.fallback_completion_cost_per_1k is not None
            and self.fallback_completion_cost_per_1k < 0
        ):
            raise AIConfigurationError("fallback completion cost must not be negative")


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """AI 网关运行配置。"""

    enabled: bool = True
    timeout_sec: float = 12.0
    daily_budget_usd: Decimal = Decimal("1.0")
    permission_mode: str = "suggest_and_veto"
    routes: Mapping[str, ModelRoute] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> GatewayConfig:
        """从计划书中的 ``ai`` 配置片段创建网关配置。"""

        models_raw = raw.get("models", {})
        if not isinstance(models_raw, Mapping):
            raise AIConfigurationError("ai.models must be a mapping")
        routes: dict[str, ModelRoute] = {}
        for name, value in models_raw.items():
            if not isinstance(name, str) or not isinstance(value, Mapping):
                raise AIConfigurationError("each ai model route must be a named mapping")
            routes[name] = _route_from_mapping(value)
        timeout = raw.get("timeout_sec", 12)
        budget = raw.get("daily_budget_usd", 1.0)
        permission_mode = raw.get("permission_mode", "suggest_and_veto")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise AIConfigurationError("timeout_sec must be greater than zero")
        if not isinstance(permission_mode, str) or not permission_mode:
            raise AIConfigurationError("permission_mode must not be empty")
        try:
            budget_decimal = Decimal(str(budget))
        except (ArithmeticError, ValueError) as exc:
            raise AIConfigurationError("daily_budget_usd must be numeric") from exc
        if budget_decimal < 0:
            raise AIConfigurationError("daily_budget_usd must not be negative")
        return cls(
            enabled=bool(raw.get("enabled", True)),
            timeout_sec=float(timeout),
            daily_budget_usd=budget_decimal,
            permission_mode=permission_mode,
            routes=routes,
        )


@dataclass(frozen=True, slots=True)
class GatewayResponse:
    """模型网关的脱敏响应摘要。"""

    content: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    latency_ms: int
    used_fallback: bool


@dataclass(frozen=True, slots=True)
class AICallRecord:
    """可落库的模型调用账本记录。"""

    call_id: str
    agent_name: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    latency_ms: int
    status: str
    ts_ms: int
    provider: str
    used_fallback: bool = False

    def model_dump(self) -> dict[str, object]:
        """输出不包含 API Key 或原始 prompt 的账本字段。"""

        return {
            "id": self.call_id,
            "agent_name": self.agent_name,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": str(self.cost_usd),
            "latency_ms": self.latency_ms,
            "status": self.status,
            "ts_ms": self.ts_ms,
            "provider": self.provider,
            "used_fallback": self.used_fallback,
        }


class UrllibJSONTransport:
    """基于标准库的 OpenAI 兼容 JSON 传输。"""

    async def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_sec: float,
    ) -> dict[str, object]:
        return await asyncio.to_thread(self._post_json, url, headers, payload, timeout_sec)

    @staticmethod
    def _post_json(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_sec: float,
    ) -> dict[str, object]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise AIGatewayTimeout("AI request timed out") from exc
        except urllib.error.HTTPError as exc:
            message = f"AI provider returned HTTP {exc.code}"
            if exc.code in {408, 429} or exc.code >= 500:
                raise AIGatewayError(message) from exc
            raise AIResponseError(message) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AIGatewayError("AI provider network request failed") from exc
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise AIResponseError("AI provider returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise AIResponseError("AI provider response must be a JSON object")
        return decoded


class OpenAICompatibleGateway:
    """统一调用四个 Agent 路由槽位的网关。"""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        transport: JSONTransport | None = None,
        now_ms: Callable[[], int] | None = None,
        call_log_sink: CallLogSink | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibJSONTransport()
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._call_log_sink = call_log_sink
        self._call_logs: list[AICallRecord] = []
        self._idempotent: dict[str, GatewayResponse] = {}

    @property
    def call_logs(self) -> tuple[AICallRecord, ...]:
        """返回只读的调用账本视图。"""

        return tuple(self._call_logs)

    def spend_for_day(self, ts_ms: int | None = None) -> Decimal:
        """计算指定 UTC 自然日的成功调用费用。"""

        target_day = _utc_day(self._now_ms() if ts_ms is None else ts_ms)
        return sum(
            (record.cost_usd for record in self._call_logs if _utc_day(record.ts_ms) == target_day),
            Decimal("0"),
        )

    async def complete(
        self,
        agent_name: str,
        messages: Sequence[Mapping[str, object]],
        *,
        idempotency_key: str | None = None,
        response_format: Mapping[str, object] | None = None,
    ) -> GatewayResponse:
        """调用指定 Agent，并在主路由失败时尝试备用路由。"""

        if idempotency_key and idempotency_key in self._idempotent:
            return self._idempotent[idempotency_key]
        if not self.config.enabled:
            raise AIGatewayError("AI gateway is disabled")
        route = self.config.routes.get(agent_name)
        if route is None:
            raise AIConfigurationError(f"unknown AI route: {agent_name}")
        _validate_messages(messages)
        self._ensure_budget(agent_name, route.model)
        payload: dict[str, object] = {
            "model": route.model,
            "messages": [dict(message) for message in messages],
        }
        if response_format is not None:
            payload["response_format"] = dict(response_format)

        try:
            response = await self._call_route(
                agent_name,
                route,
                payload,
                used_fallback=False,
            )
        except (AIGatewayError, TimeoutError) as primary_exc:
            if not route.fallback_model:
                raise primary_exc
            fallback_route = _fallback_route(route)
            self._ensure_budget(agent_name, fallback_route.model)
            fallback_payload = dict(payload)
            fallback_payload["model"] = fallback_route.model
            try:
                response = await self._call_route(
                    agent_name,
                    fallback_route,
                    fallback_payload,
                    used_fallback=True,
                )
            except (AIGatewayError, TimeoutError) as fallback_exc:
                raise AIGatewayError("primary and fallback AI routes failed") from fallback_exc
            del primary_exc

        if idempotency_key:
            self._idempotent[idempotency_key] = response
        return response

    async def _call_route(
        self,
        agent_name: str,
        route: ModelRoute,
        payload: dict[str, object],
        *,
        used_fallback: bool,
    ) -> GatewayResponse:
        started = time.monotonic()
        ts_ms = self._now_ms()
        try:
            raw = await self.transport.post_json(
                _completion_url(route.base_url),
                _headers(route.api_key),
                payload,
                self.config.timeout_sec,
            )
        except TimeoutError as exc:
            latency_ms = _elapsed_ms(started)
            self._append_log(
                agent_name,
                route,
                status="TIMEOUT",
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=Decimal("0"),
                latency_ms=latency_ms,
                ts_ms=ts_ms,
                used_fallback=used_fallback,
            )
            raise AIGatewayTimeout("AI request timed out") from exc
        except AIGatewayError as exc:
            latency_ms = _elapsed_ms(started)
            self._append_log(
                agent_name,
                route,
                status="FAILED",
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=Decimal("0"),
                latency_ms=latency_ms,
                ts_ms=ts_ms,
                used_fallback=used_fallback,
            )
            raise exc
        except Exception as exc:
            latency_ms = _elapsed_ms(started)
            self._append_log(
                agent_name,
                route,
                status="FAILED",
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=Decimal("0"),
                latency_ms=latency_ms,
                ts_ms=ts_ms,
                used_fallback=used_fallback,
            )
            raise AIGatewayError("AI transport failed") from exc

        content, prompt_tokens, completion_tokens, model = _parse_response(raw)
        cost = _calculate_cost(route, prompt_tokens, completion_tokens, used_fallback)
        latency_ms = _elapsed_ms(started)
        self._append_log(
            agent_name,
            route,
            status="SUCCESS",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            ts_ms=ts_ms,
            used_fallback=used_fallback,
            model=model,
        )
        return GatewayResponse(
            content=content,
            model=model,
            provider=route.provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            used_fallback=used_fallback,
        )

    def _ensure_budget(self, agent_name: str, model: str) -> None:
        current = self.spend_for_day()
        if current >= self.config.daily_budget_usd:
            record = AICallRecord(
                call_id=str(uuid4()),
                agent_name=agent_name,
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=Decimal("0"),
                latency_ms=0,
                status="BUDGET_EXCEEDED",
                ts_ms=self._now_ms(),
                provider="gateway",
            )
            self._store_log(record)
            raise AIBudgetExceeded("daily AI budget has been exhausted")

    def _append_log(
        self,
        agent_name: str,
        route: ModelRoute,
        *,
        status: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: Decimal,
        latency_ms: int,
        ts_ms: int,
        used_fallback: bool,
        model: str | None = None,
    ) -> None:
        provider = route.fallback_provider if used_fallback else route.provider
        self._store_log(
            AICallRecord(
                call_id=str(uuid4()),
                agent_name=agent_name,
                model=(
                    model or (route.fallback_model if used_fallback else route.model) or route.model
                ),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                status=status,
                ts_ms=ts_ms,
                provider=provider or route.provider,
                used_fallback=used_fallback,
            )
        )

    def _store_log(self, record: AICallRecord) -> None:
        self._call_logs.append(record)
        if self._call_log_sink is not None:
            self._call_log_sink.record(record)


def _route_from_mapping(raw: Mapping[str, object]) -> ModelRoute:
    def optional_decimal(name: str) -> Decimal | None:
        value = raw.get(name)
        return None if value is None else Decimal(str(value))

    fallback_provider = _optional_string(raw.get("fallback_provider"))
    fallback_base_url = _optional_string(raw.get("fallback_base_url"))
    fallback_model = _optional_string(raw.get("fallback_model"))
    if not fallback_base_url or not fallback_model:
        fallback_provider = None
        fallback_base_url = None
        fallback_model = None

    return ModelRoute(
        provider=_required_string(raw, "provider"),
        base_url=_required_string(raw, "base_url"),
        model=_required_string(raw, "model"),
        api_key=_optional_string(raw.get("api_key")),
        prompt_cost_per_1k=Decimal(str(raw.get("prompt_cost_per_1k", 0))),
        completion_cost_per_1k=Decimal(str(raw.get("completion_cost_per_1k", 0))),
        fallback_provider=fallback_provider,
        fallback_base_url=fallback_base_url,
        fallback_model=fallback_model,
        fallback_api_key=_optional_string(raw.get("fallback_api_key")),
        fallback_prompt_cost_per_1k=optional_decimal("fallback_prompt_cost_per_1k"),
        fallback_completion_cost_per_1k=optional_decimal("fallback_completion_cost_per_1k"),
    )


def _fallback_route(route: ModelRoute) -> ModelRoute:
    return ModelRoute(
        provider=route.fallback_provider or "fallback",
        base_url=route.fallback_base_url or route.base_url,
        model=route.fallback_model or route.model,
        api_key=route.fallback_api_key,
        prompt_cost_per_1k=route.fallback_prompt_cost_per_1k or Decimal("0"),
        completion_cost_per_1k=route.fallback_completion_cost_per_1k or Decimal("0"),
    )


def _required_string(raw: Mapping[str, object], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise AIConfigurationError(f"{name} must be a non-empty string")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AIConfigurationError("optional route values must be non-empty strings")
    if value.startswith("${") and value.endswith("}"):
        return None
    return value


def _validate_messages(messages: Sequence[Mapping[str, object]]) -> None:
    if not messages:
        raise AIResponseError("messages must not be empty")
    for message in messages:
        if not isinstance(message.get("role"), str) or not message["role"]:
            raise AIResponseError("each message must include a role")
        if "content" not in message:
            raise AIResponseError("each message must include content")


def _parse_response(raw: Mapping[str, object]) -> tuple[str, int, int, str]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AIResponseError("AI response choices are missing")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise AIResponseError("AI response choice is invalid")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise AIResponseError("AI response message is missing")
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, Mapping) and isinstance(part.get("text", ""), str)
        )
    else:
        raise AIResponseError("AI response content is invalid")
    if not text.strip():
        raise AIResponseError("AI response content is empty")
    usage = raw.get("usage", {})
    if not isinstance(usage, Mapping):
        usage = {}
    prompt_tokens = _nonnegative_int(usage.get("prompt_tokens", 0), "prompt_tokens")
    completion_tokens = _nonnegative_int(usage.get("completion_tokens", 0), "completion_tokens")
    model = raw.get("model", "unknown")
    if not isinstance(model, str) or not model:
        model = "unknown"
    return text, prompt_tokens, completion_tokens, model


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AIResponseError(f"{name} must be a non-negative integer")
    return value


def _calculate_cost(
    route: ModelRoute,
    prompt_tokens: int,
    completion_tokens: int,
    used_fallback: bool,
) -> Decimal:
    del used_fallback
    prompt_rate = route.prompt_cost_per_1k
    completion_rate = route.completion_cost_per_1k
    return (
        Decimal(prompt_tokens) * (prompt_rate or Decimal("0")) / Decimal(1000)
        + Decimal(completion_tokens) * (completion_rate or Decimal("0")) / Decimal(1000)
    ).quantize(Decimal("0.00000001"))


def _completion_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _utc_day(ts_ms: int) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).date()
