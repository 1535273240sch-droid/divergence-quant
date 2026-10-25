# M01 HANDOFF

## 状态

已完成 M01 第一版：配置加载、数据库模型与 Alembic 迁移骨架、Redis 客户端边界。

## 已完成内容

1. `src/dquant/settings.py`
   - YAML 加载。
   - `DQUANT_EXCHANGE_MODE`、`DQUANT_DATABASE_URL`、`DQUANT_REDIS_URL` 覆盖。
   - `${ENV_NAME}` 占位符展开。
   - demo/live/backtest 模式校验。
   - live 模式必须同时满足 `LIVE_TRADING_CONFIRM=YES` 和 `sub_account_only=true`。
2. `src/dquant/db/models.py`
   - 落地计划书 §7 的 24 张业务表模型。
   - 金额、价格、数量使用 Numeric/Decimal 语义。
   - K 线使用 `(inst_id, bar, ts_ms)` 复合主键。
3. `src/dquant/db/base.py`
   - SQLAlchemy 声明式基类和命名约定。
4. `src/dquant/db/session.py`
   - 异步数据库引擎、会话工厂、事务上下文和连接池释放。
5. `src/dquant/db/redis.py`
   - Redis 客户端创建、ping 检查和关闭边界。
6. `alembic.ini`、`alembic/env.py`、`alembic/versions/0001_initial_schema.py`
   - 支持异步 PostgreSQL URL。
   - 支持 `py -m alembic upgrade head --sql` 生成静态 SQL。
7. M01 单元测试
   - 配置加载、环境变量覆盖、实盘安全门禁。
   - SQLite 元数据建表兼容性。
   - 数据库引擎和 Redis URL 解析。

## 关键入口

```python
from dquant.settings import default_settings, load_settings
from dquant.db import Base
from dquant.db.redis import create_redis_client
from dquant.db.session import create_database_engine
```

## 实际检查

```text
ruff check .                          # 通过
ruff format --check src tests alembic  # 通过，28 files already formatted
py -m pytest -q                       # 通过，15 passed
py -m alembic upgrade head --sql      # 通过，能生成完整初始 DDL
```

## 已知限制

- 当前环境没有安装 `mypy`，不能把类型检查报告为通过。
- M01 不主动连接 PostgreSQL 或 Redis；需要 M02/M13 在 Docker 环境中进行真实服务集成测试。
- `candles` 当前是通用 SQLAlchemy 表；TimescaleDB hypertable 转换应放在部署迁移或 M13 运维脚本中，并在真实 PostgreSQL 环境验收。
- Alembic 初始迁移以 `Base.metadata` 为单一来源创建表；后续增量迁移必须使用显式 `op.add_column`/`op.create_index` 等操作，不能改写已发布的 0001 迁移。
