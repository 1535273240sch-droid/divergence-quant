"""异步数据库引擎和会话边界。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from dquant.settings import DatabaseSettings


def create_database_engine(settings: DatabaseSettings) -> AsyncEngine:
    """根据配置创建异步 SQLAlchemy 引擎；不会在这里主动连接数据库。"""

    return create_async_engine(
        settings.url,
        echo=settings.echo,
        pool_pre_ping=settings.pool_pre_ping,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建事务会话工厂。"""

    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """提供一个自动提交/回滚的异步会话上下文。"""

    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_database_engine(engine: AsyncEngine) -> None:
    """释放数据库连接池。"""

    await engine.dispose()
