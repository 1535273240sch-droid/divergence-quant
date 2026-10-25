# M13 HANDOFF

## 状态
已完成 M13：部署、监控、备份与灾难恢复（对应计划书 T10.1、T10.2 与 §10、§11、§12）。

## 本次完成内容
1. **全组件 Docker Compose 服务编排 (`docker-compose.yml`, `Dockerfile`)**：
   - 编排计划书 §3.3 规定的全部 8 个核心容器：`postgres` (TimescaleDB)、`redis`、`collector`、`engine`、`api`、`web`、`prometheus`、`grafana`，以及按需回测 `backtest`。
   - 所有长驻服务均配置 `restart: always` 与针对性 healthcheck。
   - 容器互联采用独立的隔离网络 `dquant_net`，数据持久化到各命名卷。
   - 杜绝硬编码明文密码与密钥，统一由 `.env` 安全注入。

2. **生产级 Nginx 反向代理与安全加固 (`deploy/nginx/`)**：
   - HTTP (80) 强制 301 重定向至 HTTPS (443)。
   - 现代 TLSv1.2/1.3 加密套件与 HSTS、X-Frame-Options、CSP 等安全响应头。
   - 前端 SPA 静态托管与静态资源长缓存。
   - `/api/` 反向代理与速率限制（15r/s 防暴力破解）。
   - `/ws/` WebSocket 长连接反向代理支持。
   - Let's Encrypt Certbot 证书申请支持。

3. **Prometheus 与 Grafana 全方位监控告警 (`deploy/prometheus/`, `deploy/grafana/`)**：
   - 抓取 engine、api、collector、redis、postgres 监控端点。
   - 落地 7 项关键业务与系统告警规则 (`alerts.yml`)：
     - `InstanceDown`（容器/服务离线）
     - `EngineHeartbeatMissing`（引擎心跳超时 > 3分钟）
     - `MarketDataDelayHigh`（行情接收延迟 > 60秒）
     - `ReconciliationDiscrepancy`（交易所对账差异非0）
     - `RiskCircuitBreakerTriggered`（风控熔断触发）
     - `DiskSpaceLow`（磁盘空间低于 15%）
     - `BackupFailed`（每日备份失败或缺失）
   - Grafana 自动加载 Prometheus 数据源与内置《DivergenceQuant 运维总览看板》（包含系统状态、对账状态、风控熔断、行情延迟、动态权益曲线与 AI 消耗）。

4. **自动化运维与灾备恢复脚本 (`scripts/`)**：
   - `scripts/healthcheck.sh`：容器运行状态、PostgreSQL 就绪、Redis ping、FastAPI 健康端点、Redis 引擎心跳（180s 内）与磁盘容量巡检。
   - `scripts/deploy.sh`：遵循“停开仓安全锁 → 排查挂单 → 拉取代码 → 数据库迁移 → 重建启动 → 健康巡检 → 交易所对账 → 人工确认放行”的发布铁律。
   - `scripts/backup.sh`：导出 PostgreSQL (`pg_dump -Fc`)、配置文件、审计日志与回测元数据；打包压缩；AES-256-CBC 强加密；生成 SHA256 校验和；保留 30 天滚动清理；预留对象存储 S3/OSS 上传接口。满足 RPO ≤ 24 小时。
   - `scripts/restore.sh`：备份签名校验与解密；强制先置系统为【停开仓】与【安全模式】；恢复数据库与报告；重启容器；执行交易所对账；满足 RTO ≤ 30 分钟。严格限制必须人工放行才能恢复开仓。

5. **时序超表与日志轮转 (`deploy/postgres/`, `deploy/logrotate.d/`)**：
   - `deploy/postgres/init-timescaledb.sql`：TimescaleDB 扩展安装，将 `candles` 表按日转换为超表，设置 7 天前历史数据自动压缩策略。
   - `deploy/logrotate.d/dquant`：单文件 100MB 轮转，保留 30 天，延迟压缩。

6. **部署与运维文档与测试 (`deploy/README.md`, `tests/unit/test_m13_deploy.py`)**：
   - 编写了详尽完整的生产部署、安全加固、证书申请与灾备演练手册。
   - 编写了 6 项高标准单元测试，涵盖配置解析、服务依赖、安全边界与告警完整性。

## 修改与新增文件清单
- `docker-compose.yml`
- `Dockerfile`
- `deploy/nginx/nginx.conf`
- `deploy/nginx/conf.d/dquant.conf`
- `deploy/prometheus/prometheus.yml`
- `deploy/prometheus/alerts.yml`
- `deploy/grafana/provisioning/datasources/datasources.yml`
- `deploy/grafana/provisioning/dashboards/dashboards.yml`
- `deploy/grafana/dashboards/dquant-overview.json`
- `deploy/postgres/init-timescaledb.sql`
- `deploy/logrotate.d/dquant`
- `deploy/systemd/dquant.service`
- `deploy/README.md`
- `scripts/healthcheck.sh`
- `scripts/deploy.sh`
- `scripts/backup.sh`
- `scripts/restore.sh`
- `tests/unit/test_m13_deploy.py`
- `协作/M13_部署运维/任务说明.md`
- `协作/M13_部署运维/HANDOFF.md`

## 运维接口与命令
- 健康巡检：`bash scripts/healthcheck.sh`
- 安全发布：`bash scripts/deploy.sh`
- 每日备份：`bash scripts/backup.sh`
- 灾难恢复演练：`bash scripts/restore.sh [备份文件路径]`
- 人工解除停开仓：`docker exec dquant_redis redis-cli del dquant:trading:pause dquant:system:safe_mode`

## 运行测试方法
```bash
py -m pytest -q tests/unit/test_m13_deploy.py
ruff check deploy/ tests/unit/test_m13_deploy.py
ruff format --check tests/unit/test_m13_deploy.py
```

## 测试实际结果
```text
tests/unit/test_m13_deploy.py ......                                     [100%]
6 passed
```

## 已知问题与边界
- 生产环境运行脚本依赖 Linux Shell 环境与 Docker/Docker Compose 运行时。
- 生产域名 SSL 证书在 `deploy/nginx/conf.d/dquant.conf` 默认使用 `deploy/nginx/ssl/dquant.crt`，首次部署需按文档执行生成自签名证书或使用 Certbot 签发真实公网证书。
- 真实云服务器上的对账命令 `python -m dquant.execution.reconcile` 依赖 M09 模块完成实现。

## 依赖与下游衔接
- 依赖 M01 的配置与数据库模型；
- 依赖 M09 的对账模块接口；
- 依赖 M11 的 FastAPI 健康检查路由与端口；
- 交付下游：供生产服务器运维部署、CI/CD 自动化流水线、每周灾备恢复演练。
