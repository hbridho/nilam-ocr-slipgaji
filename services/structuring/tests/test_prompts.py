"""The prompt lives outside the code: a file next to the service by default, or a `system_prompt` row (the table
agreed for every NILAM document), optionally behind Redis."""

import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.prompts import (
    DEFAULT_PROMPT_PATH,
    PromptRow,
    RedisPrompt,
    decode,
    encode,
    read_db_prompt,
    register_db_prompt_source,
)
from app.services.structuring_service import StructuringService

# The team's DDL, in SQLite: identity -> INTEGER PRIMARY KEY, partial unique index on the active row.
DDL = (
    "CREATE TABLE system_prompt (version INTEGER PRIMARY KEY, system_prompt TEXT NOT NULL, "
    "is_active BOOLEAN NOT NULL DEFAULT 0, change_note VARCHAR(100), created_at TEXT)",
    "CREATE UNIQUE INDEX uq_system_prompt_active ON system_prompt (is_active) WHERE is_active = 1",
)


@pytest.fixture
def database(tmp_path: Path) -> str:
    path = tmp_path / "prompts.db"
    with sqlite3.connect(path) as conn:
        for statement in DDL:
            conn.execute(statement)
        conn.executemany(
            "INSERT INTO system_prompt (system_prompt, is_active, change_note) VALUES (?, ?, ?)",
            [
                ("PROMPT V1 {n} {fields} {text}", 0, "awal"),
                ("PROMPT V2 {n} {fields} {text}", 1, "aktif"),
                ("PROMPT V3 draft", 0, "draf"),
            ],
        )
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def test_the_default_prompt_is_a_file_next_to_the_service():
    settings = Settings(api_key="x", environment="local", _env_file=None)

    assert settings.prompt_source == "file"
    assert Path(settings.prompt_path) == DEFAULT_PROMPT_PATH
    assert settings.prompt_db_table == "nilam_ocr_slipgaji.system_prompt"
    assert "{text}" in DEFAULT_PROMPT_PATH.read_text(encoding="utf-8"), "the template keeps its placeholders"


def test_the_table_allows_one_active_row_only(database):
    path = database.removeprefix("sqlite+aiosqlite:///")
    with sqlite3.connect(path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE system_prompt SET is_active = 1 WHERE version = 1")


def test_a_db_prompt_is_the_active_row_or_the_version_asked_for(database):
    assert read_db_prompt(database, "system_prompt", None) == ("PROMPT V2 {n} {fields} {text}", 2)
    assert read_db_prompt(database, "system_prompt", 3) == ("PROMPT V3 draft", 3)


def test_switching_to_the_database_changes_the_prompt_in_use(database):
    from slip_ml import runtime

    register_db_prompt_source(database_url=database, table="system_prompt", fallback_path=None, fallback=False)
    try:
        report = runtime.configure(prompt={"source": "db", "name": "slip_gaji"})
        assert report["prompt"]["source"] == "db"
        assert report["state"]["prompt_chars"] == len("PROMPT V2 {n} {fields} {text}")
    finally:
        runtime.configure(
            prompt={"source": "file", "path": str(DEFAULT_PROMPT_PATH), "name": "slip_gaji", "version": 1}
        )


def test_an_unreadable_database_falls_back_to_the_file_or_refuses(tmp_path):
    from slip_ml import runtime

    missing = f"sqlite+aiosqlite:///{(tmp_path / 'nope' / 'x.db').as_posix()}"
    register_db_prompt_source(
        database_url=missing, table="system_prompt", fallback_path=str(DEFAULT_PROMPT_PATH), fallback=True
    )
    try:
        report = runtime.configure(prompt={"source": "db", "name": "slip_gaji", "version": 9})
        assert report["state"]["prompt_chars"] == len(DEFAULT_PROMPT_PATH.read_text(encoding="utf-8"))

        register_db_prompt_source(database_url=missing, table="system_prompt", fallback_path=None, fallback=False)
        with pytest.raises((SQLAlchemyError, LookupError)):
            runtime.configure(prompt={"source": "db", "name": "slip_gaji", "version": 9})
    finally:
        runtime.configure(
            prompt={"source": "file", "path": str(DEFAULT_PROMPT_PATH), "name": "slip_gaji", "version": 1}
        )


def test_db_without_a_database_url_and_no_fallback_refuses_to_start():
    with pytest.raises(ValueError, match="PROMPT_SOURCE=db needs DATABASE_URL"):
        Settings(api_key="x", environment="local", _env_file=None, prompt_source="db", prompt_db_fallback_to_file=False)


# --- Redis in front of the table (nilam-ocr-shm's pattern, key ocr:prompt:slipgaji) ---------------------------


class FakeRedis:
    def __init__(self, *, down: bool = False):
        self.values: dict[str, str] = {}
        self.expiry: dict[str, int | None] = {}
        self.down = down

    async def get(self, name: str):
        if self.down:
            raise ConnectionError("redis is down")
        return self.values.get(name)

    async def set(self, name: str, value: str, ex: int | None = None):
        if self.down:
            raise ConnectionError("redis is down")
        self.values[name], self.expiry[name] = value, ex


@pytest.fixture
def prompt_in_use():
    """Put back the prompt the research layer calls with: it is shared by every test of this service."""
    from slip_ml import runtime

    text, meta = runtime.current_prompt()
    yield
    runtime.use_prompt(text, meta)


@pytest.fixture
async def engines():
    from ocr_common.pipeline.database import dispose_engines

    yield
    await dispose_engines()


def _cache(redis: FakeRedis, database_url: str, **kwargs) -> RedisPrompt:
    return RedisPrompt(redis, database_url=database_url, table="system_prompt", **kwargs)


def _settings(**kwargs) -> Settings:
    return Settings(api_key="x", environment="local", _env_file=None, **kwargs)


def test_the_redis_cache_is_off_by_default_and_keyed_with_the_team_prefix():
    settings = _settings()

    assert settings.structuring_prompt_redis_enabled is False
    assert settings.structuring_prompt_redis_key == "ocr:prompt:slipgaji"
    assert settings.structuring_prompt_redis_ttl_seconds == 3600


def test_the_redis_cache_needs_a_url_and_the_active_row_of_the_table(database):
    on = {"structuring_prompt_redis_enabled": True}
    with pytest.raises(ValueError, match="STRUCTURING_REDIS_URL is required"):
        _settings(**on, prompt_source="db", database_url=database)
    on["structuring_redis_url"] = "redis://redis:6379/0"
    with pytest.raises(ValueError, match="PROMPT_SOURCE=db"):
        _settings(**on, database_url=database)
    with pytest.raises(ValueError, match="PROMPT_VERSION empty"):
        _settings(**on, prompt_source="db", prompt_version=2, database_url=database)
    with pytest.raises(ValueError, match="CLOUDSQL_INSTANCE or DATABASE_URL"):
        _settings(**on, prompt_source="db")

    assert _settings(**on, prompt_source="db", database_url=database).structuring_prompt_redis_enabled


async def test_a_miss_reads_the_active_row_and_fills_the_key(database, engines):
    redis = FakeRedis()

    row, origin = await _cache(redis, database).active()

    assert (row.version, row.system_prompt, origin) == (2, "PROMPT V2 {n} {fields} {text}", "table")
    stored = json.loads(redis.values["ocr:prompt:slipgaji"])
    assert stored == {
        "version": 2,
        "system_prompt": "PROMPT V2 {n} {fields} {text}",
        "change_note": "aktif",
        "created_at": None,
    }
    assert redis.expiry["ocr:prompt:slipgaji"] == 3600


async def test_a_hit_is_used_without_reading_the_table(tmp_path):
    redis = FakeRedis()
    redis.values["ocr:prompt:slipgaji"] = encode(PromptRow(7, "FROM REDIS {text}", "v7"))
    no_database = f"sqlite+aiosqlite:///{(tmp_path / 'nope' / 'x.db').as_posix()}"

    row, origin = await _cache(redis, no_database).active()

    assert (row.version, row.system_prompt, row.change_note, origin) == (7, "FROM REDIS {text}", "v7", "redis")


async def test_an_unreadable_value_is_replaced_by_the_table_row(database, engines):
    redis = FakeRedis()
    redis.values["ocr:prompt:slipgaji"] = '{"version": 1}'

    row, origin = await _cache(redis, database, ttl_seconds=0).active()

    assert (row.version, origin) == (2, "table")
    assert decode(redis.values["ocr:prompt:slipgaji"]).version == 2
    assert redis.expiry["ocr:prompt:slipgaji"] is None, "TTL 0 = the key never expires"


async def test_a_new_key_puts_its_prompt_in_force_without_a_restart(database, engines, prompt_in_use):
    from slip_ml import runtime

    redis = FakeRedis()
    cache = _cache(redis, database)

    await cache.refresh()
    assert runtime.current_prompt() == (
        "PROMPT V2 {n} {fields} {text}",
        {"source": "db", "name": "slip_gaji", "version": 2},
    )

    # The administrators activate version 3 and replace the key (or delete it and let the table refill it).
    redis.values["ocr:prompt:slipgaji"] = encode(PromptRow(3, "PROMPT V3 {n} {fields} {text}"))
    await cache.refresh()
    assert runtime.current_prompt()[0] == "PROMPT V3 {n} {fields} {text}"
    assert runtime.state()["prompt"]["version"] == 3


async def test_redis_or_the_table_down_keeps_the_prompt_in_use(tmp_path, prompt_in_use):
    from slip_ml import runtime

    before = runtime.current_prompt()
    no_database = f"sqlite+aiosqlite:///{(tmp_path / 'nope' / 'x.db').as_posix()}"

    await _cache(FakeRedis(down=True), no_database).refresh()
    assert runtime.current_prompt() == before

    await _cache(FakeRedis(), no_database).refresh()  # a miss, and the table cannot be read
    assert runtime.current_prompt() == before


async def test_every_job_checks_the_key_before_it_structures():
    calls: list[str] = []

    class Prompt:
        async def refresh(self):
            calls.append("refresh")

    class Structurer:
        name = "fake"

        def structure(self, pages):
            calls.append("structure")
            return {"slips": [], "n_slips": 0}

    service = StructuringService(Structurer(), Prompt())  # ty: ignore[invalid-argument-type]
    await service.run([{"page": 1, "text": "GAJI BERSIH 1.000.000", "confidence": {}}])

    assert calls == ["refresh", "structure"]
