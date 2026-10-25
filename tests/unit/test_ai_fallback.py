import asyncio
import json
from decimal import Decimal

import pytest

from dquant.ai import (
    AIAction,
    AIBudgetExceeded,
    AIResponseError,
    BriefingAgent,
    GatewayConfig,
    InMemoryParameterStore,
    NewsMacroSentinel,
    OpenAICompatibleGateway,
    StrategyEvolutionAgent,
    VisualAuditor,
)
from dquant.ai.news_collector import NewsItem
from dquant.contracts import (
    DivergenceKind,
    OrderType,
    RegimeState,
    ScreenerCandidate,
    Signal,
    SignalAction,
    SignalSide,
)


class FakeTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_sec: float,
    ) -> dict[str, object]:
        self.calls.append({"url": url, "headers": headers, "payload": payload})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, dict)
        return response


def _response(content: str, model: str = "model", prompt_tokens: int = 100) -> dict[str, object]:
    return {
        "model": model,
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 100},
    }


def _gateway(transport: FakeTransport, *, budget: float = 1.0) -> OpenAICompatibleGateway:
    config = GatewayConfig.from_mapping(
        {
            "enabled": True,
            "timeout_sec": 12,
            "daily_budget_usd": budget,
            "models": {
                "vision": {
                    "provider": "primary-provider",
                    "base_url": "https://primary.example/v1",
                    "model": "primary-model",
                    "api_key": "primary-secret",
                    "prompt_cost_per_1k": 1.0,
                    "completion_cost_per_1k": 1.0,
                    "fallback_provider": "backup-provider",
                    "fallback_base_url": "https://backup.example/v1",
                    "fallback_model": "backup-model",
                    "fallback_api_key": "backup-secret",
                },
                "news": {
                    "provider": "primary-provider",
                    "base_url": "https://primary.example/v1",
                    "model": "primary-model",
                    "api_key": "primary-secret",
                },
                "briefing": {
                    "provider": "primary-provider",
                    "base_url": "https://primary.example/v1",
                    "model": "primary-model",
                    "api_key": "primary-secret",
                },
                "reasoning": {
                    "provider": "primary-provider",
                    "base_url": "https://primary.example/v1",
                    "model": "primary-model",
                    "api_key": "primary-secret",
                },
            },
        }
    )
    return OpenAICompatibleGateway(config, transport=transport, now_ms=lambda: 1_700_000_000_000)


def _candidate() -> ScreenerCandidate:
    return ScreenerCandidate(
        inst_id="BTC-USDT-SWAP",
        pool="A",
        regime=RegimeState.UPTREND,
        divergence_id="div-1",
        divergence_kind=DivergenceKind.BULL_REGULAR,
        score=80,
        rank=1,
        updated_ts_ms=1_700_000_000_000,
    )


def _signal() -> Signal:
    return Signal(
        signal_id="sig-1",
        inst_id="BTC-USDT-SWAP",
        side=SignalSide.LONG,
        action=SignalAction.OPEN,
        entry_type=OrderType.LIMIT,
        price=Decimal("100"),
        stop_loss=Decimal("95"),
        reason="4H 底背离后突破触发位",
        strategy_name="S1",
        created_ts_ms=1_700_000_000_000,
    )


def _news_item(
    source_name: str,
    *,
    official: bool = False,
    url: str | None = None,
) -> NewsItem:
    return NewsItem(
        item_id=f"news-{source_name}",
        title="项目存在重大安全事件",
        source_name=source_name,
        url=url or f"https://{source_name}.example/article",
        published_ts_ms=1_699_999_900_000,
        fetched_ts_ms=1_700_000_000_000,
        credibility=0.95 if official else 0.8,
        official=official,
        inst_id="BTC-USDT-SWAP",
    )


def test_gateway_uses_fallback_and_keeps_secret_out_of_call_log() -> None:
    transport = FakeTransport(
        [TimeoutError("primary timeout"), _response('{"ok": true}', "backup-model")]
    )
    gateway = _gateway(transport)

    result = asyncio.run(
        gateway.complete(
            "vision",
            [{"role": "user", "content": "审阅图表"}],
            idempotency_key="visual:BTC-USDT-SWAP:1",
        )
    )

    assert result.used_fallback is True
    assert result.model == "backup-model"
    assert [record.status for record in gateway.call_logs] == ["TIMEOUT", "SUCCESS"]
    serialized = json.dumps(
        [record.model_dump() for record in gateway.call_logs], ensure_ascii=False
    )
    assert "primary-secret" not in serialized
    assert "backup-secret" not in serialized


def test_gateway_budget_blocks_new_call_after_successful_spend() -> None:
    transport = FakeTransport([_response("{}", prompt_tokens=400), _response("{}")])
    gateway = _gateway(transport, budget=0.5)

    asyncio.run(gateway.complete("vision", [{"role": "user", "content": "一次调用"}]))
    with pytest.raises(AIBudgetExceeded):
        asyncio.run(gateway.complete("vision", [{"role": "user", "content": "二次调用"}]))

    assert len(transport.calls) == 1


def test_gateway_idempotency_returns_cached_response_without_repeating_request() -> None:
    transport = FakeTransport([_response('{"value": 1}')])
    gateway = _gateway(transport)
    messages = [{"role": "user", "content": "相同请求"}]

    first = asyncio.run(gateway.complete("vision", messages, idempotency_key="same-request"))
    second = asyncio.run(gateway.complete("vision", messages, idempotency_key="same-request"))

    assert first == second
    assert len(transport.calls) == 1


def test_gateway_rejects_empty_messages_and_visual_input() -> None:
    gateway = _gateway(FakeTransport([]))
    with pytest.raises(AIResponseError, match="messages must not be empty"):
        asyncio.run(gateway.complete("vision", []))

    with pytest.raises(ValueError, match="chart_png must not be empty"):
        asyncio.run(
            VisualAuditor(gateway).review(
                _candidate(),
                chart_png=b"",
                indicators={"area_ratio": 0.5},
                now_ts_ms=1_700_000_000_000,
            )
        )


def test_gateway_accepts_unresolved_optional_fallback_placeholders() -> None:
    config = GatewayConfig.from_mapping(
        {
            "models": {
                "vision": {
                    "provider": "primary",
                    "base_url": "https://primary.example/v1",
                    "model": "vision-model",
                    "fallback_provider": "configurable",
                    "fallback_base_url": "${AI_VISION_FALLBACK_BASE_URL}",
                    "fallback_model": "${AI_VISION_FALLBACK_MODEL}",
                }
            }
        }
    )

    assert config.routes["vision"].fallback_model is None


def test_visual_auditor_invalid_structured_output_degrades_to_math_result() -> None:
    transport = FakeTransport([_response("not-json")])
    gateway = _gateway(transport)
    auditor = VisualAuditor(gateway)

    review = asyncio.run(
        auditor.review(
            _candidate(),
            chart_png=b"png",
            indicators={"area_ratio": 0.5, "volume_ratio": 0.7},
            now_ts_ms=1_700_000_000_000,
        )
    )

    assert review.action == AIAction.PASS
    assert review.score_delta == 0
    assert "不可用" in review.comment_cn


def test_news_sentinel_requires_trusted_evidence_for_veto() -> None:
    veto_payload = _response(
        json.dumps(
            {
                "action": "VETO",
                "risk_level": "HIGH",
                "comment_cn": "检测到重大安全风险",
            },
            ensure_ascii=False,
        ),
        model="news-model",
    )

    official_gateway = _gateway(FakeTransport([veto_payload]))
    official_outcome = asyncio.run(
        NewsMacroSentinel(official_gateway).review(
            "BTC-USDT-SWAP",
            [_news_item("okx", official=True)],
            direction=SignalSide.LONG,
            now_ts_ms=1_700_000_000_000,
        )
    )
    assert official_outcome.ai_review.action == AIAction.VETO
    assert official_outcome.veto_eligible is True

    untrusted_gateway = _gateway(FakeTransport([veto_payload]))
    untrusted_outcome = asyncio.run(
        NewsMacroSentinel(untrusted_gateway).review(
            "BTC-USDT-SWAP",
            [_news_item("blog")],
            direction=SignalSide.LONG,
            now_ts_ms=1_700_000_000_000,
        )
    )
    assert untrusted_outcome.ai_review.action == AIAction.HALF_SIZE
    assert untrusted_outcome.veto_eligible is False


def test_news_unavailable_is_explicit_and_conservative() -> None:
    gateway = _gateway(FakeTransport([TimeoutError("news unavailable")] * 2))
    outcome = asyncio.run(
        NewsMacroSentinel(gateway).review(
            "BTC-USDT-SWAP",
            [],
            direction=SignalSide.LONG,
            now_ts_ms=1_700_000_000_000,
        )
    )

    assert outcome.available is False
    assert outcome.requires_news_confirmation is True
    assert outcome.ai_review.action == AIAction.HALF_SIZE


def test_briefing_fallback_is_short_and_does_not_block_signal() -> None:
    gateway = _gateway(FakeTransport([TimeoutError("briefing timeout")] * 2))
    outcome = asyncio.run(
        BriefingAgent(gateway).brief(
            _signal(),
            visual_review=None,
            news_outcome=None,
            context={"btc_state": "UPTREND"},
            now_ts_ms=1_700_000_000_000,
        )
    )

    assert outcome.available is False
    assert len(outcome.text) <= 120
    assert outcome.ai_review.action == AIAction.PASS


class FakeBacktest:
    async def evaluate(
        self,
        params_before: dict[str, object],
        params_after: dict[str, object],
    ) -> dict[str, object]:
        del params_before, params_after
        return {
            "max_drawdown_before": 0.20,
            "max_drawdown_after": 0.18,
            "sharpe_before": 1.0,
            "sharpe_after": 1.2,
            "net_return_before": 0.10,
            "net_return_after": 0.11,
            "golden_recall": 0.85,
        }


def test_strategy_evolver_requires_approval_before_apply_and_supports_rollback() -> None:
    payload = _response(
        json.dumps(
            {
                "diagnosis_md": "样本外表现改善且回撤下降",
                "params_after": {"area_ratio": 0.6, "volume_ratio": 0.75},
            },
            ensure_ascii=False,
        ),
        model="reasoning-model",
    )
    gateway = _gateway(FakeTransport([payload]))
    store = InMemoryParameterStore({"area_ratio": 0.5, "volume_ratio": 0.8})
    evolver = StrategyEvolutionAgent(gateway, parameter_store=store)

    proposal = asyncio.run(
        evolver.generate_proposal(
            diagnosis_input="最近 30 天止损交易摘要",
            params_before=store.current_params,
            backtest=FakeBacktest(),
            now_ts_ms=1_700_000_000_000,
        )
    )

    assert proposal.status == "PASSED_BACKTEST"
    assert store.current_params == {"area_ratio": 0.5, "volume_ratio": 0.8}

    approved = evolver.approve_proposal(proposal.proposal_id)
    assert approved.status == "APPROVED"
    assert store.current_params == {"area_ratio": 0.6, "volume_ratio": 0.75}

    rolled_back = evolver.rollback(proposal.proposal_id)
    assert rolled_back.status == "ROLLED_BACK"
    assert store.current_params == {"area_ratio": 0.5, "volume_ratio": 0.8}
