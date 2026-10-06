SERVICES := orchestrator guardrail-blank guardrail-blur guardrail-identity extraction structuring scoring
PY ?= python
PORT_orchestrator := 8034
PORT_extraction := 8030
PORT_structuring := 8032
PORT_scoring := 8033
# Ketiga guardrail sebagai service sendiri-sendiri, dipanggil paralel.
PORT_guardrail-blank := 8035
PORT_guardrail-blur := 8036
PORT_guardrail-identity := 8037

dev:
	$(PY) -m pip install -r requirements-dev.txt

test: test-lib $(SERVICES:%=test-%)

# Lock file ber-hash untuk image Docker. Input: requirements.txt (pin langsung) + dependensi
# ocr_common dari pyproject.toml-nya; output: requirements.lock (semua versi transitif + hash,
# untuk Linux x86_64 / Python 3.11). Jalankan setelah mengubah salah satu input. Versi transitif
# yang sudah ada di lock dipertahankan (idempoten); menaikkannya: make lock LOCK_FLAGS=--upgrade
LOCK_FLAGS ?=
LOCK := $(PY) -m uv pip compile --generate-hashes --python-version 3.11 --python-platform x86_64-unknown-linux-gnu --custom-compile-command "make lock" $(LOCK_FLAGS)
lock: $(SERVICES:%=lock-%) lock-db
lock-orchestrator:
	$(LOCK) services/orchestrator/requirements.txt libs/ocr_common/pyproject.toml --extra db -o services/orchestrator/requirements.lock
lock-extraction lock-structuring lock-scoring: lock-%:
	$(LOCK) services/$*/requirements.txt libs/ocr_common/pyproject.toml --extra db --extra cloudsql --extra gcs -o services/$*/requirements.lock
# Ketiga guardrail tidak punya basis data: mereka menjawab satu pertanyaan dan tidak menyimpan apa pun.
lock-guardrail-blank lock-guardrail-blur lock-guardrail-identity: lock-%:
	$(LOCK) services/$*/requirements.txt libs/ocr_common/pyproject.toml --extra gcs -o services/$*/requirements.lock
lock-db:
	$(LOCK) db/requirements.txt libs/ocr_common/pyproject.toml --extra cloudsql -o db/requirements.lock
# Gagal kalau ada lock yang ketinggalan dari requirements.txt / pyproject ocr_common (lock-nya ikut diperbarui).
lock-check: lock
	@test -z "$$(git status --porcelain -- services/*/requirements.lock db/requirements.lock)" \
		|| { git status --short -- services/*/requirements.lock db/requirements.lock; echo "requirements.lock berubah atau belum di-commit; commit hasil 'make lock' di atas"; exit 1; }
lint:
	$(PY) -m ruff check .
format:
	$(PY) -m ruff format . && $(PY) -m ruff check --fix .
typecheck: typecheck-lib $(SERVICES:%=typecheck-%)
openapi: $(SERVICES:%=openapi-%) openapi-gateway

test-lib:
	cd libs/ocr_common && $(PY) -m pytest -q
typecheck-lib:
	cd libs/ocr_common && $(PY) -m ty check ocr_common tests

test-%:
	cd services/$* && $(PY) -m pytest -q
typecheck-%:
	cd services/$* && $(PY) -m ty check app tests
openapi-%:
	cd services/$* && API_KEY=x ENVIRONMENT=local $(PY) -m ocr_common.web.openapi
openapi-gateway: $(SERVICES:%=openapi-%)
	$(PY) scripts/build_gateway_openapi.py
# Satu perintah untuk semuanya, tanpa .env (backend mock): python scripts/regen_openapi.py
openapi-all:
	$(PY) scripts/regen_openapi.py
api-docs:
	$(PY) -m http.server 8088
run-%:
	cd services/$* && $(PY) -m uvicorn app.main:app --reload --port $(PORT_$*)

db-upgrade:
	$(PY) -m alembic -c db/alembic.ini upgrade head
db-check:
	$(PY) -m alembic -c db/alembic.ini check
db-revision:
	$(PY) -m alembic -c db/alembic.ini revision --autogenerate -m "$(m)"
db-external:
	$(PY) db/external/apply.py

build:
	docker compose build
up:
	docker compose up -d --build
up-%:
	docker compose up -d --build $*
up-db:
	docker compose -f docker-compose.yml -f docker-compose.db.yml up -d --build
down:
	docker compose -f docker-compose.yml -f docker-compose.db.yml down
ps:
	@docker ps --filter name=ms-bribrain-nilam-ocr-slipgaji- --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
logs-%:
	docker compose logs -f $*

smoke:
	$(PY) scripts/smoke_e2e.py

# Satu folder per service untuk repo Bitbucket (review NCM): dist/bitbucket/ms-bribrain-nilam-ocr-slipgaji-*
split:
	$(PY) scripts/split_services.py

helm-lint:
	helm lint deploy/helm/nilam-ocr-slipgaji -f deploy/helm/nilam-ocr-slipgaji/values-ddb-dev.yaml

# Menjalankan di laptop tidak lewat make: `make` tidak ada di Windows, dan .ps1 ditolak pada mesin
# yang ExecutionPolicy-nya masih Restricted. Lihat local/README.md.
local:
	$(PY) local/run_local.py

.PHONY: dev test lint format typecheck lock lock-db lock-check openapi openapi-gateway api-docs test-lib typecheck-lib db-upgrade db-check db-revision db-external build up up-db down ps smoke local
