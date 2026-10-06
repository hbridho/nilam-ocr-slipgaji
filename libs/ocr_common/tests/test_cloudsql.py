import secrets
import sys
import types

import pytest

from ocr_common.config import PipelineSettings
from ocr_common.pipeline import cloudsql, database

URL = (
    "postgresql+asyncpg://gc-bribrain-dev-sac-sql-01%40common-sec-dev-01.iam@/bribrain_ocr"
    "?cloudsql_instance=edm-bribrain-dev-01:asia-southeast2:gc-bribrain-dev-sql-psql-01"
)


class FakeConnector:
    made: list["FakeConnector"] = []

    def __init__(self, **options):
        self.options = options
        self.calls: list[tuple] = []
        self.closed = False
        FakeConnector.made.append(self)

    async def connect_async(self, instance, driver, **kwargs):
        self.calls.append((instance, driver, kwargs))
        return object()

    async def close_async(self):
        self.closed = True


@pytest.fixture
def connector(monkeypatch):
    """The Cloud SQL Python Connector replaced by `FakeConnector` (no Google credentials in the tests)."""
    FakeConnector.made = []
    module = types.ModuleType("google.cloud.sql.connector")
    module.__dict__.update(Connector=FakeConnector, IPTypes={"PRIVATE": "PRIVATE", "PUBLIC": "PRIMARY", "PSC": "PSC"})
    monkeypatch.setitem(sys.modules, "google.cloud.sql.connector", module)
    yield FakeConnector
    database._connectors.clear()


async def test_a_cloudsql_url_logs_in_with_iam_as_its_user_over_the_private_ip(connector):
    connect = database.cloudsql_connect(URL)

    await connect()
    await connect()

    [made] = connector.made
    assert made.options["enable_iam_auth"] is True
    assert made.options["ip_type"] == "PRIVATE"
    assert (
        made.calls
        == [
            (
                "edm-bribrain-dev-01:asia-southeast2:gc-bribrain-dev-sql-psql-01",
                "asyncpg",
                {"user": "gc-bribrain-dev-sac-sql-01@common-sec-dev-01.iam", "db": "bribrain_ocr"},
            )
        ]
        * 2
    ), "one connector per URL, reused"


async def test_the_ip_type_comes_from_the_url(connector):
    await database.cloudsql_connect(URL + "&cloudsql_ip_type=psc")()

    assert connector.made[0].options["ip_type"] == "PSC"


async def test_dispose_closes_the_connector(connector):
    await database.cloudsql_connect(URL)()

    await database.dispose_engines()

    assert connector.made[0].closed
    assert database._connectors == {}


async def test_with_a_password_it_logs_in_as_a_built_in_user(connector):
    password = secrets.token_hex(8)
    built_in = f"postgresql+asyncpg://nilam_ocr:{password}@/bribrain_ocr?cloudsql_instance=p:r:i"

    await database.cloudsql_connect(built_in)()

    [made] = connector.made
    assert made.options["enable_iam_auth"] is False
    assert made.calls == [("p:r:i", "asyncpg", {"user": "nilam_ocr", "db": "bribrain_ocr", "password": password})]


def test_a_cloudsql_url_needs_the_user_and_the_database():
    with pytest.raises(ValueError, match="IAM user and the database"):
        database.cloudsql_connect("postgresql+asyncpg://@/?cloudsql_instance=p:r:i")


# The CLOUDSQL_* settings, as nilam-ocr-shm: the dev values of Cloud SQL `nilam`.
INSTANCE = "edm-bribrain-dev-01:asia-southeast2:gc-bribrain-dev-sql-psql-01"


def _cloudsql_settings(**values) -> dict:
    return {
        "cloudsql_instance": INSTANCE,
        "cloudsql_database": "nilam",
        "cloudsql_azure_tenant_id": "tenant",
        "cloudsql_azure_client_id": "client",
        "cloudsql_azure_client_secret": secrets.token_hex(8),
        "cloudsql_gcp_project_number": "1",
        "cloudsql_gcp_pool_id": "pool",
        "cloudsql_gcp_provider_id": "provider",
        "cloudsql_gcp_service_account_email": "gc-bribrain-dev-sac-sql-01@common-sec-dev-01.iam.gserviceaccount.com",
        **values,
    }


@pytest.fixture
def shm_connector(monkeypatch):
    """`create_async_connector` replaced: what pipeline.cloudsql builds its connector with."""
    made = []

    async def create_async_connector(**options):
        connector = FakeConnector(**options)
        made.append(connector)
        return connector

    module = types.ModuleType("google.cloud.sql.connector")
    module.__dict__.update(
        create_async_connector=create_async_connector, IPTypes={"PRIVATE": "PRIVATE", "PUBLIC": "PRIMARY", "PSC": "PSC"}
    )
    monkeypatch.setitem(sys.modules, "google.cloud.sql.connector", module)
    yield made
    cloudsql._connectors.clear()
    cloudsql._configs.clear()


async def test_cloudsql_settings_log_in_as_the_database_user_through_the_wif_identity(shm_connector):
    password = secrets.token_hex(8)
    url = cloudsql.register(
        cloudsql.config_from(_cloudsql_settings(cloudsql_user="bribrain_user", cloudsql_password=password))
    )
    assert url == f"cloudsql://{INSTANCE}/nilam"

    connect = cloudsql.async_creator(url)
    await connect()
    await connect()

    [made] = shm_connector
    assert made.options["ip_type"] == "PRIVATE"
    assert made.options["credentials"].service_account_email.startswith("gc-bribrain-dev-sac-sql-01@")
    assert (
        made.calls
        == [
            (
                INSTANCE,
                "asyncpg",
                {"user": "bribrain_user", "db": "nilam", "enable_iam_auth": False, "password": password},
            )
        ]
        * 2
    ), "one connector per URL and event loop, reused"


async def test_without_a_password_the_service_account_logs_in_with_iam(shm_connector):
    url = cloudsql.register(cloudsql.config_from(_cloudsql_settings()))

    await cloudsql.async_creator(url)()

    [(_, _, login)] = shm_connector[0].calls
    assert login == {"user": "gc-bribrain-dev-sac-sql-01@common-sec-dev-01.iam", "db": "nilam", "enable_iam_auth": True}


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ({"cloudsql_instance": "only-a-name"}, "<project>:<region>:<instance>"),
        ({"cloudsql_database": ""}, "CLOUDSQL_DATABASE"),
        ({"cloudsql_azure_client_secret": ""}, "CLOUDSQL_AZURE_CLIENT_SECRET"),
        ({"cloudsql_ip_type": "vpn"}, "CLOUDSQL_IP_TYPE"),
    ],
)
def test_a_half_set_cloudsql_configuration_is_refused(values, reason):
    with pytest.raises(ValueError, match=reason):
        cloudsql.config_from(_cloudsql_settings(**values))


def test_the_secrets_never_show_in_the_configuration():
    config = cloudsql.config_from(_cloudsql_settings(cloudsql_password="hunter2-password"))
    assert "hunter2" not in repr(config)
    assert config.azure_client_secret not in repr(config)


def test_the_settings_put_the_cloudsql_url_where_database_url_goes():
    password = secrets.token_hex(8)
    values = {key.upper(): value for key, value in _cloudsql_settings(cloudsql_password=password).items()}
    settings = PipelineSettings(
        api_key="k", environment="local", _env_file=None, **{k.lower(): v for k, v in values.items()}
    )
    try:
        assert settings.database_url == f"cloudsql://{INSTANCE}/nilam"
        assert cloudsql.registered(settings.database_url).password == password
        with pytest.raises(ValueError, match="not both"):
            PipelineSettings(
                api_key="k",
                environment="local",
                _env_file=None,
                database_url="sqlite+aiosqlite:///x.db",
                **{k.lower(): v for k, v in values.items()},
            )
    finally:
        cloudsql._configs.clear()


def test_a_cloudsql_url_gets_a_connector_engine():
    url = cloudsql.register(cloudsql.config_from(_cloudsql_settings()))
    try:
        engine = database.get_engine(url)
        assert engine.url.host is None, "no host: the connector finds the instance"
    finally:
        database._engines.pop(url)
        cloudsql._configs.clear()


def test_get_engine_does_not_connect_and_hands_the_connector_to_sqlalchemy():
    engine = database.get_engine(URL)
    try:
        assert engine.url.drivername == "postgresql+asyncpg"
        assert engine.url.host is None, "no host: the connector finds the instance"
    finally:
        database._engines.pop(URL)
