"""SQLite response cache with TTL expiration for free open API clients."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class CacheEntry:
    """Cached API payload and retrieval metadata."""

    key: str
    namespace: str
    payload: Any
    source_name: str
    retrieved_at: datetime


class SQLiteCache:
    """Thread-safe SQLite key-value cache with per-entry TTL."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_cache (
                    cache_key TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_cache_namespace ON api_cache (namespace)"
            )

    def get(self, cache_key: str) -> CacheEntry | None:
        """Return a non-expired CacheEntry or None on miss/expiration."""
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT namespace, payload_json, source_name, created_at, expires_at
                FROM api_cache
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            namespace, payload_json, source_name, created_at, expires_at = row
            if expires_at <= now:
                conn.execute("DELETE FROM api_cache WHERE cache_key = ?", (cache_key,))
                return None
            return CacheEntry(
                key=cache_key,
                namespace=namespace,
                payload=json.loads(payload_json),
                source_name=source_name,
                retrieved_at=datetime.fromtimestamp(created_at, tz=timezone.utc),
            )

    def set(
        self,
        cache_key: str,
        namespace: str,
        payload: Any,
        source_name: str,
        ttl_seconds: int = 86400,
    ) -> datetime:
        """Store or overwrite a JSON-serializable payload with a TTL in seconds."""
        now = time.time()
        expires_at = now + max(1, ttl_seconds)
        payload_json = json.dumps(payload, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO api_cache (cache_key, namespace, payload_json, source_name, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    namespace = excluded.namespace,
                    payload_json = excluded.payload_json,
                    source_name = excluded.source_name,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (cache_key, namespace, payload_json, source_name, now, expires_at),
            )
        return datetime.fromtimestamp(now, tz=timezone.utc)

    def delete(self, cache_key: str) -> None:
        """Delete a single entry if present."""
        with self._connect() as conn:
            conn.execute("DELETE FROM api_cache WHERE cache_key = ?", (cache_key,))

    def clear(self, namespace: str | None = None) -> None:
        """Clear all entries or all entries within a namespace."""
        with self._connect() as conn:
            if namespace is None:
                conn.execute("DELETE FROM api_cache")
            else:
                conn.execute("DELETE FROM api_cache WHERE namespace = ?", (namespace,))
