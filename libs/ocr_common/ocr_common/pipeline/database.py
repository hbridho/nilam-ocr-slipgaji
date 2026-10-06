"""One async SQLAlchemy engine per database URL, shared by everything in the process.

The deployed services reach Cloud SQL `nilam` with the CLOUDSQL_* settings, as nilam-ocr-shm does (pipeline.cloudsql):
the settings register them under a `cloudsql://<project:region:instance>/<database>` URL that takes DATABASE_URL's
place, and its engine opens connections through the Cloud SQL Python Connector as the Entra ID / Workload Identity
Federation service account.

A plain DATABASE_URL still works (tests, a local Postgres), and so does nilam-ocr-npwp's form, a URL with
`cloudsql_instance` in its query, connected as the host's default credentials (GKE Workload Identity). Without a
password that logs in with IAM database authentication as the URL's user (its `@` written `%40`); with a password, as a
built-in user. `cloudsql_ip_type` is `private` (default), `public` or `psc`::

    postgresql+asyncpg://sa-name%40project.iam@/database?cloudsql_instance=project:region:instance
    postgresql+asyncpg://user:password@/database?cloudsql_instance=project:region:instance
"""

import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy import JSON, URL, make_url, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

JSON_TYPE = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")

# The PostgreSQL schema every table of this repository lives in, one schema per document type in the shared
# database `nilam` (migration 0010 moved them out of `public`), and the prefix of every table name: the client's
# naming, `nilam_ocr_slipgaji.nilam_ocr_jobs`, as nilam-ocr-shm's `nilam_ocr_shm.nilam_ocr_jobs`.
PIPELINE_SCHEMA = "nilam_ocr_slipgaji"
TABLE_PREFIX = "nilam_"

CLOUDSQL_INSTANCE = "cloudsql_instance"
CLOUDSQL_IP_TYPE = "cloudsql_ip_type"

_engines: dict[str, AsyncEngine] = {}
_factories: dict[str, async_sessionmaker] = {}
# One Cloud SQL Connector per `cloudsql_instance` URL, made on the first connection (it must live on the engine's loop).
_connectors: dict[str, Any] = {}


def get_engine(url: str) -> AsyncEngine:
    """The engine for `url`, created on first use with pool pre-ping. SQLite (the tests) has no schemas, so
    there the tables of `PIPELINE_SCHEMA` are used without one."""
    if url not in _engines:
        if url.startswith("cloudsql://"):
            from ocr_common.pipeline import cloudsql

            _engines[url] = create_async_engine(
                "postgresql+asyncpg://",
                async_creator=cloudsql.async_creator(url),
                pool_pre_ping=True,
                hide_parameters=True,
            )
            return _engines[url]
        options = {"schema_translate_map": {PIPELINE_SCHEMA: None}} if url.startswith("sqlite") else {}
        target = make_url(url)
        if CLOUDSQL_INSTANCE in target.query:
            _engines[url] = create_async_engine(
                URL.create(target.drivername),
                async_creator=cloudsql_connect(url),
                pool_pre_ping=True,
                hide_parameters=True,
            )
        else:
            _engines[url] = create_async_engine(
                url, pool_pre_ping=True, hide_parameters=True, execution_options=options
            )
    return _engines[url]


def cloudsql_connect(url: str) -> Callable[[], Coroutine[Any, Any, Any]]:
    """The `async_creator` of a `cloudsql_instance` URL: an asyncpg connection through the Cloud SQL Python
    Connector, logged in as the URL's user: with its password, or with IAM database authentication when it has none."""
    target = make_url(url)
    instance = str(target.query[CLOUDSQL_INSTANCE])
    ip_type = str(target.query.get(CLOUDSQL_IP_TYPE, "private")).upper()
    if not target.username or not target.database:
        raise ValueError(f"a {CLOUDSQL_INSTANCE} URL needs the IAM user and the database: user%40project.iam@/db")
    login = {"password": target.password} if target.password else {}

    async def connect() -> Any:
        from google.cloud.sql.connector import Connector, IPTypes  # the `cloudsql` extra of ocr-common

        connector = _connectors.get(url)
        if connector is None:
            connector = Connector(
                ip_type=IPTypes[ip_type],
                enable_iam_auth=not target.password,
                loop=asyncio.get_running_loop(),
                refresh_strategy="lazy",
            )
            _connectors[url] = connector
        return await connector.connect_async(instance, "asyncpg", user=target.username, db=target.database, **login)

    return connect


def get_session_factory(url: str) -> async_sessionmaker:
    """An `async_sessionmaker` bound to the engine for `url`."""
    if url not in _factories:
        _factories[url] = async_sessionmaker(get_engine(url), expire_on_commit=False)
    return _factories[url]


async def check_connection(url: str) -> None:
    """Runs `SELECT 1`; raises when the database is unreachable (readiness, startup)."""
    async with get_engine(url).connect() as conn:
        await conn.execute(text("SELECT 1"))


async def dispose_engines() -> None:
    """Closes every engine's pool and Cloud SQL Connector; call at shutdown and between tests."""
    for engine in _engines.values():
        await engine.dispose()
    for connector in _connectors.values():
        await connector.close_async()
    _engines.clear()
    _factories.clear()
    _connectors.clear()
    if "ocr_common.pipeline.cloudsql" in sys.modules:
        await sys.modules["ocr_common.pipeline.cloudsql"].close_connectors()
