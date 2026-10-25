# M10 HANDOFF：AI 网关、新闻数据和四个 Agent

## 1. 状态

M10 第一版已完成，范围按计划书 §5.14、T8.3～T8.5 实现。实现只使用注入的 transport、回测门禁和参数存储，默认不连接任何真实模型服务或 OKX 实盘。

## 2. 开始调查后的结论

- M10 原先只有 `src/dquant/ai/__init__.py` 占位文件，没有网关、Agent 或新闻标准化实现。
- `src/dquant/contracts/models.py` 已提供 `AIReview`，字段含义保持不变；M10 没有修改 `contracts/`。
- M01 已提供 `ai_reviews`、`ai_optimization_proposals`、`ai_call_logs`、`macro_events` 等数据库表模型，但 M10 不直接写数据库，避免跨模块越界。
- M02/M04/M05/M07/M08/M09 是相关依赖或消费者：M04 提供背离结果，M05 提供候选币，M07 提供 `Signal`，M08/M11/M12 消费 AI 审阅，M06 为 Agent 4 提供回测门禁。
- 当前仓库没有 M10 专属的旧 HANDOFF；M01 HANDOFF 已阅读。

## 3. 修改范围

只修改了以下允许目录：

- `src/dquant/ai/`
- `src/dquant/data/news_collector.py`
- `tests/unit/test_ai_fallback.py`
- `tests/integration/test_ai_gateway.py`
- `协作/M10_AI/HANDOFF.md`

没有修改 `contracts/`、总控契约、总进度、`pyproject.toml`、`config/config.yaml`、其他业务模块或数据库迁移。

## 4. 已完成内容

### 4.1 统一模型网关

- `GatewayConfig` / `ModelRoute`：支持 `vision`、`news`、`briefing`、`reasoning` 四个独立路由槽位。
- `OpenAICompatibleGateway`：OpenAI 兼容 `/chat/completions` JSON 调用，支持标准库传输和注入测试 transport。
- 主路由失败时使用备用路由；超时、网络错误、非法结构都有明确异常。
- 每次调用记录模型、Token、费用、延迟、状态、UTC 毫秒时间戳和是否使用备用路由。
- `CallLogSink` 是可选持久化边界，M01/M11 可以注入数据库写入器；调用日志不包含 prompt、原始响应或 API Key。
- `daily_budget_usd` 按 UTC 自然日限额；达到上限后抛出 `AIBudgetExceeded`。
- `idempotency_key` 对成功调用做缓存，重复调用不会再次访问模型服务。
- `UrllibJSONTransport` 使用 Python 标准库，不新增依赖。

### 4.2 Agent 1：视觉二审

- `VisualAuditor` 校验图像和指标输入，要求结构化 JSON 输出。
- 输出 `VisualAuditOutput`，限制清晰度、评分修正范围、动作和中文点评长度。
- AI 超时、预算耗尽、非法 JSON 或服务不可用时，返回 `AIReview(action=PASS, score_delta=0)`，不阻塞数学策略。

### 4.3 Agent 2：消息/宏观避雷

- `NewsItem`、`NewsSource`、`EvidenceChain`、`NewsCollector` 记录来源、URL、发布时间、抓取时间、可信度和是否官方。
- `NewsMacroSentinel` 只允许满足“一个官方来源或两个独立可信来源”的模型 VETO 生效。
- 证据不足但模型返回 VETO 时自动降为 `HALF_SIZE`，不把未经证实的模型推测直接升级为风控否决。
- 新闻为空、超时或不可用时明确返回 `available=False`、`requires_news_confirmation=True` 和保守的 `HALF_SIZE`；高风险逆势动作的消费者必须检查该标志。
- `src/dquant/data/news_collector.py` 保留计划书规定的路径，并转出 M10 的统一类型；它不主动抓取 RSS/API，来源适配器由数据层注入。

### 4.4 Agent 3：手机确认一句话陪审

- `BriefingAgent` 组合策略信号、视觉审阅、消息审阅和账户上下文。
- 结构化输出严格限制为最多 120 字。
- AI 不可用时返回确定性的短简报，不阻塞信号或保护性流程。

### 4.5 Agent 4：周度复盘和参数提案

- `StrategyEvolutionAgent` 通过 `BacktestGate` 注入样本外回测门禁。
- 单次最多修改 3 个参数，每个数值参数变动不超过 25%，禁止增删参数。
- 门禁要求：最大回撤不增加、金标召回率至少 80%，且样本外 Sharpe 或净收益提升至少 10%。
- `InMemoryParameterStore` 只作为本地演练/测试实现；生成提案和回测通过不会修改当前配置，只有显式调用 `approve_proposal()` 才应用，`rollback()` 可回滚。

## 5. 对外接口

主要入口：

```python
from dquant.ai import (
    GatewayConfig,
    OpenAICompatibleGateway,
    VisualAuditor,
    NewsMacroSentinel,
    BriefingAgent,
    StrategyEvolutionAgent,
)
from dquant.ai.news_collector import NewsCollector, NewsItem, NewsSource
```

公共结果：

- Agent 1 直接返回 `dquant.contracts.AIReview`。
- Agent 2 返回 `NewsSentinelOutcome`，其中 `ai_review` 是公共 `AIReview`，同时带 `available`、`veto_eligible`、`requires_news_confirmation` 和 `EvidenceChain`。
- Agent 3 返回 `BriefingOutcome`，其中 `ai_review` 是公共 `AIReview`，`text` 是手机端短简报。
- Agent 4 返回 `EvolutionProposal`，只能通过显式审批接口改变注入的参数存储。
- 网关调用账本为 `AICallRecord`，可通过 `call_logs` 读取，也可注入 `CallLogSink` 持久化。

## 6. 测试和实际结果

M10 专属检查：

```text
py -m pytest -q tests/unit/test_ai_fallback.py tests/integration/test_ai_gateway.py
11 passed

ruff check src/dquant/ai src/dquant/data/news_collector.py tests/unit/test_ai_fallback.py tests/integration/test_ai_gateway.py
All checks passed

ruff format --check src/dquant/ai src/dquant/data/news_collector.py tests/unit/test_ai_fallback.py tests/integration/test_ai_gateway.py
8 files already formatted
```

全项目检查：

- `py -m pytest -q`：`423 passed, 14 failed`。失败集中在已有 M04 背离测试、M02 OKX/行情测试、M08 风控测试和缺失的 M04 脚本，不是 M10 测试；M10 的 11 个测试均通过。
- `ruff check src/dquant/ai tests`：失败于其他模块已有测试文件，共 20 个 lint 错误；M10 专属目录检查通过。
- `ruff format --check src/dquant/ai tests`：其他模块已有 6 个测试文件未格式化；M10 专属目录检查通过。
- `py -m mypy ...`：当前 Python 环境没有安装 mypy，实际错误为 `No module named mypy`，不能报告类型检查通过。

## 7. 已知问题和下一步

1. M10 目前默认使用内存调用账本；生产持久化需要 M11/M01 实现并注入 `CallLogSink`，不能把内存账本当作数据库落库完成。
2. 新闻来源抓取、RSS/API 调度、宏观日历同步不在本次 M10 的无网络实现中；M02/后续数据层需把真实或录制的来源转换为 `NewsItem`。
3. 计划书数据库字段包含 `agent_name`、`provider`、`fallback_used`、`evidence_json` 等信息，而现有 `AIReview` 公共契约没有这些字段。M10 通过 `NewsSentinelOutcome` 和 `AICallRecord` 暂存这些信息，没有私自改契约。
4. 所有真实模型请求仍需由部署配置提供 API Key；当前测试只使用 fake transport，不连接 OKX 实盘。

## 8. 公共接口变更申请

### 建议变更

在下一次总控契约版本中，为 `AIReview` 增加以下**可选字段**：

- `agent_name: str | None`：区分视觉、新闻、简报 Agent。
- `provider: str | None`：记录实际供应商。
- `fallback_used: bool = False`：记录是否走备用路由。
- `evidence_json: dict[str, object] | None`：保存证据数量、来源和 VETO 门禁结果。
- `available: bool = True`：区分 AI 正常结果与降级结果。

### 原因

计划书 §7.1 要求 `ai_reviews` 可追溯 Agent、供应商、降级路径和新闻证据；现有 `AIReview` 只能承载通用动作、评分修正和 URL 列表，无法完整映射审计字段。M10 暂不改变公共模型，避免影响 M08/M11/M12 现有调用方。

### 影响模块

- M00：契约版本和兼容迁移。
- M01：`AIReviewModel` 字段映射和增量迁移。
- M08：风控读取 `available` / `requires_news_confirmation` 的降级策略。
- M11/M12：API 和 UI 展示供应商、备用路由、证据链。

### 兼容旧接口方案

全部新增字段使用可选值或已有安全默认值，旧消费者只读取 `action`、`score_delta`、`comment_cn` 等现有字段时无需改动。总控批准后再同步契约、数据库迁移、fixture 和集成测试。
