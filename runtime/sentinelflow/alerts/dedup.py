import sqlite3

from sentinelflow.config.runtime import CONFIG_DIR
from sentinelflow.services.sqlite_support import open_sqlite_connection, sqlite_connection, sqlite_transaction

DB_PATH = CONFIG_DIR / "sys_queue.db"

class AlertDedupStore:
    """SQLite-backed store for alert deduplication state.

    Correctness relies on SQLite primary-key conflicts and transaction semantics,
    not on an in-process Python lock. This keeps behavior consistent across
    threads and tighter under multi-process deployments.
    """

    def __init__(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with sqlite_connection(DB_PATH) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS alert_dedup (
                    event_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL
                )
            ''')

    def _get_conn(self) -> sqlite3.Connection:
        return open_sqlite_connection(DB_PATH)

    def mark_processing(self, event_id: str) -> bool:
        with sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            try:
                conn.execute("INSERT INTO alert_dedup (event_id, status) VALUES (?, ?)", (event_id, "processing"))
                return True
            except sqlite3.IntegrityError:
                return False

    def mark_done(self, event_id: str) -> None:
        with sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            conn.execute("INSERT OR REPLACE INTO alert_dedup (event_id, status) VALUES (?, ?)", (event_id, "completed"))

    def mark_failed(self, event_id: str) -> None:
        with sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            conn.execute("DELETE FROM alert_dedup WHERE event_id = ?", (event_id,))

    def is_processing(self, event_id: str) -> bool:
        with sqlite_connection(DB_PATH) as conn:
            row = conn.execute("SELECT status FROM alert_dedup WHERE event_id = ?", (event_id,)).fetchone()
            return row is not None and row[0] == "processing"

    def is_completed(self, event_id: str) -> bool:
        with sqlite_connection(DB_PATH) as conn:
            row = conn.execute("SELECT status FROM alert_dedup WHERE event_id = ?", (event_id,)).fetchone()
            return row is not None and row[0] == "completed"

    def seen(self, event_id: str) -> bool:
        with sqlite_connection(DB_PATH) as conn:
            row = conn.execute("SELECT status FROM alert_dedup WHERE event_id = ?", (event_id,)).fetchone()
            return row is not None

    def forget(self, event_id: str) -> None:
        with sqlite_transaction(DB_PATH, begin_mode="IMMEDIATE") as conn:
            conn.execute("DELETE FROM alert_dedup WHERE event_id = ?", (event_id,))
