"""Where the LLM prompt comes from: a file next to the service (default) or a database row.

The prompt is never in code or in settings — only a pointer is:

    PROMPT_SOURCE=file  PROMPT_PATH=/app/prompts/slip_gaji.v1.md      (default; or a mounted ConfigMap)
    PROMPT_SOURCE=db    PROMPT_NAME=slip_gaji  PROMPT_VERSION=2       (row in table `prompts`)

`PROMPT_VERSION` unset with `db` takes the newest `active` row of that name. The `prompts` table is created
by migration 0010 (`db/migrations`); a new prompt is a new row, so the history stays in the database:

    INSERT INTO prompts (name, version, body, active) VALUES ('slip_gaji', 2, '...', true);

The text is read once, at start-up, like the file: a running service never changes prompt mid-flight, and
the prompt that produced a result is the one logged at start (`PROMPT_META`). Restart (or roll) the pods to
pick a new row up.

If the database cannot be read at start-up, `PROMPT_DB_FALLBACK_TO_FILE=true` (default) falls back to
`PROMPT_PATH` with a warning, so a database outage does not keep structuring down; `false` refuses to start
instead, for deployments where only the database prompt is acceptable.
"""

import asyncio
import concurrent.futures
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "slip_gaji.v1.md"


class PromptNotFound(LookupError):
    """No usable row for the requested prompt."""


async def _fetch(database_url: str, table: str, name: str, version: int | None) -> str:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            if version is None:
                query = text(
                    f"SELECT body FROM {table} WHERE name = :name AND active ORDER BY version DESC LIMIT 1"  # noqa: S608
                )
                row = (await conn.execute(query, {"name": name})).first()
            else:
                query = text(f"SELECT body FROM {table} WHERE name = :name AND version = :version")  # noqa: S608
                row = (await conn.execute(query, {"name": name, "version": version})).first()
    finally:
        await engine.dispose()
    if row is None or not (row[0] or "").strip():
        raise PromptNotFound(f"no prompt {name!r} version {version or 'active'} in table {table}")
    return row[0]


def read_db_prompt(database_url: str, table: str, name: str, version: int | None, timeout: float = 5.0) -> str:
    """Read one prompt row synchronously (the prompt resolver is sync, and may run inside an event loop)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _fetch(database_url, table, name, version))
        return future.result(timeout=timeout)


def register_db_prompt_source(
    *, database_url: str | None, table: str, fallback_path: str | None, fallback: bool
) -> None:
    """Register `PROMPT_SOURCE=db` with the research layer's prompt registry (`core.config`)."""
    from slip_ml.vendor import ensure_path

    ensure_path()
    from core import config as _config

    def resolve(spec: dict[str, Any], base: Path) -> str:
        name = spec.get("name") or "slip_gaji"
        version = spec.get("version")
        try:
            if not database_url:
                raise PromptNotFound("PROMPT_SOURCE=db needs DATABASE_URL")
            return read_db_prompt(database_url, table, name, int(version) if version is not None else None)
        except Exception as exc:
            if not fallback or not fallback_path:
                raise
            logger.warning(
                "prompt %s v%s not readable from the database (%s); using %s", name, version, exc, fallback_path
            )
            return Path(fallback_path).read_text(encoding="utf-8")

    _config.register_prompt_source("db", resolve)
