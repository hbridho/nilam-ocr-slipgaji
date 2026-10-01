import pytest
from pydantic import ValidationError

from app.config import Settings


def test_mock_backend_is_refused_outside_local():
    """Guardrail tiruan berarti tidak ada penjaga sama sekali; hanya boleh di laptop."""
    with pytest.raises(ValidationError, match="BLUR_BACKEND=mock fabricates results"):
        Settings(api_key="x", _env_file=None, environment="production", blur_backend="mock")


def test_the_real_backend_is_the_default():
    """Dibaca dari definisi kelas, bukan dari instance: conftest memasang BLUR_BACKEND=mock di
    environment proses tes, dan instance apa pun akan mewarisinya."""
    assert Settings.model_fields["blur_backend"].default == "slip_blur"
    assert Settings.model_fields["blur_threshold"].default is None


def test_the_threshold_must_be_a_probability():
    """0 akan menahan setiap halaman, 1 hampir tidak satu pun."""
    for value in (0, 1, 1.5, -0.1):
        with pytest.raises(ValidationError):
            Settings(api_key="x", _env_file=None, environment="local", blur_threshold=value)


def test_there_is_no_threshold_url_here():
    """Skala skor gerbang mutu (P(rusak)) berlawanan arah dengan skala identitas (P(slip gaji)).

    Satu angka `reject_threshold` dari Orkestrasi pusat untuk keduanya akan menggeser titik operasi
    salah satunya tanpa ada yang menyadari, jadi setelan itu sengaja tidak ada di service ini."""
    assert "blur_threshold_url" not in Settings.model_fields


def test_settings_of_the_pipeline_are_ignored(monkeypatch):
    monkeypatch.setenv("EXTRACTION_SERVICE_URL", "http://127.0.0.1:8030")
    monkeypatch.setenv("PIPELINE_WAIT_SECONDS", "15")

    settings = Settings(api_key="x", _env_file=None, environment="production", blur_backend="slip_blur")

    assert not hasattr(settings, "pipeline_wait_seconds")
