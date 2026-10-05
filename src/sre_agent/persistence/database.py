"""Async engine + schema bootstrap for the incident store."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sre_agent.persistence.tables import Base


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, future=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
