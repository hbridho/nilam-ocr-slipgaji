# Base image dipin ke digest (manifest list, semua arsitektur) supaya build bisa diulang.
# Memperbarui: docker buildx imagetools inspect python:3.11-slim  -> salin "Digest" ke semua Dockerfile.
FROM python:3.11-slim@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9

WORKDIR /app

# Dependensi dari lock file ber-hash (make lock).
COPY services/guardrail-blank/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# ocr_common dan slip_ml dipasang tanpa resolusi dependensi: semuanya sudah ada di lock.
COPY libs/ocr_common ./libs/ocr_common
COPY libs/slip_ml ./libs/slip_ml
RUN pip install --no-cache-dir --no-deps --no-build-isolation ./libs/ocr_common ./libs/slip_ml

COPY services/guardrail-blank/app ./app

# Bisa dibaca appuser apa pun umask mesin build (mis. 027 di image hardened): COPY membawa izin berkas dari host.
RUN chmod -R a+rX /app && useradd --create-home --shell /bin/bash appuser
USER appuser

ENV PORT=8035
EXPOSE 8035

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\", \"8035\")}/health', timeout=3)" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
