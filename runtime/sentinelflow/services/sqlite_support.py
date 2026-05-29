from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


# 256 MiB memory-mapped reads and a larger page cache keep large-DB reads fast;
# wal_autocheckpoint bounds the -wal sidecar under heavy write load.
_MMAP_SIZE_BYTES = 256 * 1024 * 1024
_CACHE_SIZE_KIB = -64 * 1024  # negative => KiB of cache (~64 MiB)


def open_sqlite_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    # Takes effect on fresh databases (before tables exist); existing databases
    # need a one-off manual VACUUM to switch, which is documented as low-peak ops.
    conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    conn.execute(f"PRAGMA mmap_size={_MMAP_SIZE_BYTES}")
    conn.execute(f"PRAGMA cache_size={_CACHE_SIZE_KIB}")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
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
