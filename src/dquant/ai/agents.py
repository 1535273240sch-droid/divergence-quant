"""M10 的四个 Agent：视觉二审、消息避雷、确认简报和策略进化。"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from dquant.contracts import AIAction, AIReview, ScreenerCandidate, Signal, SignalSide

from .errors import AIBudgetExceeded, AIGatewayError, AIResponseError
from .gateway import OpenAICompatibleGateway
from .news_collector import EvidenceChain, NewsCollector, NewsItem


class VisualAuditOutput(BaseModel):
    """Agent 1 的强制结构化输出。"""

    model_config = ConfigDict(extra="ignore")

    cleanliness_score: int = Field(ge=0, le=100)
    sr_resonance: bool
    fakeout_risk: Literal["LOW", "MEDIUM", "HIGH"]
    score_delta: int = Field(ge=-15, le=15)
    action: AIAction
    comment_cn: str = Field(max_length=60)


class NewsAuditOutput(BaseModel):
    """Agent 2 的强制结构化输出。"""

    model_config = ConfigDict(extra="ignore")

    action: AIAction
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    comment_cn: str = Field(max_length=120)


class BriefingOutput(BaseModel):
    """Agent 3 的强制结构化输出。"""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=1, max_length=120)


@dataclass(frozen=True, slots=True)
class NewsSentinelOutcome:
    """消息 Agent 结果及其证据门禁状态。"""

    ai_review: AIReview
    evidence: EvidenceChain
    available: bool
    veto_eligible: bool
    requires_news_confirmation: bool


@dataclass(frozen=True, slots=True)
class BriefingOutcome:
    """手机确认页使用的一句话结果。"""

    ai_review: AIReview
    text: str
    available: bool


class BacktestGate(Protocol):
    """Agent 4 使用的样本外回测门禁。"""

    async def evaluate(
        self,
        params_before: dict[str, object],
        params_after: dict[str, object],
    ) -> Mapping[str, object]:
        """返回当前参数与候选参数的对比指标。"""

        ...


@dataclass(frozen=True, slots=True)
class EvolutionProposal:
    """待审批的参数优化提案。"""

    proposal_id: str
    diagnosis_md: str
    params_before: dict[str, object]
    params_after: dict[str, object]
    metrics: dict[str, object]
    status: str
    created_ts_ms: int
    model_used: str
    cost_usd: Decimal


class InMemoryParameterStore:
    """测试和本地演练用的版本化参数存储。

    真实数据库适配由后续模块注入；该类只提供“批准后应用、可回滚”的最小边界。
    """

    def __init__(self, initial_params: Mapping[str, object]) -> None:
        if not initial_params:
            raise ValueError("initial parameters must not be empty")
        self._current = dict(initial_params)
        self._history: list[dict[str, object]] = []

    @property
    def current_params(self) -> dict[str, object]:
        return dict(self._current)

    def apply(self, params: Mapping[str, object]) -> None:
        self._history.append(dict(self._current))
        self._current = dict(params)

    def rollback(self) -> None:
        if not self._history:
            raise ValueError("no parameter version to roll back")
        self._current = self._history.pop()


class VisualAuditor:
    """Agent 1：对数学背离结果做异步视觉二审。"""

    def __init__(
        self,
        gateway: OpenAICompatibleGateway,
        *,
        allow_veto: bool = True,
        max_score_delta: int = 15,
    ) -> None:
        if not 0 <= max_score_delta <= 15:
            raise ValueError("max_score_delta must be between 0 and 15")
        self.gateway = gateway
        self.allow_veto = allow_veto
        self.max_score_delta = max_score_delta

    async def review(
        self,
        candidate: ScreenerCandidate,
        *,
        chart_png: bytes,
        indicators: Mapping[str, object],
        now_ts_ms: int,
    ) -> AIReview:
        """审阅单个候选币；AI 不可用时返回不修正数学评分的 PASS。"""

        if not chart_png:
            raise ValueError("chart_png must not be empty")
        if not indicators:
            raise ValueError("indicators must not be empty")
        image = base64.b64encode(chart_png).decode("ascii")
        prompt = {
            "candidate": candidate.model_dump(mode="json"),
            "indicators": dict(indicators),
            "rule": "仅输出 JSON，不得修改在线策略参数。",
        }
        messages: list[Mapping[str, object]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": json.dumps(prompt, ensure_ascii=False, default=str)},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image}"},
                    },
                ],
            }
        ]
        review_id = _review_id("visual", candidate.inst_id, now_ts_ms)
        try:
            response = await self.gateway.complete(
                "vision",
                messages,
                idempotency_key=review_id,
                response_format={"type": "json_object"},
            )
            parsed = VisualAuditOutput.model_validate(_parse_json_object(response.content))
        except (AIGatewayError, AIBudgetExceeded, AIResponseError, ValueError, TypeError):
            return _fallback_review(
                review_id,
                candidate.inst_id,
                now_ts_ms,
                comment="AI视觉二审不可用，沿用数学评分。",
            )

        action = parsed.action
        if action == AIAction.VETO and not self.allow_veto:
            action = AIAction.HALF_SIZE
        score_delta = max(-self.max_score_delta, min(self.max_score_delta, parsed.score_delta))
        return AIReview(
            review_id=review_id,
            inst_id=candidate.inst_id,
            action=action,
            score_delta=score_delta,
            comment_cn=parsed.comment_cn,
            model_used=response.model,
            evidence_refs=[],
            cost_usd=response.cost_usd,
            created_ts_ms=now_ts_ms,
        )


class NewsMacroSentinel:
    """Agent 2：消息面和宏观风险审阅。"""

    def __init__(
        self,
        gateway: OpenAICompatibleGateway,
        *,
        collector: NewsCollector | None = None,
        allow_veto: bool = True,
        macro_window_hours: int = 2,
    ) -> None:
        if macro_window_hours < 0:
            raise ValueError("macro_window_hours must not be negative")
        self.gateway = gateway
        self.collector = collector or NewsCollector()
        self.allow_veto = allow_veto
        self.macro_window_hours = macro_window_hours

    async def review(
        self,
        inst_id: str,
        news_items: Sequence[NewsItem],
        *,
        direction: SignalSide,
        now_ts_ms: int,
        macro_events: Sequence[Mapping[str, object]] = (),
    ) -> NewsSentinelOutcome:
        """审阅近 48 小时消息，并应用可信证据 VETO 门禁。"""

        if not inst_id.strip():
            raise ValueError("inst_id must not be empty")
        recent_items = self.collector.recent(
            news_items,
            now_ts_ms=now_ts_ms,
            max_age_ms=48 * 60 * 60 * 1000,
            inst_id=inst_id,
        )
        evidence = self.collector.evidence_chain(recent_items)
        review_id = _review_id("news", inst_id, now_ts_ms)
        if not recent_items:
            return _news_unavailable(review_id, inst_id, now_ts_ms, evidence)

        prompt = {
            "inst_id": inst_id,
            "direction": direction.value,
            "news": [
                {
                    "item_id": item.item_id,
                    "title": item.title,
                    "source_name": item.source_name,
                    "url": item.url,
                    "published_ts_ms": item.published_ts_ms,
                    "fetched_ts_ms": item.fetched_ts_ms,
                    "credibility": item.credibility,
                    "official": item.official,
                    "inst_id": item.inst_id,
                    "event_type": item.event_type,
                }
                for item in recent_items
            ],
            "macro_events": list(macro_events),
            "veto_rule": "VETO 仅在官方来源或两个独立可信来源支持时成立。",
        }
        try:
            response = await self.gateway.complete(
                "news",
                [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False, default=str)}],
                idempotency_key=review_id,
                response_format={"type": "json_object"},
            )
            parsed = NewsAuditOutput.model_validate(_parse_json_object(response.content))
        except (AIGatewayError, AIBudgetExceeded, AIResponseError, ValueError, TypeError):
            return _news_unavailable(review_id, inst_id, now_ts_ms, evidence)

        action = parsed.action
        if action == AIAction.VETO and not (self.allow_veto and evidence.can_veto):
            action = AIAction.HALF_SIZE
        comment = parsed.comment_cn
        if action == AIAction.VETO:
            comment = f"{comment}（证据 {len(evidence.evidence_refs)} 条）"
        review = AIReview(
            review_id=review_id,
            inst_id=inst_id,
            action=action,
            score_delta=0,
            comment_cn=comment[:120],
            model_used=response.model,
            evidence_refs=list(evidence.evidence_refs),
            cost_usd=response.cost_usd,
            created_ts_ms=now_ts_ms,
        )
        return NewsSentinelOutcome(
            ai_review=review,
            evidence=evidence,
            available=True,
            veto_eligible=evidence.can_veto,
            requires_news_confirmation=action == AIAction.HALF_SIZE and not evidence.can_veto,
        )


class BriefingAgent:
    """Agent 3：生成手机确认页使用的短陪审简报。"""

    def __init__(self, gateway: OpenAICompatibleGateway, *, max_chars: int = 120) -> None:
        if not 1 <= max_chars <= 120:
            raise ValueError("max_chars must be between 1 and 120")
        self.gateway = gateway
        self.max_chars = max_chars

    async def brief(
        self,
        signal: Signal,
        *,
        visual_review: AIReview | None,
        news_outcome: NewsSentinelOutcome | None,
        context: Mapping[str, object],
        now_ts_ms: int,
    ) -> BriefingOutcome:
        """生成不超过 120 字的理由、风险和建议。"""

        review_id = _review_id("briefing", signal.inst_id, now_ts_ms)
        prompt = {
            "signal": signal.model_dump(mode="json"),
            "visual_review": visual_review.model_dump(mode="json") if visual_review else None,
            "news_review": news_outcome.ai_review.model_dump(mode="json") if news_outcome else None,
            "context": dict(context),
            "format": "中文直白输出，最多 120 字，包含理由、风险、建议。",
        }
        try:
            response = await self.gateway.complete(
                "briefing",
                [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False, default=str)}],
                idempotency_key=review_id,
                response_format={"type": "json_object"},
            )
            parsed = BriefingOutput.model_validate(_parse_json_object(response.content))
        except (AIGatewayError, AIBudgetExceeded, AIResponseError, ValueError, TypeError):
            text = _fallback_briefing(signal, self.max_chars)
            return BriefingOutcome(
                ai_review=_fallback_review(
                    review_id,
                    signal.inst_id,
                    now_ts_ms,
                    comment="AI陪审不可用，按数学策略执行。",
                ),
                text=text,
                available=False,
            )

        action = AIAction.PASS
        if news_outcome is not None:
            action = news_outcome.ai_review.action
        review = AIReview(
            review_id=review_id,
            inst_id=signal.inst_id,
            action=action,
            score_delta=0,
            comment_cn=parsed.text[:120],
            model_used=response.model,
            evidence_refs=(
                list(news_outcome.ai_review.evidence_refs) if news_outcome is not None else []
            ),
            cost_usd=response.cost_usd,
            created_ts_ms=now_ts_ms,
        )
        return BriefingOutcome(ai_review=review, text=parsed.text[: self.max_chars], available=True)


class StrategyEvolutionAgent:
    """Agent 4：生成受限参数提案，审批前绝不修改在线参数。"""

    def __init__(
        self,
        gateway: OpenAICompatibleGateway,
        *,
        parameter_store: InMemoryParameterStore,
        max_params_changed: int = 3,
        max_change_ratio: float = 0.25,
    ) -> None:
        if max_params_changed < 1 or max_change_ratio <= 0:
            raise ValueError("invalid evolution limits")
        self.gateway = gateway
        self.parameter_store = parameter_store
        self.max_params_changed = max_params_changed
        self.max_change_ratio = max_change_ratio
        self._proposals: dict[str, EvolutionProposal] = {}

    async def generate_proposal(
        self,
        *,
        diagnosis_input: str,
        params_before: Mapping[str, object],
        backtest: BacktestGate,
        now_ts_ms: int,
    ) -> EvolutionProposal:
        """生成提案并运行回测门禁；只保存提案，不应用配置。"""

        if not diagnosis_input.strip():
            raise ValueError("diagnosis_input must not be empty")
        before = dict(params_before)
        proposal_id = _review_id("evolver", "strategy", now_ts_ms)
        try:
            response = await self.gateway.complete(
                "reasoning",
                [
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"diagnosis": diagnosis_input, "params_before": before},
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                ],
                idempotency_key=proposal_id,
                response_format={"type": "json_object"},
            )
            raw = _parse_json_object(response.content)
            diagnosis = raw.get("diagnosis_md")
            after_raw = raw.get("params_after")
            if not isinstance(diagnosis, str) or not diagnosis.strip():
                raise AIResponseError("diagnosis_md is required")
            if not isinstance(after_raw, Mapping):
                raise AIResponseError("params_after must be an object")
            after = dict(after_raw)
            _validate_param_change(before, after, self.max_params_changed, self.max_change_ratio)
            metrics = dict(await backtest.evaluate(before, after))
            status = "PASSED_BACKTEST" if _passes_gate(metrics) else "REJECTED"
            model_used = response.model
            cost = response.cost_usd
        except (AIGatewayError, AIBudgetExceeded, AIResponseError, ValueError, TypeError):
            diagnosis = "AI 提案不可用，未生成可执行参数变更。"
            after = before
            metrics = {}
            status = "REJECTED"
            model_used = "unavailable"
            cost = Decimal("0")

        proposal = EvolutionProposal(
            proposal_id=proposal_id,
            diagnosis_md=diagnosis,
            params_before=before,
            params_after=after,
            metrics=metrics,
            status=status,
            created_ts_ms=now_ts_ms,
            model_used=model_used,
            cost_usd=cost,
        )
        self._proposals[proposal_id] = proposal
        return proposal

    def approve_proposal(self, proposal_id: str) -> EvolutionProposal:
        """模拟用户在控制台点击“批准并应用”。"""

        proposal = self._get_proposal(proposal_id)
        if proposal.status != "PASSED_BACKTEST":
            raise ValueError("only PASSED_BACKTEST proposals can be approved")
        self.parameter_store.apply(proposal.params_after)
        approved = _replace_proposal(proposal, status="APPROVED")
        self._proposals[proposal_id] = approved
        return approved

    def rollback(self, proposal_id: str) -> EvolutionProposal:
        """回滚已批准的上一版参数。"""

        proposal = self._get_proposal(proposal_id)
        if proposal.status != "APPROVED":
            raise ValueError("only approved proposals can be rolled back")
        self.parameter_store.rollback()
        rolled_back = _replace_proposal(proposal, status="ROLLED_BACK")
        self._proposals[proposal_id] = rolled_back
        return rolled_back

    def get_proposal(self, proposal_id: str) -> EvolutionProposal:
        """读取提案快照。"""

        return self._get_proposal(proposal_id)

    def _get_proposal(self, proposal_id: str) -> EvolutionProposal:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(f"unknown proposal: {proposal_id}")
        return proposal


def _parse_json_object(content: str) -> dict[str, object]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise AIResponseError("structured AI response must be a JSON object")
    return parsed


def _review_id(agent_name: str, inst_id: str, ts_ms: int) -> str:
    raw = f"{agent_name}:{inst_id}:{ts_ms}".encode()
    return f"{agent_name}-{hashlib.sha256(raw).hexdigest()[:24]}"


def _fallback_review(review_id: str, inst_id: str, ts_ms: int, *, comment: str) -> AIReview:
    return AIReview(
        review_id=review_id,
        inst_id=inst_id,
        action=AIAction.PASS,
        score_delta=0,
        comment_cn=comment,
        model_used="unavailable",
        evidence_refs=[],
        cost_usd=0,
        created_ts_ms=ts_ms,
    )


def _news_unavailable(
    review_id: str,
    inst_id: str,
    ts_ms: int,
    evidence: EvidenceChain,
) -> NewsSentinelOutcome:
    review = AIReview(
        review_id=review_id,
        inst_id=inst_id,
        action=AIAction.HALF_SIZE,
        score_delta=0,
        comment_cn="消息数据不可用，禁止高风险信号直接放行。",
        model_used="unavailable",
        evidence_refs=list(evidence.evidence_refs),
        cost_usd=0,
        created_ts_ms=ts_ms,
    )
    return NewsSentinelOutcome(
        ai_review=review,
        evidence=evidence,
        available=False,
        veto_eligible=False,
        requires_news_confirmation=True,
    )


def _fallback_briefing(signal: Signal, max_chars: int) -> str:
    stop = signal.stop_loss if signal.stop_loss is not None else "未设置"
    text = (
        f"AI不可用；按{signal.strategy_name}数学信号执行，方向{signal.side.value}，"
        f"入场{signal.price}，止损{stop}。"
    )
    return text[:max_chars]


def _validate_param_change(
    before: Mapping[str, object],
    after: Mapping[str, object],
    max_params_changed: int,
    max_change_ratio: float,
) -> None:
    if set(before) != set(after):
        raise ValueError("parameter proposal cannot add or remove keys")
    changed = [key for key in before if before[key] != after[key]]
    if len(changed) > max_params_changed:
        raise ValueError("parameter proposal changes too many parameters")
    for key in changed:
        old = before[key]
        new = after[key]
        if isinstance(old, bool) or isinstance(new, bool) or not isinstance(old, (int, float)):
            raise ValueError(f"parameter {key} is not a numeric tunable value")
        if not isinstance(new, (int, float)):
            raise ValueError(f"parameter {key} is not a numeric tunable value")
        if old == 0:
            raise ValueError(f"parameter {key} cannot change from zero")
        if abs(float(new) - float(old)) / abs(float(old)) > max_change_ratio:
            raise ValueError(f"parameter {key} changes more than allowed ratio")


def _passes_gate(metrics: Mapping[str, object]) -> bool:
    try:
        drawdown_after = _numeric_metric(metrics, "max_drawdown_after")
        drawdown_before = _numeric_metric(metrics, "max_drawdown_before")
        recall = _numeric_metric(metrics, "golden_recall")
        sharpe_before = _numeric_metric(metrics, "sharpe_before")
        sharpe_after = _numeric_metric(metrics, "sharpe_after")
        return_before = _numeric_metric(metrics, "net_return_before")
        return_after = _numeric_metric(metrics, "net_return_after")
        drawdown_ok = drawdown_after <= drawdown_before
        recall_ok = recall >= 0.8
        sharpe_lift = _relative_lift(sharpe_before, sharpe_after)
        return_lift = _relative_lift(return_before, return_after)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False
    return drawdown_ok and recall_ok and max(sharpe_lift, return_lift) >= 0.10


def _numeric_metric(metrics: Mapping[str, object], name: str) -> float:
    value = metrics[name]
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"metric {name} must be numeric")
    return float(value)


def _relative_lift(before: float, after: float) -> float:
    old = before
    new = after
    if old == 0:
        return float("inf") if new > 0 else 0.0
    return (new - old) / abs(old)


def _replace_proposal(proposal: EvolutionProposal, *, status: str) -> EvolutionProposal:
    return EvolutionProposal(
        proposal_id=proposal.proposal_id,
        diagnosis_md=proposal.diagnosis_md,
        params_before=dict(proposal.params_before),
        params_after=dict(proposal.params_after),
        metrics=dict(proposal.metrics),
        status=status,
        created_ts_ms=proposal.created_ts_ms,
        model_used=proposal.model_used,
        cost_usd=proposal.cost_usd,
    )
