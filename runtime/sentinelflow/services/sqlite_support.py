from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def open_sqlite_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def sqlite_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_sqlite_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def sqlite_transaction(db_path: Path, *, begin_mode: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = open_sqlite_connection(db_path)
    try:
        if begin_mode:
            conn.execute(f"BEGIN {begin_mode}")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
