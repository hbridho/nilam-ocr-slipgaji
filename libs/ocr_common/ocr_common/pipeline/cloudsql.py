"""Cloud SQL for PostgreSQL through the Cloud SQL Python Connector (asyncpg), the way nilam-ocr-shm reaches the shared
Cloud SQL database `nilam`: settings CLOUDSQL_* instead of DATABASE_URL, and the Google APIs called as a service account
through Workload Identity Federation with Microsoft Entra ID (ocr_common.clients.gcp), never the host's credentials.

    CLOUDSQL_INSTANCE=edm-bribrain-dev-01:asia-southeast2:gc-bribrain-dev-sql-psql-01
    CLOUDSQL_DATABASE=nilam
    CLOUDSQL_IP_TYPE=PRIVATE                       # PRIVATE / PUBLIC / PSC
    CLOUDSQL_USER= / CLOUDSQL_PASSWORD=            # both empty: IAM database login as the service account
    CLOUDSQL_AZURE_TENANT_ID= / _CLIENT_ID= / _CLIENT_SECRET=                       # the Entra app for Cloud SQL
    CLOUDSQL_GCP_PROJECT_NUMBER= / _POOL_ID= / _PROVIDER_ID= / _SERVICE_ACCOUNT_EMAIL=

The configuration is registered under a URL `cloudsql://<project:region:instance>/<database>`, which the settings put
where DATABASE_URL goes, so everything that takes a database URL works unchanged and `database.get_engine` builds the
engine through the connector for it. The machine must reach the instance's IP on port 3307 (the connector's port).
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ocr_common.clients.gcp import WifConfig, wif_credentials

CLOUDSQL_SCHEME = "cloudsql://"
IP_TYPES = ("PRIVATE", "PUBLIC", "PSC")
# Settings / environment names (`CLOUDSQL_<NAME>`) that must be set with CLOUDSQL_INSTANCE.
REQUIRED = (
    "database",
    "azure_tenant_id",
    "azure_client_id",
    "azure_client_secret",
    "gcp_project_number",
    "gcp_pool_id",
    "gcp_provider_id",
    "gcp_service_account_email",
)


@dataclass(frozen=True)
class CloudSqlConfig:
    instance: str
    database: str
    azure_tenant_id: str
    azure_client_id: str
    azure_client_secret: str = field(repr=False)
    gcp_project_number: str = ""
    gcp_pool_id: str = ""
    gcp_provider_id: str = ""
    gcp_service_account_email: str = ""
    user: str = ""
    password: str = field(default="", repr=False)
    ip_type: str = "PRIVATE"

    @property
    def iam_auth(self) -> bool:
        """IAM database authentication: no password, the service account is the database user."""
        return not self.password

    @property
    def db_user(self) -> str:
        """CLOUDSQL_USER, else the service account as Cloud SQL names its PostgreSQL IAM user."""
        return self.user or self.gcp_service_account_email.removesuffix(".gserviceaccount.com")

    @property
    def url(self) -> str:
        return f"{CLOUDSQL_SCHEME}{self.instance}/{self.database}"

    @property
    def identity(self) -> WifConfig:
        return WifConfig(
            tenant_id=self.azure_tenant_id,
            client_id=self.azure_client_id,
            client_secret=self.azure_client_secret,
            project_number=self.gcp_project_number,
            pool_id=self.gcp_pool_id,
            provider_id=self.gcp_provider_id,
            service_account_email=self.gcp_service_account_email,
        )


def config_from(values: Mapping[str, Any]) -> CloudSqlConfig:
    """The configuration from settings-style names (`cloudsql_instance`, ...); ValueError naming what is missing."""

    def get(name: str) -> str:
        return str(values.get(f"cloudsql_{name}") or "").strip()

    instance = get("instance")
    if instance.count(":") != 2:
        raise ValueError("CLOUDSQL_INSTANCE must be <project>:<region>:<instance>")
    missing = [f"CLOUDSQL_{name.upper()}" for name in REQUIRED if not get(name)]
    if missing:
        raise ValueError(f"CLOUDSQL_INSTANCE is set but {', '.join(missing)} is not")
    ip_type = get("ip_type").upper() or "PRIVATE"
    if ip_type not in IP_TYPES:
        raise ValueError(f"CLOUDSQL_IP_TYPE must be one of {', '.join(IP_TYPES)}")
    return CloudSqlConfig(
        instance=instance,
        database=get("database"),
        azure_tenant_id=get("azure_tenant_id"),
        azure_client_id=get("azure_client_id"),
        azure_client_secret=get("azure_client_secret"),
        gcp_project_number=get("gcp_project_number"),
        gcp_pool_id=get("gcp_pool_id"),
        gcp_provider_id=get("gcp_provider_id"),
        gcp_service_account_email=get("gcp_service_account_email"),
        user=get("user"),
        password=get("password"),
        ip_type=ip_type,
    )


_configs: dict[str, CloudSqlConfig] = {}
# One connector per configuration and event loop: it caches the instance's certificate and address, and must be
# used on the loop it was made on (structuring reads its prompt on a loop of its own at start).
_connectors: dict[tuple[str, int], Any] = {}


def register(config: CloudSqlConfig) -> str:
    """Makes `config` available to `get_engine` and returns the URL that stands for it."""
    _configs[config.url] = config
    return config.url


def is_cloudsql(url: str | None) -> bool:
    return bool(url) and str(url).startswith(CLOUDSQL_SCHEME)


def registered(url: str) -> CloudSqlConfig:
    if url not in _configs:
        raise RuntimeError(f"no Cloud SQL configuration registered for {url}: set the CLOUDSQL_* settings")
    return _configs[url]


def connect_kwargs(config: CloudSqlConfig) -> dict[str, Any]:
    """What the connector's asyncpg connect takes: IAM login without a password, or user + password."""
    kwargs: dict[str, Any] = {"user": config.db_user, "db": config.database, "enable_iam_auth": config.iam_auth}
    if not config.iam_auth:
        kwargs["password"] = config.password
    return kwargs


async def _connector(url: str, config: CloudSqlConfig) -> Any:
    key = (url, id(asyncio.get_running_loop()))
    if key not in _connectors:
        from google.cloud.sql.connector import IPTypes, create_async_connector  # the `cloudsql` extra

        _connectors[key] = await create_async_connector(
            credentials=wif_credentials(config.identity), ip_type=IPTypes[config.ip_type], refresh_strategy="lazy"
        )
    return _connectors[key]


def async_creator(url: str) -> Callable[[], Awaitable[Any]]:
    """The `async_creator` of the SQLAlchemy engine for a registered Cloud SQL URL."""
    config = registered(url)

    async def connect() -> Any:
        connector = await _connector(url, config)
        return await connector.connect_async(config.instance, "asyncpg", **connect_kwargs(config))

    return connect


async def close_connectors() -> None:
    """Closes the connectors of this event loop (shutdown, between tests)."""
    loop = id(asyncio.get_running_loop())
    for key in [key for key in _connectors if key[1] == loop]:
        await _connectors.pop(key).close_async()
