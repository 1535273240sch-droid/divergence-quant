"""M10：AI 网关与四个 Agent 的公共入口。"""

from dquant.contracts import AIAction

from .agents import (
    BriefingAgent,
    BriefingOutcome,
    EvolutionProposal,
    InMemoryParameterStore,
    NewsMacroSentinel,
    NewsSentinelOutcome,
    StrategyEvolutionAgent,
    VisualAuditor,
    VisualAuditOutput,
)
from .errors import (
    AIBudgetExceeded,
    AIConfigurationError,
    AIGatewayError,
    AIGatewayTimeout,
    AIResponseError,
)
from .gateway import (
    AICallRecord,
    CallLogSink,
    GatewayConfig,
    GatewayResponse,
    ModelRoute,
    OpenAICompatibleGateway,
    UrllibJSONTransport,
)
from .news_collector import (
    EvidenceChain,
    NewsCollector,
    NewsItem,
    NewsSource,
    NewsValidationError,
)

__all__ = [
    "AIBudgetExceeded",
    "AICallRecord",
    "CallLogSink",
    "AIAction",
    "AIConfigurationError",
    "AIGatewayError",
    "AIGatewayTimeout",
    "AIResponseError",
    "BriefingAgent",
    "BriefingOutcome",
    "EvolutionProposal",
    "GatewayConfig",
    "GatewayResponse",
    "InMemoryParameterStore",
    "ModelRoute",
    "EvidenceChain",
    "NewsCollector",
    "NewsItem",
    "NewsMacroSentinel",
    "NewsSentinelOutcome",
    "NewsSource",
    "NewsValidationError",
    "OpenAICompatibleGateway",
    "StrategyEvolutionAgent",
    "UrllibJSONTransport",
    "VisualAuditOutput",
    "VisualAuditor",
]
