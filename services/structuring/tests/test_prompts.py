"""The prompt lives outside the code: a file next to the service by default, or a database row."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.prompts import DEFAULT_PROMPT_PATH, read_db_prompt, register_db_prompt_source

DDL = (
    "CREATE TABLE prompts (id INTEGER PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL, "
    "body TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT 1, note TEXT, created_at TEXT)"
)


@pytest.fixture
def database(tmp_path: Path) -> str:
    path = tmp_path / "prompts.db"
    with sqlite3.connect(path) as conn:
        conn.execute(DDL)
        conn.executemany(
            "INSERT INTO prompts (name, version, body, active) VALUES (?, ?, ?, ?)",
            [
                ("slip_gaji", 1, "PROMPT V1 {n} {fields} {text}", 1),
                ("slip_gaji", 2, "PROMPT V2 {n} {fields} {text}", 1),
                ("slip_gaji", 3, "PROMPT V3 draft", 0),
            ],
        )
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def test_the_default_prompt_is_a_file_next_to_the_service():
    settings = Settings(api_key="x", environment="local", _env_file=None)

    assert (settings.prompt_source, settings.prompt_name) == ("file", "slip_gaji")
    assert Path(settings.prompt_path) == DEFAULT_PROMPT_PATH
    text = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")
    assert "{text}" in text, "the template keeps its placeholders"


def test_a_db_prompt_is_read_by_version_or_newest_active(database):
    assert read_db_prompt(database, "prompts", "slip_gaji", 1).startswith("PROMPT V1")
    assert read_db_prompt(database, "prompts", "slip_gaji", None).startswith("PROMPT V2"), "v3 is not active"


def test_switching_to_the_database_changes_the_prompt_in_use(database):
    from slip_ml import runtime

    register_db_prompt_source(database_url=database, table="prompts", fallback_path=None, fallback=False)
    try:
        report = runtime.configure(prompt={"source": "db", "name": "slip_gaji", "version": 2})
        assert report["prompt"] == {"source": "db", "name": "slip_gaji", "version": 2}
        assert report["state"]["prompt_chars"] == len("PROMPT V2 {n} {fields} {text}")
    finally:
        runtime.configure(
            prompt={"source": "file", "path": str(DEFAULT_PROMPT_PATH), "name": "slip_gaji", "version": 1}
        )


def test_an_unreadable_database_falls_back_to_the_file_or_refuses(tmp_path):
    from slip_ml import runtime

    missing = f"sqlite+aiosqlite:///{(tmp_path / 'nope' / 'x.db').as_posix()}"
    register_db_prompt_source(
        database_url=missing, table="prompts", fallback_path=str(DEFAULT_PROMPT_PATH), fallback=True
    )
    try:
        report = runtime.configure(prompt={"source": "db", "name": "slip_gaji", "version": 9})
        assert report["state"]["prompt_chars"] == len(DEFAULT_PROMPT_PATH.read_text(encoding="utf-8"))

        register_db_prompt_source(database_url=missing, table="prompts", fallback_path=None, fallback=False)
        with pytest.raises((SQLAlchemyError, LookupError)):
            runtime.configure(prompt={"source": "db", "name": "slip_gaji", "version": 9})
    finally:
        runtime.configure(
            prompt={"source": "file", "path": str(DEFAULT_PROMPT_PATH), "name": "slip_gaji", "version": 1}
        )


def test_db_without_a_database_url_and_no_fallback_refuses_to_start():
    with pytest.raises(ValueError, match="PROMPT_SOURCE=db needs DATABASE_URL"):
        Settings(api_key="x", environment="local", _env_file=None, prompt_source="db", prompt_db_fallback_to_file=False)
