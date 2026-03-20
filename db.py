"""Database connection management for ARIA.

Provides sync connections to PostgreSQL via a connection pool.
Used by all stores (daemon.py and tick.py).
"""

import logging
from datetime import date as _date, time as _time, datetime as _datetime

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import config

log = logging.getLogger("aria.db")

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Get or create the connection pool."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            config.DATABASE_URL,
            min_size=2,
            max_size=10,
            kwargs={"row_factory": dict_row, "autocommit": True},
        )
        log.info("Database connection pool created")
    return _pool


def get_conn():
    """Get a connection from the pool. Use as context manager.

    Usage:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT ...").fetchall()
    """
    return get_pool().connection()


def serialize_row(row: dict) -> dict:
    """Convert a database row to a dict matching the JSON store format.

    Converts date/time/datetime types to ISO format strings so that
    existing code (string comparisons, f-string formatting) works unchanged.
    """
    result = {}
    for key, val in row.items():
        if isinstance(val, _datetime):
            result[key] = val.isoformat()
        elif isinstance(val, _date):
            result[key] = val.isoformat()
        elif isinstance(val, _time):
            result[key] = val.strftime("%H:%M")
        else:
            result[key] = val
    return result


def close():
    """Close the connection pool."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        log.info("Database connection pool closed")
