# M11 HANDOFF: FastAPI、鉴权、通知和审计

## 状态

M11 模块已完整实现并全部通过单元与集成测试。

## 本次完成内容

### 1. 邮件通知与告警系统 (`src/dquant/notify/`)
- **配置与安全**: `config.py` 支持通过环境变量覆盖 SMTP 配置，密码与密钥绝不硬编码。
- **视觉模板**: `templates.py` 严格遵循顶级量化终端 Pure Black & Liquid Glass 视觉规范（OLED 纯黑 `#000000` 底色、翡翠霓虹绿 `#00F5A0`、霓虹绯红 `#FF4D5E`、钛金冷灰 `#CBD5E1`）。包含信号通知、成交通知、风控告警、错误报警、每日战报与多池选币合并摘要，且具备完备的 HTML 转义与 XSS 防护。
- **限流与去重**: `limiter.py` 实现滑动窗口每小时最大 30 封限额，同标的/同规则消息时间窗口防刷屏去重。
- **可靠性与发件箱持久化**: `outbox.py` 发送失败时重试最多 3 次，仍失败自动写入 `notify_outbox` 表，提供后台自动补发调度方法。
- **通知门面**: `EmailNotifier` 整合上述全部机制，支持异步发送与无 SMTP 环境下的 `dry_run` 降级。

### 2. FastAPI 服务、安全鉴权与审计 (`src/dquant/api/`)
- **配置与防爆破**: `config.py` 支持环境变量覆盖 JWT 密钥、管理员密码哈希、TOTP 种子与超时参数。
- **统一错误封装**: `errors.py` 输出标准响应结构 `{code, message, detail, timestamp}`，提供全局统一异常拦截器。
- **双因素认证与防暴力破解**: `auth.py` 实现 RFC 6238 标准 TOTP 算法（动态截断与时钟步长容忍），密码连续错误 5 次自动锁定 15 分钟 (HTTP 423)。
- **一次性确认下单令牌**: `auth.py` 实现基于 HMAC-SHA256 签名的 15 分钟有效令牌机制，单次消费防重放、防 CSRF。
- **交易安全门禁**: 核心规则落地：`GET /api/v1/signals/{id}/confirm` 返回 405 Method Not Allowed，严格禁止 GET 请求产生交易动作；仅支持经过登录认证的 POST 请求 + 一次性令牌二次确认 + 价格滑点超限防穿仓校验。
- **审计与脱敏**: `audit.py` 拦截敏感操作（登录、确认下单、修改策略参数、审批 AI 提案、紧急停开仓、紧急全平），敏感字段（password, token, secret）自动替换为 `******`，API Key 仅保留后 4 位，并写入 `AuditLogModel`。
- **业务路由**:
  - `auth_routes.py`: 登录、TOTP 初始化绑定、验证、当前用户查询。
  - `signal_routes.py`: 待确认信号列表、信号详情、二次确认下单、信号驳回。
  - `strategy_routes.py`: 策略列表、参数调整、AI 提案审批与驳回。
  - `emergency_routes.py`: 紧急开关状态查询、紧急停止开仓、一键全部平仓（必须输入口令 `CLOSE_ALL_CONFIRMED`）、人工解除熔断恢复开仓。
  - `market_routes.py`: 选币候选池与持仓状态快照。
  - `health_routes.py`: 探针健康检查 (`/api/v1/health`)。
- **WebSocket 实时流**: `websocket.py` 支持长连接管理、心跳双向通信 (PING/PONG)、信号与紧急状态变更实时全网广播。

## 修改与新建文件

- `src/dquant/notify/config.py`
- `src/dquant/notify/limiter.py`
- `src/dquant/notify/templates.py`
- `src/dquant/notify/outbox.py`
- `src/dquant/notify/notifier.py`
- `src/dquant/notify/__init__.py`
- `src/dquant/api/config.py`
- `src/dquant/api/errors.py`
- `src/dquant/api/audit.py`
- `src/dquant/api/auth.py`
- `src/dquant/api/schemas.py`
- `src/dquant/api/websocket.py`
- `src/dquant/api/routes/auth_routes.py`
- `src/dquant/api/routes/signal_routes.py`
- `src/dquant/api/routes/strategy_routes.py`
- `src/dquant/api/routes/emergency_routes.py`
- `src/dquant/api/routes/market_routes.py`
- `src/dquant/api/routes/health_routes.py`
- `src/dquant/api/app.py`
- `src/dquant/api/__init__.py`
- `tests/unit/test_notify.py`
- `tests/unit/test_api.py`
- `tests/integration/test_api_auth.py`
- `协作/M11_API_通知_鉴权/任务说明.md`
- `协作/M11_API_通知_鉴权/HANDOFF.md`

## 暴露的关键公共类与函数

### 通知模块入口 (`dquant.notify`)
```python
from dquant.notify import (
    EmailNotifier,
    EmailSettings,
    NotificationLimiter,
    OutboxManager,
    load_email_settings,
    render_signal_email,
    render_fill_email,
    render_risk_email,
    render_error_email,
    render_daily_report_email,
    render_screener_summary_email,
)
```

### API 模块入口 (`dquant.api`)
```python
from dquant.api import (
    app,
    create_app,
    AuditService,
    sanitize_dict,
    create_access_token,
    decode_access_token,
    verify_totp,
    compute_totp,
    generate_totp_secret,
    confirmation_token_manager,
    ws_manager,
)
```

## 测试与验证结果

在项目根目录下执行验证命令：

```bash
# 1. 静态代码检查
ruff check src/dquant/api src/dquant/notify tests/unit/test_api.py tests/unit/test_notify.py tests/integration/test_api_auth.py
# 输出: All checks passed!

# 2. 代码格式检查
ruff format --check src/dquant/api src/dquant/notify tests/unit/test_api.py tests/unit/test_notify.py tests/integration/test_api_auth.py
# 输出: 23 files already formatted

# 3. M11 专属测试
py -m pytest -q tests/unit/test_api.py tests/unit/test_notify.py tests/integration/test_api_auth.py
# 输出: 19 passed in 3.37s

# 4. M11 与已有模块回归测试
py -m pytest -q tests/unit/test_api.py tests/unit/test_notify.py tests/integration/test_api_auth.py tests/unit/test_contracts.py tests/test_project_layout.py tests/unit/test_m01_db.py tests/unit/test_m01_settings.py
# 输出: 31 passed in 6.35s
```

## 已知限制与依赖下一步

1. **上游服务对接**: M11 目前提供了完整的 API 契约、内存测试池和降级机制。当 M09 (OMS) 和 M10 (AI) 交付后，可直接在 `signal_routes.py` 中将订单执行委托给 M09 的 OMS 引擎。
2. **前端消费**: M12 (Web 控制台与手机端) 可直接连接 `/api/v1/auth/login`、`/api/v1/signals/pending`、`/api/v1/signals/{id}/confirm` 以及 `/ws` 实时广播流进行联调。
3. **运维与容器**: M13 (部署与监控) 可以直接使用 `GET /api/v1/health` 作为 Docker 与 Nginx 的就绪探针。
