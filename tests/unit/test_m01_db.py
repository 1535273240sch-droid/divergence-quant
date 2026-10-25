from sqlalchemy import create_engine

from dquant.db import Base
from dquant.db.redis import create_redis_client
from dquant.db.session import create_database_engine
from dquant.settings import DatabaseSettings, RedisSettings

EXPECTED_TABLES = {
    "instruments",
    "candles",
    "regime_snapshots",
    "divergence_signals",
    "screener_runs",
    "screener_results",
    "strategy_signals",
    "risk_decisions",
    "orders",
    "fills",
    "positions",
    "equity_snapshots",
    "risk_events",
    "audit_logs",
    "signal_confirmations",
    "golden_samples",
    "divergence_feedback",
    "symbol_cooldowns",
    "ai_reviews",
    "ai_optimization_proposals",
    "ai_call_logs",
    "macro_events",
    "notify_outbox",
    "backtest_runs",
}


def test_metadata_contains_plan_tables_and_can_create_on_sqlite() -> None:
    assert EXPECTED_TABLES.issubset(Base.metadata.tables)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    assert set(Base.metadata.tables).issuperset(EXPECTED_TABLES)
    engine.dispose()


def test_async_database_engine_is_created_without_connecting() -> None:
    engine = create_database_engine(
        DatabaseSettings(url="postgresql+asyncpg://dquant@localhost:5432/dquant")
    )

    assert str(engine.url) == "postgresql+asyncpg://dquant@localhost:5432/dquant"


def test_redis_client_uses_configured_url() -> None:
    client = create_redis_client(RedisSettings(url="redis://example.test:6379/3"))

    assert client.connection_pool.connection_kwargs["host"] == "example.test"
    assert client.connection_pool.connection_kwargs["db"] == 3
