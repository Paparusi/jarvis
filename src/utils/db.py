"""Database connection pool for JARVIS."""

from __future__ import annotations

import asyncpg

from src.utils.config import get_database_url
from src.utils.logging import get_logger

log = get_logger("db")

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the database connection pool."""
    global _pool
    if _pool is None:
        dsn = get_database_url()
        _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
        log.info("database_pool_created", dsn=dsn.split("@")[-1])
    return _pool


async def close_pool() -> None:
    """Close the database connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("database_pool_closed")


async def execute(query: str, *args) -> str:
    """Execute a query and return status."""
    pool = await get_pool()
    return await pool.execute(query, *args)


async def fetch(query: str, *args) -> list[asyncpg.Record]:
    """Execute a query and return all rows."""
    pool = await get_pool()
    return await pool.fetch(query, *args)


async def fetchrow(query: str, *args) -> asyncpg.Record | None:
    """Execute a query and return first row."""
    pool = await get_pool()
    return await pool.fetchrow(query, *args)
