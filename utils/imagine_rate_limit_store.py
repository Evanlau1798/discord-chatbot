from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IMAGINE_RATE_LIMIT_DB_PATH = Path("databases/imagine_rate_limits.db")
WINDOW_SECONDS = 24 * 60 * 60
DEFAULT_DAILY_LIMIT = 3
TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


@dataclass(frozen=True)
class ImagineRateLimitConfig:
    enabled: bool
    daily_limit: int
    whitelist_ids: frozenset[str]


@dataclass(frozen=True)
class ImagineQuotaStatus:
    allowed: bool
    unlimited: bool
    limit: int
    used_count: int
    remaining: int
    reset_at: int | None


class ImagineRateLimiter:
    def __init__(
        self,
        path: str | Path = DEFAULT_IMAGINE_RATE_LIMIT_DB_PATH,
        *,
        config: ImagineRateLimitConfig | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.path = Path(path)
        self.config = config or get_imagine_rate_limit_config()
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def check(self, user_id) -> ImagineQuotaStatus:
        if self._is_unlimited(user_id):
            return self._unlimited_status()
        row = self._fetch_row(user_id)
        now = self._now()
        if row is None or _window_expired(row["window_started_at"], now):
            return ImagineQuotaStatus(True, False, self.config.daily_limit, 0, self.config.daily_limit, None)
        remaining = max(0, self.config.daily_limit - row["used_count"])
        return ImagineQuotaStatus(
            allowed=remaining > 0,
            unlimited=False,
            limit=self.config.daily_limit,
            used_count=row["used_count"],
            remaining=remaining,
            reset_at=row["window_started_at"] + WINDOW_SECONDS,
        )

    def record_success(self, user_id) -> ImagineQuotaStatus:
        if self._is_unlimited(user_id):
            return self._unlimited_status()
        now = self._now()
        with self._connect() as conn:
            row = _row_to_dict(conn.execute(
                "SELECT window_started_at, used_count FROM imagine_rate_limits WHERE user_id = ?",
                (str(user_id),),
            ).fetchone())
            if row is None or _window_expired(row["window_started_at"], now):
                window_started_at = now
                used_count = 1
            else:
                window_started_at = row["window_started_at"]
                used_count = min(self.config.daily_limit, row["used_count"] + 1)
            conn.execute(
                """
                INSERT INTO imagine_rate_limits(user_id, window_started_at, used_count, last_used_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    window_started_at=excluded.window_started_at,
                    used_count=excluded.used_count,
                    last_used_at=excluded.last_used_at
                """,
                (str(user_id), window_started_at, used_count, now),
            )
        return self.check(user_id)

    def reset_all(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM imagine_rate_limits")
            return int(cursor.rowcount or 0)

    def raw_row_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM imagine_rate_limits").fetchone()
        return int(row[0] if row else 0)

    def _is_unlimited(self, user_id) -> bool:
        return not self.config.enabled or str(user_id) in self.config.whitelist_ids

    def _unlimited_status(self) -> ImagineQuotaStatus:
        return ImagineQuotaStatus(True, True, self.config.daily_limit, 0, self.config.daily_limit, None)

    def _fetch_row(self, user_id) -> dict[str, int] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT window_started_at, used_count FROM imagine_rate_limits WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()
        return _row_to_dict(row)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS imagine_rate_limits(
                    user_id TEXT PRIMARY KEY,
                    window_started_at INTEGER NOT NULL,
                    used_count INTEGER NOT NULL,
                    last_used_at INTEGER NOT NULL
                )
                """
            )

    def _connect(self):
        return sqlite3.connect(self.path)

    def _now(self) -> int:
        return int(self.clock())


def get_imagine_rate_limit_config(env: Mapping[str, str] | None = None) -> ImagineRateLimitConfig:
    values = os.environ if env is None else env
    return ImagineRateLimitConfig(
        enabled=_parse_bool(values.get("AI_IMAGINE_RATE_LIMIT_ENABLED"), default=False),
        daily_limit=_parse_daily_limit(values.get("AI_IMAGINE_DAILY_LIMIT")),
        whitelist_ids=frozenset(_parse_whitelist(values.get("AI_IMAGINE_RATE_LIMIT_WHITELIST", ""))),
    )


def format_imagine_rate_limit_notice(reset_at: int) -> str:
    return f"-# 您已達到每日繪圖數量限制，請<t:{int(reset_at)}:R>後再試一次"


def _parse_bool(value, *, default: bool) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def _parse_daily_limit(value) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return DEFAULT_DAILY_LIMIT
    return parsed if parsed > 0 else DEFAULT_DAILY_LIMIT


def _parse_whitelist(value: str) -> list[str]:
    return [item for item in (part.strip() for part in str(value or "").split(",")) if item.isdigit()]


def _row_to_dict(row) -> dict[str, int] | None:
    if row is None:
        return None
    return {"window_started_at": int(row[0]), "used_count": int(row[1])}


def _window_expired(window_started_at: int, now: int) -> bool:
    return now >= int(window_started_at) + WINDOW_SECONDS
