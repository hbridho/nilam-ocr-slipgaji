"""Downloads a slip gaji model from GCS over the copy baked in libs/slip_ml, for running a version on a laptop or
building an image with that version baked in. A deployed service does not need this: with *_MODEL_GCS_URI set it
downloads its model itself at start.

    python scripts/fetch_weights.py                         # every model, v1 of the shared GCS layout
    python scripts/fetch_weights.py identity --version 2
    IDENTITY_MODEL_GCS_URI=gs://... IDENTITY_MODEL_SHA256=... python scripts/fetch_weights.py identity

Credentials as scripts/upload_model.py (wif.gcs.env / --env-file, else Application Default Credentials). The
download is checked against the MD5 GCS keeps and the SHA-256 recorded at upload (or *_MODEL_SHA256). The file
replaced is vendored (libs/slip_ml/slip_ml/vendor/ml/models): `scripts/sync_ml.py` puts the research copy back.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs" / "ocr_common"))
sys.path.insert(0, str(ROOT / "libs" / "slip_ml"))
sys.path.insert(0, str(ROOT / "scripts"))

from slip_ml import models  # noqa: E402

# The variable each model's URI is read from, as the services name it (quality: guardrail-blur and -blank).
ENV = {models.IDENTITY: "IDENTITY", models.QUALITY: "BLUR", models.CONFIDENCE: "SCORING"}


def fetch(kind: str, version: int, env_file: Path) -> None:
    from upload_model import credentials

    from ocr_common.clients import gcs

    uri = os.environ.get(f"{ENV[kind]}_MODEL_GCS_URI") or models.gcs_uri(kind, version)
    sha256 = os.environ.get(f"{ENV[kind]}_MODEL_SHA256")
    target = Path(models.baked(kind))
    creds, identity = credentials(env_file)
    print(f"{kind}: {uri} -> {target.relative_to(ROOT)} (as {identity})")
    gcs.download(uri, target, creds, sha256=sha256)
    print(f"{kind}: ready ({target.stat().st_size // 1024} KB)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kinds", nargs="*", help=f"{' | '.join(models.GCS_MODELS)} (default: all)")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--env-file", type=Path, default=ROOT / "wif.gcs.env")
    args = parser.parse_args()
    unknown = set(args.kinds) - set(models.GCS_MODELS)
    if unknown:
        parser.error(f"unknown model {', '.join(sorted(unknown))}; expected {', '.join(models.GCS_MODELS)}")
    for kind in args.kinds or models.GCS_MODELS:
        fetch(kind, args.version, args.env_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
