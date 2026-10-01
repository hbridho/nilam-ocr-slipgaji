import pytest
from pydantic import ValidationError

from app.config import Settings


def test_mock_backend_is_refused_outside_local():
    """Guardrail tiruan berarti tidak ada penjaga sama sekali; hanya boleh di laptop."""
    with pytest.raises(ValidationError, match="IDENTITY_BACKEND=mock fabricates results"):
        Settings(api_key="x", _env_file=None, environment="production", identity_backend="mock")


def test_the_real_backend_is_the_default():
    """Dibaca dari definisi kelas, bukan dari instance: conftest memasang IDENTITY_BACKEND=mock di
    environment proses tes, dan instance apa pun akan mewarisinya."""
    assert Settings.model_fields["identity_backend"].default == "slip_identity"
    assert Settings.model_fields["identity_reject_threshold"].default is None
    assert Settings.model_fields["identity_threshold_url"].default is None


def test_the_threshold_must_be_a_probability():
    """0 akan meloloskan setiap dokumen, 1 hampir tidak satu pun."""
    for value in (0, 1, 1.5, -0.1):
        with pytest.raises(ValidationError):
            Settings(api_key="x", _env_file=None, environment="local", identity_reject_threshold=value)


def test_settings_of_the_pipeline_are_ignored(monkeypatch):
    monkeypatch.setenv("EXTRACTION_SERVICE_URL", "http://127.0.0.1:8030")
    monkeypatch.setenv("PIPELINE_WAIT_SECONDS", "15")

    settings = Settings(api_key="x", _env_file=None, environment="production", identity_backend="slip_identity")

    assert not hasattr(settings, "pipeline_wait_seconds")
