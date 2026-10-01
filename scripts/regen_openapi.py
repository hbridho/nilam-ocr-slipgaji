"""Regenerate every service's openapi.yaml, then api/gateway.openapi.yaml, without any .env file.

Each service's spec is written by `python -m ocr_common.web.openapi` run in its own folder; the settings it
needs to import (a mock backend, local environment) are given here, so a fresh clone can run this as is:

    python scripts/regen_openapi.py
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = (
    "orchestrator",
    "extraction",
    "guardrail-blank",
    "guardrail-blur",
    "guardrail-identity",
    "structuring",
    "scoring",
)
ENV = {
    "API_KEY": "x",
    "ENVIRONMENT": "local",
    "EXTRACTION_BACKEND": "mock",
    "STRUCTURING_BACKEND": "mock",
    "SCORING_BACKEND": "mock",
    "BLANK_BACKEND": "mock",
    "BLUR_BACKEND": "mock",
    "IDENTITY_BACKEND": "mock",
    "DATABASE_URL": "",
    "ORCHESTRATION_URL": "",
    "TESTING_ENDPOINTS": "false",
}


def main() -> None:
    env = {**os.environ, **ENV}
    paths = [str(ROOT / "libs" / "ocr_common"), str(ROOT / "libs" / "slip_ml")]
    env["PYTHONPATH"] = os.pathsep.join(paths + [env.get("PYTHONPATH", "")])
    for service in SERVICES:
        cwd = ROOT / "services" / service
        subprocess.run([sys.executable, "-m", "ocr_common.web.openapi"], cwd=cwd, env=env, check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_gateway_openapi.py")], cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    main()
