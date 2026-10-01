import pytest
from pydantic import ValidationError

from app.config import Settings

EXTRACTION = "http://ms-bribrain-nilam-ocr-slipgaji-extraction:8030"
STRUCTURING = "http://ms-bribrain-nilam-ocr-slipgaji-structuring:8032"
SCORING = "http://ms-bribrain-nilam-ocr-slipgaji-scoring:8033"
LOCALHOST = "http://127.0.0.1:9999"


def _deployed(extraction: str = EXTRACTION, structuring: str = STRUCTURING, scoring: str = SCORING) -> Settings:
    return Settings(
        api_key="x",
        _env_file=None,
        environment="production",
        pipeline_wait_seconds=0,
        extraction_service_url=extraction,
        structuring_service_url=structuring,
        scoring_service_url=scoring,
    )


def test_service_addresses_are_accepted_outside_local():
    assert _deployed()


@pytest.mark.parametrize(
    ("setting", "overrides"),
    [
        ("EXTRACTION_SERVICE_URL", {"extraction": LOCALHOST}),
        ("STRUCTURING_SERVICE_URL", {"structuring": LOCALHOST}),
        ("SCORING_SERVICE_URL", {"scoring": LOCALHOST}),
    ],
)
def test_localhost_services_are_refused_outside_local(setting, overrides):
    """Ketiganya, juga dengan PIPELINE_WAIT_SECONDS=0: GET /v1/extract-ocr/{request_id} tetap membaca
    tahap-tahap itu."""
    with pytest.raises(ValidationError, match=f"{setting} points to localhost"):
        _deployed(**overrides)


def test_there_is_no_guardrails_client_here():
    """Guardrail dinilai di tahap OCR (modelnya membaca teks OCR), jadi orchestrator tidak punya
    alamat service guardrail sama sekali."""
    settings = Settings(api_key="x", _env_file=None, environment="local")

    assert not hasattr(settings, "guardrails_service_url")


def test_localhost_services_are_fine_locally():
    settings = Settings(api_key="x", _env_file=None, environment="local")

    assert settings.extraction_service_url == "http://127.0.0.1:8030"
    assert settings.port == 8034


def test_the_wait_is_the_spec_s_15_seconds():
    """API spec [07]: 15 detik, lalu 202 + callback. Bisa dinaikkan per lingkungan bila disepakati."""
    assert Settings(api_key="x", _env_file=None, environment="local").pipeline_wait_seconds == 15.0
    assert (
        Settings(api_key="x", _env_file=None, environment="local", pipeline_wait_seconds=8).pipeline_wait_seconds == 8
    )
    with pytest.raises(ValidationError, match="pipeline_wait_seconds"):
        # pyrefly: ignore[bad-argument-type]
        Settings(api_key="x", _env_file=None, environment="local", pipeline_wait_seconds=-1)


def test_the_field_threshold_is_on_the_0_1_scale():
    assert Settings(api_key="x", _env_file=None, environment="local").field_confidence_threshold == 0.5
    with pytest.raises(ValidationError):
        Settings(api_key="x", _env_file=None, environment="local", field_confidence_threshold=80)


def test_the_page_limit_allows_several_months_in_one_file():
    assert Settings(api_key="x", _env_file=None, environment="local").max_document_pages >= 3
