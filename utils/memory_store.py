from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_MEMORY_DB_PATH = "./databases/user_memories.db"
MAX_MEMORY_LENGTH = 4000


class MemoryStoreError(Exception):
    pass


class MemoryStore:
    def __init__(self, path: str | Path = DEFAULT_MEMORY_DB_PATH, key: str | None = None):
        self.path = Path(path)
        raw_key = key or os.getenv("MEMORY_ENCRYPTION_KEY", "")
        if not raw_key:
            raise MemoryStoreError("缺少 MEMORY_ENCRYPTION_KEY")
        try:
            self.fernet = Fernet(raw_key.encode("utf-8"))
        except Exception as exc:
            raise MemoryStoreError("MEMORY_ENCRYPTION_KEY 格式不合法") from exc
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def get_memory(self, user_id) -> str:
        encrypted = self._fetch_encrypted(user_id)
        if not encrypted:
            return ""
        try:
            return self.fernet.decrypt(encrypted.encode("utf-8")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError):
            return ""

    def set_memory(self, user_id, memory: str) -> None:
        normalized = _normalize_memory(memory)
        if not normalized:
            return
        encrypted = self.fernet.encrypt(normalized.encode("utf-8")).decode("utf-8")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_memories(userID, memory, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(userID) DO UPDATE SET memory=excluded.memory, updated_at=excluded.updated_at
                """,
                (str(user_id), encrypted, int(time.time())),
            )

    def reset_memory(self, user_id) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM user_memories WHERE userID = ?", (str(user_id),))

    def _fetch_encrypted(self, user_id) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT memory FROM user_memories WHERE userID = ?", (str(user_id),)).fetchone()
        return str(row[0]) if row else ""

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_memories(
                    userID TEXT PRIMARY KEY,
                    memory TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def _connect(self):
        return sqlite3.connect(self.path)


def _normalize_memory(memory: str) -> str:
    text = str(memory or "").strip()
    if len(text) > MAX_MEMORY_LENGTH:
        return text[:MAX_MEMORY_LENGTH]
    return text
