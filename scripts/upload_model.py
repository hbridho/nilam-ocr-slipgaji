"""Uploads the slip gaji models to GCS from a script instead of the console, then downloads each one back and
checks it against its manifest. The folder uploaded is the one scripts/export_gcs_models.py builds:

    python scripts/export_gcs_models.py                 # dist/gcs/nilam-ocr-slipgaji/...
    python scripts/upload_model.py                      # every model + manifest.json -> gs://<bucket>/nilam-ocr-slipgaji/
    python scripts/upload_model.py --only identity      # one model (identity | quality | confidence)

    python scripts/upload_model.py --file m.json --uri gs://bucket/path/m.json    # any single file

Credentials: GCP Workload Identity Federation with Entra ID (Tim SEA), the variables of `--env-file`
(default wif.gcs.env, never committed) or of the environment; without them, Application Default Credentials
(gcloud auth application-default login, or a GCE VM's service account). An existing object is overwritten: a new
model goes to a new version folder (`export_gcs_models.py --version 2`) so v1 stays to roll back to.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs" / "ocr_common"))
sys.path.insert(0, str(ROOT / "libs" / "slip_ml"))

from ocr_common.clients import gcs  # noqa: E402
from ocr_common.clients.gcp import (  # noqa: E402
    CLOUD_PLATFORM_SCOPE,
    ENV_NAMES,
    read_env_file,
    wif_config,
    wif_credentials,
)

from slip_ml import models  # noqa: E402

EXPORT = ROOT / "dist" / "gcs" / models.GCS_PREFIX


def credentials(env_file: Path) -> tuple[Any, str]:
    import os

    import google.auth
    from google.auth import exceptions

    values = {**os.environ, **(read_env_file(env_file) if env_file.is_file() else {})}
    config = wif_config(values)
    if config is not None:
        return wif_credentials(config), config.service_account_email
    try:
        found, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
    except exceptions.DefaultCredentialsError:
        raise SystemExit(
            f"no GCP credentials: set {', '.join(ENV_NAMES.values())} (wif.gcs.env, --env-file) "
            "or run `gcloud auth application-default login`"
        ) from None
    return found, getattr(found, "service_account_email", None) or "application default credentials"


def upload(file: Path, uri: str, creds: Any, sha256: str | None = None) -> str:
    """Upload, download back, verify; the SHA-256 GCS recorded."""
    stored = gcs.upload(file, uri, creds)
    recorded = stored["metadata"][gcs.SHA256_METADATA]
    if sha256 and recorded != sha256:
        raise SystemExit(f"{uri}: uploaded sha256 {recorded} is not the manifest's {sha256}")
    with tempfile.TemporaryDirectory() as scratch:
        gcs.download(uri, Path(scratch) / file.name, creds, sha256=recorded)
    print(f"  {uri}  {stored['size']} bytes  sha256 {recorded[:16]}...  verified")
    return recorded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=sorted(models.GCS_MODELS), action="append", help="model kind(s)")
    parser.add_argument("--export", type=Path, default=EXPORT, help="the export_gcs_models.py output folder")
    parser.add_argument("--file", type=Path, help="a single file to upload to --uri instead")
    parser.add_argument("--uri", help="gs://bucket/path/to/file, with --file")
    parser.add_argument("--env-file", type=Path, default=ROOT / "wif.gcs.env")
    args = parser.parse_args()

    creds, identity = credentials(args.env_file)
    print(f"as {identity}")
    if args.file or args.uri:
        if not (args.file and args.uri and args.file.is_file()):
            raise SystemExit("--file (an existing file) and --uri go together")
        upload(args.file, args.uri, creds)
        return 0

    if not args.export.is_dir():
        raise SystemExit(f"{args.export} does not exist: run scripts/export_gcs_models.py first")
    manifests = sorted(args.export.glob("*/*/v*/manifest.json"))
    wanted = {models.GCS_MODELS[kind][1] for kind in args.only} if args.only else None
    done = 0
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if wanted is not None and manifest["model"] not in wanted:
            continue
        model_uri = manifest["uri"]
        upload(path.parent / manifest["file"], model_uri, creds, sha256=manifest["sha256"])
        upload(path, model_uri.rsplit("/", 1)[0] + "/manifest.json", creds)
        for user in manifest["used_by"]:
            for env, uri in user["env"].items():
                print(f"    {user['service']}: {env}={uri}")
        done += 1
    if not done:
        raise SystemExit(f"no model to upload under {args.export}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
