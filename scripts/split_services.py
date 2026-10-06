#!/usr/bin/env python3
"""One self-contained repository per service, for Bitbucket (NCM code review).

    python scripts/split_services.py            # -> dist/bitbucket/ms-bribrain-nilam-ocr-slipgaji-<service>/

Each repository builds and tests on its own: the service (app/, tests/, openapi.yaml, .env.example,
requirements, Dockerfile with paths rewritten to the repository root), the shared libraries it installs
(libs/ocr_common, and libs/slip_ml where the Dockerfile copies it), a README, a .gitignore and a
bitbucket-pipelines.yml that runs ruff and pytest. The monorepo stays the source of truth: change code
there and run this again; never edit dist/.

Never copied: .env files, caches, local logs.
"""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "bitbucket"
PREFIX = "ms-bribrain-nilam-ocr-slipgaji"
SERVICES = {
    "orchestrator": (8034, "Pintu masuk API spec [07]: cek berkas, urutan, threshold; menunggu pipeline"),
    "extraction": (8030, "OCR (layanan PaddleOCR), lalu ketiga guardrail bersamaan"),
    "guardrail-blank": (8035, "Guardrail 1: halaman kosong (aturan panjang teks)"),
    "guardrail-blur": (8036, "Guardrail 2: terlalu buram (6 ciri mutu OCR)"),
    "guardrail-identity": (8037, "Guardrail 3: slip gaji atau bukan (TF-IDF + tata letak)"),
    "structuring": (8032, "Teks OCR -> 20 field per slip (aturan + arbitrase LLM opsional)"),
    "scoring": (8033, "P(nilai benar) per field, skala 0-1"),
}
IGNORE = shutil.ignore_patterns(
    ".env", "__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", "*.egg-info", ".venv", "logs"
)
GITIGNORE = """.env
*.env
!.env.example
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
*.egg-info/
"""
RUFF = """[tool.ruff]
line-length = 120
target-version = "py311"
src = [".", "libs/ocr_common", "libs/slip_ml"]
extend-exclude = ["libs/slip_ml/slip_ml/vendor"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]

[tool.ruff.lint.isort]
known-first-party = ["app", "tests"]
section-order = ["future", "standard-library", "third-party", "ocr-common", "first-party", "local-folder"]

[tool.ruff.lint.isort.sections]
"ocr-common" = ["ocr_common"]

[tool.ruff.lint.per-file-ignores]
"app/api/**" = ["B008", "B904"]
"app/dependencies.py" = ["B008"]
"libs/ocr_common/ocr_common/web/intake.py" = ["B008"]
"libs/slip_ml/slip_ml/*.py" = ["E402"]
"""
PIPELINE = """image: python:3.11-slim

pipelines:
  default:
    - step:
        name: Lint and test
        caches: [pip]
        script:
          - pip install -r requirements.lock
          - pip install --no-deps {libs}
          - pip install pytest==8.3.4 pytest-asyncio==0.24.0 aiosqlite==0.21.0 pyyaml==6.0.2 ruff==0.6.8
          - ruff check . && ruff format --check .
          - pytest -q
    - step:
        name: Image builds
        services: [docker]
        script:
          - docker build -t {repo}:$BITBUCKET_COMMIT .
"""


# SonarQube (SAST) of each Bitbucket repo: the BRI template of the NILAM repos (nilam-ocr-npwp), per service. The
# pipeline checks the repo out into `code/` and runs the scanner next to it, hence `../code`. Everything is scanned.
SONAR = """# must be unique in a given SonarQube instance
sonar.projectKey=bribrain:{repo}:development
# this is the name and version displayed in the SonarQube UI. Was mandatory prior to SonarQube 6.1.
sonar.projectName={repo}
sonar.projectVersion=1.0

# Path is relative to the sonar-project.properties file. Replace "\\" by "/" on Windows.
# This property is optional if sonar.moduleonfigs set.
sonar.sources=../code
sonar.projectBaseDir=../code

sonar.lang.patterns.xml=browserconfig.xml
sonar.flow.file.suffixes=xml
sonar.python.version=3.11
# sonar.exclusions=../code/vendor/*

# Encoding of the source code. Default is default system encoding
sonar.sourceEncoding=UTF-8
"""


def readme(name: str, repo: str, port: int, what: str, libs: list[str]) -> str:
    return f"""# {repo}

{what}. Bagian dari pipeline OCR Slip Gaji NILAM (7 service); kontrak API: API spec [07].

| | |
|---|---|
| Port | {port} |
| Image | `{repo}` |
| Library bersama | {", ".join(f"`{lib}`" for lib in libs)} (salinan dari monorepo `nilam-ocr-slipgaji`) |
| Setelan | `.env.example` |
| API | `openapi.yaml`, Swagger di `/docs` |

Repo ini dihasilkan dari monorepo `nilam-ocr-slipgaji` (`scripts/split_services.py`) untuk code review NCM:
arsitektur lengkap, alur, dan runbook deploy ada di sana (README.md, ARSITEKTUR.md, deploy/DEPLOY.md).

```bash
pip install -r requirements.lock && pip install --no-deps {" ".join(libs)}
cp .env.example .env
uvicorn app.main:app --port {port}
pytest -q
docker build -t {repo} .
```
"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for name, (port, what) in SERVICES.items():
        source = ROOT / "services" / name
        repo = f"{PREFIX}-{name}"
        target = OUT / repo
        shutil.copytree(source, target, ignore=IGNORE)

        dockerfile = (source / "Dockerfile").read_text(encoding="utf-8")
        libs = [lib for lib in ("libs/ocr_common", "libs/slip_ml") if f"COPY {lib} " in dockerfile]
        for lib in libs:
            shutil.copytree(ROOT / lib, target / lib, ignore=IGNORE)
        # The monorepo builds from its root (`services/<name>/...`); here the repository is the root.
        (target / "Dockerfile").write_text(dockerfile.replace(f"services/{name}/", ""), encoding="utf-8")

        pyproject = target / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8").replace("../../libs/", "libs/") if pyproject.exists() else ""
        if "[tool.ruff]" not in text:
            text = re.sub(r"\n*$", "\n\n", text) + RUFF if text else RUFF
        pyproject.write_text(text, encoding="utf-8")
        (target / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
        (target / "README.md").write_text(readme(name, repo, port, what, libs), encoding="utf-8")
        (target / "sonar-project.properties").write_text(SONAR.format(repo=repo), encoding="utf-8", newline="\n")
        (target / "bitbucket-pipelines.yml").write_text(
            PIPELINE.format(libs=" ".join(f"./{lib}" for lib in libs), repo=repo), encoding="utf-8"
        )
        print(f"{target.relative_to(ROOT)}  ({', '.join(libs)})")
    shutil.copy(ROOT / "BITBUCKET_REPOS.md", OUT / "BITBUCKET_REPOS.md")


if __name__ == "__main__":
    main()
