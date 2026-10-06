"""An empty variable (`X=`, what docker compose passes for an unset `${X:-}`) is "not set": the default applies."""

from ocr_common.config import PipelineSettings


def test_empty_variables_take_the_defaults(monkeypatch):
    for name in ("ORCHESTRATION_TIMEOUT_SECONDS", "ORCHESTRATION_URL", "PIPELINE_OUTBOX", "ELASTIC_APM_SERVER_URL"):
        monkeypatch.setenv(name, "")

    settings = PipelineSettings(api_key="k", environment="local", _env_file=None)

    assert settings.orchestration_timeout_seconds == 10.0
    assert settings.orchestration_url is None
    assert settings.pipeline_outbox is False
    assert settings.elastic_apm_server_url is None
