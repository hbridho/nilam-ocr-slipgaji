"""Which model file each slip gaji model is read from: the JSON baked in the image (default), or a file a service
downloaded from GCS at start (`ocr_common.clients.models.model_file`).

The vendored loaders read a module-level path when they load, so pointing them at another file needs no edit of
the vendored code (which `scripts/sync_ml.py` would overwrite):

    confidence  conf_score.MODEL_PATH             conf.json            scoring
    quality     guard_quality_score.MODEL_PATH    guard_quality.json   guardrail-blur (and guardrail-blank: its
                                                                       blank_max_chars lives in this model)
    identity    guard_score.MODELS (a directory)  guard_text.json      guardrail-identity

`use()` also empties the loader's cache, so the next load reads the new file. Call it once, before the first
prediction (the services do it when they build their backend).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from slip_ml.vendor import ensure_path

ensure_path()

import conf_score as _conf  # noqa: E402
import guard_quality_score as _quality  # noqa: E402
import guard_score as _guard  # noqa: E402

CONFIDENCE = "confidence"
QUALITY = "quality"
IDENTITY = "identity"

# The file name of each model in the image (and the one guard_score expects).
FILE_NAMES = {CONFIDENCE: "conf.json", QUALITY: "guard_quality.json", IDENTITY: "guard_text.json"}

# Where each model lives in GCS, in the layout shared by the NILAM documents (bucket gc-bribrain-dev-gcs-ocr-nilam-01):
# nilam-ocr-slipgaji/<stage>/<model>/v<N>/<model>_v<N>.json next to its manifest.json. The names follow the other
# documents' (`is_<doc>_doc_confidence`, `unreadable_doc_confidence`). scripts/export_gcs_models.py builds the folder.
GCS_BUCKET = "gc-bribrain-dev-gcs-ocr-nilam-01"
GCS_PREFIX = "nilam-ocr-slipgaji"
GCS_MODELS = {
    IDENTITY: ("guardrails", "is_slip_gaji_doc_confidence"),
    QUALITY: ("guardrails", "unreadable_doc_confidence"),
    CONFIDENCE: ("scoring", "field_confidence"),
}


def gcs_uri(kind: str, version: int = 1) -> str:
    """The GCS URI of model `kind` at `version`, in the shared layout."""
    stage, name = GCS_MODELS[kind]
    return f"gs://{GCS_BUCKET}/{GCS_PREFIX}/{stage}/{name}/v{version}/{name}_v{version}.json"


def baked(kind: str) -> str:
    """The model file baked in the image for `kind`."""
    if kind == CONFIDENCE:
        return str(_conf.HERE / "models" / FILE_NAMES[kind])
    if kind == QUALITY:
        return str(_quality.HERE / "models" / FILE_NAMES[kind])
    if kind == IDENTITY:
        return str(_guard.HERE / "models" / FILE_NAMES[kind])
    raise ValueError(f"unknown slip gaji model {kind!r}; expected {', '.join(FILE_NAMES)}")


def use(kind: str, path: str) -> None:
    """Read model `kind` from `path` from now on."""
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(f"model file not found: {file}")
    if kind == CONFIDENCE:
        _conf.MODEL_PATH = file
        _conf._CACHE.clear()  # noqa: SLF001 - the vendored loader's own cache
    elif kind == QUALITY:
        _quality.MODEL_PATH = file
        _quality._CACHE.clear()  # noqa: SLF001
    elif kind == IDENTITY:
        # guard_score reads `MODELS / guard_text.json`: a file under another name (the versioned GCS name,
        # is_slip_gaji_doc_confidence_v1.json) is copied next to itself under the name the loader expects.
        if file.name != FILE_NAMES[IDENTITY]:
            folder = file.parent / "identity"
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, folder / FILE_NAMES[IDENTITY])
            file = folder / FILE_NAMES[IDENTITY]
        _guard.MODELS = file.parent
        _guard._CACHE.clear()  # noqa: SLF001
    else:
        raise ValueError(f"unknown slip gaji model {kind!r}; expected {', '.join(FILE_NAMES)}")
