"""IDENTITY_MODEL_GCS_URI: the model is read from the downloaded file (its versioned GCS name), not the baked one."""

import shutil
import sys

import pytest

from app import dependencies
from app.config import Settings
from slip_ml import models

URI = models.gcs_uri(models.IDENTITY)


def test_the_uri_follows_the_shared_layout():
    assert URI == (
        "gs://gc-bribrain-dev-gcs-ocr-nilam-01/nilam-ocr-slipgaji/guardrails/is_slip_gaji_doc_confidence/v1/"
        "is_slip_gaji_doc_confidence_v1.json"
    )


def test_a_downloaded_model_is_the_one_used(tmp_path, monkeypatch):
    downloaded = tmp_path / "is_slip_gaji_doc_confidence_v1.json"
    shutil.copyfile(models.baked(models.IDENTITY), downloaded)
    seen = {}

    def fake_model_file(local_path, gcs_uri, sha256, settings):
        seen.update(local=local_path, uri=gcs_uri)
        return str(downloaded)

    monkeypatch.setattr("ocr_common.clients.models.model_file", fake_model_file)
    settings = Settings(api_key="x", environment="local", _env_file=None, identity_model_gcs_uri=URI)
    try:
        check = dependencies.BACKENDS["slip_identity"](settings)
        assert seen == {"local": models.baked(models.IDENTITY), "uri": URI}
        report = check.check(
            "SLIP GAJI\nPeriode : Februari 2025\nGaji Pokok Rp 4.500.000\nGAJI BERSIH Rp 4.875.000", 0.47
        )
        assert 0 <= report["proba_slip_gaji"] <= 1
        # guard_score is the vendored loader slip_ml puts on sys.path (no import path a type checker can follow).
        assert sys.modules["guard_score"].MODELS == tmp_path / "identity"
    finally:
        models.use(models.IDENTITY, models.baked(models.IDENTITY))


def test_a_uri_that_is_not_gs_is_refused():
    with pytest.raises(ValueError, match="IDENTITY_MODEL_GCS_URI must be gs://"):
        Settings(api_key="x", environment="local", _env_file=None, identity_model_gcs_uri="https://x/model.json")
