import pytest
from pydantic import ValidationError

from app.config import Settings


def test_mock_backend_is_refused_outside_local():
    """Guardrail tiruan berarti tidak ada penjaga sama sekali; hanya boleh di laptop."""
    with pytest.raises(ValidationError, match="BLANK_BACKEND=mock fabricates results"):
        Settings(api_key="x", _env_file=None, environment="production", blank_backend="mock")


def test_the_real_backend_is_the_default():
    """Dibaca dari definisi kelas, bukan dari instance: conftest memasang BLANK_BACKEND=mock di
    environment proses tes, dan instance apa pun akan mewarisinya."""
    assert Settings.model_fields["blank_backend"].default == "slip_blank"
    assert Settings.model_fields["blank_max_chars"].default is None


def test_the_limit_must_not_be_negative():
    with pytest.raises(ValidationError):
        Settings(api_key="x", _env_file=None, environment="local", blank_max_chars=-1)


def test_settings_of_the_pipeline_are_ignored(monkeypatch):
    """Setelan pintu masuk milik orchestrator; environment lama yang masih memasangnya tidak boleh
    membuat service ini gagal start."""
    monkeypatch.setenv("EXTRACTION_SERVICE_URL", "http://127.0.0.1:8030")
    monkeypatch.setenv("PIPELINE_WAIT_SECONDS", "15")

    settings = Settings(api_key="x", _env_file=None, environment="production", blank_backend="slip_blank")

    assert not hasattr(settings, "pipeline_wait_seconds")
