"""Where the LLM prompt comes from: a file next to the service (default) or the `system_prompt` table.

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

The text is read once, at start-up, like the file: a running service never changes prompt mid-flight, and the
prompt that produced a result is the one logged at start (`PROMPT_META`, with its version). Restart the
container (or roll the pods) to pick a new row up.

If the table cannot be read at start-up, `PROMPT_DB_FALLBACK_TO_FILE=true` (default) falls back to
`PROMPT_PATH` with a warning, so a database outage does not keep structuring down; `false` refuses to start.
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


async def _fetch(database_url: str, table: str, version: int | None) -> tuple[str, int]:
    from sqlalchemy import text

    from ocr_common.pipeline.database import dispose_engines, get_engine

    try:
        async with get_engine(database_url).connect() as conn:
            if version is None:
                query = text(f"SELECT system_prompt, version FROM {table} WHERE is_active")  # noqa: S608
                row = (await conn.execute(query)).first()
            else:
                query = text(f"SELECT system_prompt, version FROM {table} WHERE version = :version")  # noqa: S608
                row = (await conn.execute(query, {"version": version})).first()
    finally:
        await dispose_engines()
    if row is None or not (row[0] or "").strip():
        raise PromptNotFound(f"no prompt {'version ' + str(version) if version else 'is_active'} in {table}")
    return row[0], int(row[1])


def read_db_prompt(database_url: str, table: str, version: int | None, timeout: float = 10.0) -> tuple[str, int]:
    """(text, version) of one `system_prompt` row, synchronously (the prompt resolver is sync and may run inside
    an event loop, so it runs on its own loop in a worker thread)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _fetch(database_url, table, version))
        return future.result(timeout=timeout)


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
