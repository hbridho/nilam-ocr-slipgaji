"""The prompt lives outside the code: a file next to the service by default, or a `system_prompt` row (the table
agreed for every NILAM document)."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.prompts import DEFAULT_PROMPT_PATH, read_db_prompt, register_db_prompt_source

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
