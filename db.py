"""Database connection management for ARIA.

Provides sync connections to PostgreSQL via a connection pool.
Used by all stores (daemon.py and tick.py).
"""

import atexit
import logging
from contextlib import contextmanager
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
    Timezone-aware datetimes are converted to naive local time to match
    the original JSON behavior (all code uses datetime.now() which is naive).
    """
    result = {}
    for key, val in row.items():
        if isinstance(val, _datetime):
            if val.tzinfo is not None:
                val = val.astimezone().replace(tzinfo=None)
            result[key] = val.isoformat()
        elif isinstance(val, _date):
            result[key] = val.isoformat()
        elif isinstance(val, _time):
            result[key] = val.strftime("%H:%M")
        else:
            result[key] = val
    return result


@contextmanager
def get_transaction():
    """Get a connection with autocommit disabled for multi-statement transactions.

    All statements within the block are committed together on success,
    or rolled back on exception. Use for operations that must be atomic
    (e.g., logging related entries to multiple tables).

    Usage:
        with db.get_transaction() as conn:
            conn.execute("INSERT INTO table_a ...")
            conn.execute("INSERT INTO table_b ...")
            # both commit on exit, or both roll back on exception
    """
    pool = get_pool()
    conn = pool.getconn()
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = True
        pool.putconn(conn)


def close():
    """Close the connection pool."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        log.info("Database connection pool closed")


atexit.register(close)
