import asyncio
import json
from decimal import Decimal

from dquant.ai import (
    BriefingAgent,
    GatewayConfig,
    NewsMacroSentinel,
    OpenAICompatibleGateway,
    VisualAuditor,
)
from dquant.ai.news_collector import NewsItem
from dquant.contracts import (
    AIAction,
    DivergenceKind,
    OrderType,
    RegimeState,
    ScreenerCandidate,
    Signal,
    SignalAction,
    SignalSide,
)


class SequenceTransport:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = list(payloads)

    async def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_sec: float,
    ) -> dict[str, object]:
        del url, headers, payload, timeout_sec
        return self.payloads.pop(0)


def _response(content: str, model: str) -> dict[str, object]:
    return {
        "model": model,
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10},
    }


def test_three_agents_share_gateway_without_blocking_math_chain() -> None:
    transport = SequenceTransport(
        [
            _response(
                json.dumps(
                    {
                        "cleanliness_score": 82,
                        "sr_resonance": True,
                        "fakeout_risk": "LOW",
                        "score_delta": 4,
                        "action": "PASS",
                        "comment_cn": "结构清晰，支撑共振。",
                    },
                    ensure_ascii=False,
                ),
                "vision-model",
            ),
            _response(
                json.dumps(
                    {
                        "action": "PASS",
                        "risk_level": "LOW",
                        "comment_cn": "未发现需要否决的可信利空。",
                    },
                    ensure_ascii=False,
                ),
                "news-model",
            ),
            _response(
                json.dumps(
                    {"text": "① 日线在均线上方；② 4H 底背离；③ 止损明确。建议：按计划开多。"},
                    ensure_ascii=False,
                ),
                "briefing-model",
            ),
        ]
    )
    gateway = OpenAICompatibleGateway(
        GatewayConfig.from_mapping(
            {
                "enabled": True,
                "timeout_sec": 12,
                "daily_budget_usd": 1.0,
                "models": {
                    name: {
                        "provider": "test",
                        "base_url": "https://example.test/v1",
                        "model": f"{name}-model",
                    }
                    for name in ("vision", "news", "briefing")
                },
            }
        ),
        transport=transport,
        now_ms=lambda: 1_700_000_000_000,
    )

    candidate = ScreenerCandidate(
        inst_id="BTC-USDT-SWAP",
        pool="A",
        regime=RegimeState.UPTREND,
        divergence_id="div-1",
        divergence_kind=DivergenceKind.BULL_REGULAR,
        score=80,
        rank=1,
        updated_ts_ms=1_700_000_000_000,
    )
    visual = asyncio.run(
        VisualAuditor(gateway).review(
            candidate,
            chart_png=b"png",
            indicators={"area_ratio": 0.5},
            now_ts_ms=1_700_000_000_000,
        )
    )
    news = asyncio.run(
        NewsMacroSentinel(gateway).review(
            candidate.inst_id,
            [
                NewsItem(
                    item_id="news-1",
                    title="常规项目更新",
                    source_name="project",
                    url="https://project.example/update",
                    published_ts_ms=1_699_999_900_000,
                    fetched_ts_ms=1_700_000_000_000,
                    credibility=0.8,
                    official=True,
                    inst_id=candidate.inst_id,
                )
            ],
            direction=SignalSide.LONG,
            now_ts_ms=1_700_000_000_000,
        )
    )
    briefing = asyncio.run(
        BriefingAgent(gateway).brief(
            Signal(
                signal_id="sig-1",
                inst_id=candidate.inst_id,
                side=SignalSide.LONG,
                action=SignalAction.OPEN,
                entry_type=OrderType.LIMIT,
                price=Decimal("100"),
                stop_loss=Decimal("95"),
                reason="底背离突破",
                strategy_name="S1",
                created_ts_ms=1_700_000_000_000,
            ),
            visual_review=visual,
            news_outcome=news,
            context={"btc_state": "UPTREND"},
            now_ts_ms=1_700_000_000_000,
        )
    )

    assert visual.action == AIAction.PASS
    assert news.ai_review.action == AIAction.PASS
    assert briefing.ai_review.action == AIAction.PASS
    assert len(briefing.text) <= 120
    assert len(gateway.call_logs) == 3
