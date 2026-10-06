#!/usr/bin/env python3
"""The folder to drag into the GCS bucket: every slip gaji model in the layout the NILAM documents share.

    python scripts/export_gcs_models.py                  # -> dist/gcs/nilam-ocr-slipgaji/
    python scripts/export_gcs_models.py --version 2      # a new version next to v1, nothing overwritten

In the console: bucket `gc-bribrain-dev-gcs-ocr-nilam-01` -> folder `nilam-ocr-slipgaji/` -> Upload folder, and pick
`guardrails` and `scoring` from `dist/gcs/nilam-ocr-slipgaji/`. Result:

    nilam-ocr-slipgaji/guardrails/is_slip_gaji_doc_confidence/v1/is_slip_gaji_doc_confidence_v1.json + manifest.json
    nilam-ocr-slipgaji/guardrails/unreadable_doc_confidence/v1/unreadable_doc_confidence_v1.json  + manifest.json
    nilam-ocr-slipgaji/scoring/field_confidence/v1/field_confidence_v1.json                      + manifest.json

Each `manifest.json` names the model, its version, the service variable that points at it (with the full URI),
its SHA-256 (optional pin: `<SERVICE>_MODEL_SHA256`) and the training metrics stored with the model. A browser
upload records no SHA-256 metadata; the services then check the MD5 GCS keeps for every object, or the SHA-256 when
it is pinned.
"""

import argparse
import base64
import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs" / "slip_ml"))

from slip_ml import models  # noqa: E402

USED_BY = {
    models.IDENTITY: [("ms-bribrain-nilam-ocr-slipgaji-guardrail-identity", "IDENTITY_MODEL_GCS_URI")],
    models.QUALITY: [
        ("ms-bribrain-nilam-ocr-slipgaji-guardrail-blur", "BLUR_MODEL_GCS_URI"),
        ("ms-bribrain-nilam-ocr-slipgaji-guardrail-blank", "BLANK_MODEL_GCS_URI"),
    ],
    models.CONFIDENCE: [("ms-bribrain-nilam-ocr-slipgaji-scoring", "SCORING_MODEL_GCS_URI")],
}
DESCRIPTIONS = {
    models.IDENTITY: "P(dokumen ini slip gaji): TF-IDF unigram+bigram + 8 ciri tata letak, kalibrasi Platt",
    models.QUALITY: "P(halaman terlalu rusak untuk dibaca): regresi logistik 6 ciri mutu OCR; juga batas teks kosong",
    models.CONFIDENCE: "P(nilai field ini benar): regresi logistik + one-hot field, kalibrasi isotonic (skala 0-1)",
}
METRIC_KEYS = (
    "trained",
    "variant",
    "kind",
    "threshold",
    "cv_auc",
    "auc",
    "gini",
    "n_train",
    "n_rows",
    "n_yes",
    "n_no",
    "n_broken",
    "n_errors",
    "blank_max_chars",
    "metrics",
)


def digests(path: Path) -> tuple[str, str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), base64.b64encode(hashlib.md5(data).digest()).decode(), len(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "gcs")
    args = parser.parse_args()

    root = args.out / models.GCS_PREFIX
    if root.exists():
        shutil.rmtree(root)
    for kind, (stage, name) in models.GCS_MODELS.items():
        source = Path(models.baked(kind))
        folder = root / stage / name / f"v{args.version}"
        folder.mkdir(parents=True)
        target = folder / f"{name}_v{args.version}.json"
        shutil.copyfile(source, target)
        sha256, md5, size = digests(target)
        model = json.loads(source.read_text(encoding="utf-8"))
        uri = models.gcs_uri(kind, args.version)
        manifest = {
            "document": "slip_gaji",
            "model": name,
            "version": f"v{args.version}",
            "file": target.name,
            "format": "json, served with numpy (no pickle, no torch)",
            "description": DESCRIPTIONS[kind],
            "uri": uri,
            "sha256": sha256,
            "md5": md5,
            "size_bytes": size,
            "used_by": [{"service": service, "env": {env: uri}} for service, env in USED_BY[kind]],
            "training": {key: model[key] for key in METRIC_KEYS if key in model},
            "source": f"libs/slip_ml/slip_ml/vendor/ml/models/{source.name}",
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        (folder / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"{target.relative_to(args.out)}  sha256 {sha256[:16]}...  -> {uri}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
