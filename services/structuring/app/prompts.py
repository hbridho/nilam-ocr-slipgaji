"""Where the LLM prompt comes from: a file next to the service (default) or the `system_prompt` table, optionally
behind Redis.

The prompt is never in code or in settings — only a pointer is:

    PROMPT_SOURCE=file  PROMPT_PATH=/app/prompts/slip_gaji.v1.md          (default; or a mounted volume)
    PROMPT_SOURCE=db    PROMPT_VERSION=                                    (the one `is_active` row)
    PROMPT_SOURCE=db    PROMPT_VERSION=3                                   (that version, active or not)

`system_prompt` is the table agreed for every NILAM document (team DDL, 6 Oct 2026), created by migration 0010
in this repository's schema (`nilam_ocr_slipgaji`): `version` (identity), `system_prompt`, `is_active` (at most
one active row), `change_note`, `created_at`. A new prompt is a new row; switching prompts is flipping
`is_active`:

    BEGIN;
    UPDATE nilam_ocr_slipgaji.system_prompt SET is_active = FALSE WHERE is_active;
    INSERT INTO nilam_ocr_slipgaji.system_prompt (system_prompt, is_active, change_note)
         VALUES ('<text>', TRUE, 'slip gaji v2: ...');
    COMMIT;

The text is read once, at start-up, like the file, and the prompt in force is logged (`PROMPT_META`, with its
version). Without Redis a running service never changes prompt mid-flight: restart the container (or roll the
pods) to pick a new row up.

If the table cannot be read at start-up, `PROMPT_DB_FALLBACK_TO_FILE=true` (default) falls back to
`PROMPT_PATH` with a warning, so a database outage does not keep structuring down; `false` refuses to start.

With Redis (STRUCTURING_PROMPT_REDIS_ENABLED=true, PROMPT_SOURCE=db, PROMPT_VERSION empty) the active row is checked
before every job, the way nilam-ocr-shm does it (`RedisPrompt`):

    key    STRUCTURING_PROMPT_REDIS_KEY, the team's `ocr:prompt:<document>`: ocr:prompt:slipgaji
    value  the active row as JSON: {"version", "system_prompt", "change_note", "created_at"}
    TTL    STRUCTURING_PROMPT_REDIS_TTL_SECONDS, set when the service writes the key (3600; 0 = never expires)

A hit is the prompt. A miss (or a value that cannot be read) reads the active row from the table and writes it to
the key. So a new row reaches the running service once the key is gone: deleted by whoever activated the row (the
central Orkestrasi, or `redis-cli DEL ocr:prompt:slipgaji`), or expired. Redis unreachable, or the table unreachable
on a miss: the prompt in use stays, with a warning — the cache never fails a job.
"""

import asyncio
import concurrent.futures
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "slip_gaji.v1.md"
DEFAULT_REDIS_KEY = "ocr:prompt:slipgaji"
DEFAULT_REDIS_TTL_SECONDS = 3600


class PromptNotFound(LookupError):
    """No usable row for the requested prompt."""


@dataclass(frozen=True)
class PromptRow:
    """One row of `system_prompt`."""

    version: int
    system_prompt: str
    change_note: str | None = None
    created_at: datetime | None = None


def _datetime(value: Any) -> datetime | None:
    """`created_at` as the database gives it: a datetime from Postgres, ISO text from SQLite (the tests)."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


async def _read_row(database_url: str, table: str, version: int | None) -> PromptRow:
    from sqlalchemy import text

    from ocr_common.pipeline.database import get_engine

    columns = "system_prompt, version, change_note, created_at"
    async with get_engine(database_url).connect() as conn:
        if version is None:
            query = text(f"SELECT {columns} FROM {table} WHERE is_active")  # noqa: S608
            row = (await conn.execute(query)).first()
        else:
            query = text(f"SELECT {columns} FROM {table} WHERE version = :version")  # noqa: S608
            row = (await conn.execute(query, {"version": version})).first()
    if row is None or not (row[0] or "").strip():
        raise PromptNotFound(f"no prompt {'version ' + str(version) if version else 'is_active'} in {table}")
    return PromptRow(int(row[1]), row[0], row[2], _datetime(row[3]))


async def _fetch(database_url: str, table: str, version: int | None) -> PromptRow:
    """`_read_row` on a loop of its own, closing the engines it made there."""
    from ocr_common.pipeline.database import dispose_engines

    try:
        return await _read_row(database_url, table, version)
    finally:
        await dispose_engines()


def read_db_row(database_url: str, table: str, version: int | None, timeout: float = 10.0) -> PromptRow:
    """One `system_prompt` row, synchronously (the prompt resolver is sync and may run inside an event loop, so it
    runs on its own loop in a worker thread). Start-up only: it closes every engine of the process when done."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _fetch(database_url, table, version))
        return future.result(timeout=timeout)


def read_db_prompt(database_url: str, table: str, version: int | None, timeout: float = 10.0) -> tuple[str, int]:
    """(text, version) of one `system_prompt` row (see `read_db_row`)."""
    row = read_db_row(database_url, table, version, timeout)
    return row.system_prompt, row.version


def register_db_prompt_source(
    *, database_url: str | None, table: str, fallback_path: str | None, fallback: bool
) -> None:
    """Register `PROMPT_SOURCE=db` with the research layer's prompt registry (`core.config`)."""
    from slip_ml.vendor import ensure_path

    ensure_path()
    from core import config as _config

    def resolve(spec: dict[str, Any], base: Path) -> str:
        version = spec.get("version")
        try:
            if not database_url:
                raise PromptNotFound("PROMPT_SOURCE=db needs DATABASE_URL")
            text, found = read_db_prompt(database_url, table, int(version) if version not in (None, "") else None)
            logger.info("structuring: prompt from %s, version %s (%d characters)", table, found, len(text))
            return text
        except Exception as exc:
            if not fallback or not fallback_path:
                raise
            logger.warning(
                "prompt version %s not readable from %s (%s); using %s", version or "active", table, exc, fallback_path
            )
            return Path(fallback_path).read_text(encoding="utf-8")

    _config.register_prompt_source("db", resolve)


# --- Redis in front of the table ------------------------------------------------------------------------------


class RedisClient(Protocol):
    async def get(self, name: str) -> Any: ...

    async def set(self, name: str, value: str, ex: int | None = None) -> Any: ...


def encode(row: PromptRow) -> str:
    """The key's value: the row as JSON, the shape nilam-ocr-shm writes."""
    return json.dumps(
        {
            "version": row.version,
            "system_prompt": row.system_prompt,
            "change_note": row.change_note,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
    )


def decode(raw: str | bytes) -> PromptRow:
    value = json.loads(raw)
    if not str(value["system_prompt"]).strip():
        raise ValueError("empty system_prompt")
    return PromptRow(
        version=int(value["version"]),
        system_prompt=str(value["system_prompt"]),
        change_note=value.get("change_note"),
        created_at=_datetime(value.get("created_at")),
    )


class RedisPrompt:
    """The active row through Redis, checked before every job (`refresh`), on the service's event loop."""

    def __init__(
        self,
        redis: RedisClient,
        *,
        database_url: str,
        table: str,
        name: str = "slip_gaji",
        key: str = DEFAULT_REDIS_KEY,
        ttl_seconds: int = DEFAULT_REDIS_TTL_SECONDS,
    ):
        self._redis = redis
        self._database_url = database_url
        self._table = table
        self._name = name
        self._key = key
        self._ttl = ttl_seconds or None

    async def active(self) -> tuple[PromptRow, str]:
        """(the active row, where it came from: "redis" or "table"). A miss reads the table and fills the key.
        Raises when Redis is unreachable, or when the table cannot be read on a miss."""
        raw = await self._redis.get(self._key)
        if raw:
            try:
                return decode(raw), "redis"
            except (ValueError, KeyError, TypeError):
                logger.warning("prompt cache: unreadable value under %s; reading the table", self._key)
        row = await _read_row(self._database_url, self._table, None)
        try:
            await self._redis.set(self._key, encode(row), ex=self._ttl)
        except Exception as exc:
            logger.warning("prompt cache: could not write %s (%s)", self._key, exc)
        return row, "table"

    async def refresh(self) -> None:
        """Before a job: put the active row in force when it is not the prompt in use. Never fails the job."""
        from slip_ml import runtime

        text, meta = runtime.current_prompt()
        try:
            row, origin = await self.active()
        except Exception as exc:
            logger.warning(
                "prompt cache: %s unreadable (%s: %s); still using prompt version %s",
                self._key,
                type(exc).__name__,
                exc,
                meta.get("version") or "-",
            )
            return
        if row.system_prompt == text and row.version == meta.get("version"):
            return
        runtime.use_prompt(row.system_prompt, {"source": "db", "name": self._name, "version": row.version})
        logger.info(
            "structuring: prompt version %s from %s %s (%d characters, was version %s)",
            row.version,
            origin,
            self._key if origin == "redis" else self._table,
            len(row.system_prompt),
            meta.get("version") or "-",
        )
