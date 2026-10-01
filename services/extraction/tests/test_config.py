from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings

PROD: dict[str, Any] = {
    "environment": "production",
    "database_url": "postgresql+asyncpg://u:p@10.0.0.5:5432/db",
    "orchestration_url": "http://orkestrasi:8000",
    "extraction_backend": "api",
    "extraction_ocr_url": "http://10.213.191.199:8080",
    "structuring_service_url": "http://structuring:8032",
    "guardrail_blank_url": "http://guardrail-blank:8035",
    "guardrail_blur_url": "http://guardrail-blur:8036",
    "guardrail_identity_url": "http://guardrail-identity:8037",
}


def settings(**overrides) -> Settings:
    return Settings(api_key="x", _env_file=None, **{**PROD, **overrides})


def test_production_configuration_is_accepted():
    assert settings().environment == "production"


def test_the_ocr_service_is_the_default_backend():
    """Tidak ada model OCR di dalam image ini; modelnya dikelola di satu tempat, di layanan OCR."""
    assert Settings.model_fields["extraction_backend"].default == "api"


def test_the_api_backend_refuses_to_start_without_an_ocr_url():
    with pytest.raises(ValidationError, match="EXTRACTION_OCR_URL must be set"):
        settings(extraction_ocr_url=None)


def test_a_local_ocr_service_address_is_allowed_outside_local():
    """Layanan OCR boleh berjalan sebagai sidecar di pod yang sama, dan 127.0.0.1 lalu sah —
    berbeda dengan alamat tahap berikutnya, yang di dalam pod berarti service ini sendiri."""
    assert settings(extraction_ocr_url="http://127.0.0.1:8080").extraction_ocr_url == "http://127.0.0.1:8080"


def test_the_local_model_backend_needs_no_url():
    assert settings(extraction_backend="rapidocr", extraction_ocr_url=None).extraction_backend == "rapidocr"


def test_mock_ocr_is_refused_outside_local():
    with pytest.raises(ValidationError, match="EXTRACTION_BACKEND=mock fabricates results"):
        settings(extraction_backend="mock")


def test_default_localhost_next_stage_is_refused_outside_local():
    with pytest.raises(ValidationError, match="STRUCTURING_SERVICE_URL points to localhost"):
        settings(structuring_service_url="http://127.0.0.1:8032")


def test_guardrails_fail_open_is_the_default():
    """Penjaga yang tumbang tidak boleh menahan seluruh lalu lintas OCR; hasilnya tetap mencatat
    bahwa tidak ada yang menilai."""
    assert Settings.model_fields["guardrails_fail_open"].default is True


def test_local_keeps_the_laptop_defaults():
    local = Settings(api_key="x", _env_file=None, environment="local", extraction_backend="mock")

    assert local.structuring_service_url == "http://127.0.0.1:8032"
    assert (local.guardrail_blank_url, local.guardrail_blur_url, local.guardrail_identity_url) == (
        "http://127.0.0.1:8035",
        "http://127.0.0.1:8036",
        "http://127.0.0.1:8037",
    )


def test_each_guardrail_can_be_switched_off_on_its_own():
    """Guardrail yang dimatikan tidak dipanggil, jadi alamat localhost-nya tidak ditolak; dua yang lain tetap."""
    configured = settings(
        guardrail_blank_enabled=True, guardrail_blur_enabled=False, guardrail_blur_url="http://127.0.0.1:8036"
    )
    assert (configured.guardrail_blank_enabled, configured.guardrail_blur_enabled) == (True, False)
    with pytest.raises(ValidationError, match="GUARDRAIL_IDENTITY_URL"):
        settings(guardrail_identity_enabled=True, guardrail_identity_url="http://127.0.0.1:8037")
