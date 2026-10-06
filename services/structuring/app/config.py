from functools import lru_cache
from typing import Self

from pydantic import model_validator

from ocr_common.config import PipelineSettings

from app.prompts import DEFAULT_PROMPT_PATH


class Settings(PipelineSettings):
    port: int = 8032

    structuring_backend: str = "slip_rules"

    # Arbitrase nilai uang lewat Amazon Bedrock. Mati secara default: service harus bisa berjalan
    # tanpa kredensial AWS. Terukur di 50 dokumen berlabel: aturan saja 81,5% field benar, dengan
    # arbitrase 89,9%. Menyalakannya butuh kredensial AWS biasa di environment (tidak ada kunci
    # yang ikut masuk ke image).
    enable_llm: bool = False
    llm_model: str | None = None
    # Batas waktu satu panggilan LLM ditangani boto3 lewat AWS_* environment; job yang menunggunya
    # tetap dibatasi PIPELINE_JOB_LEASE_SECONDS.

    # SIAPA yang menjawab, dan DI MANA. Semua None = pakai apa pun yang tertulis di config.yaml
    # bawaan image. Mengisinya menimpa setelan itu saat start, jadi memindahkan beban dari Bedrock
    # ke gateway internal tidak perlu membangun ulang image:
    #
    #   LLM_BACKEND=http  LLM_ENDPOINT=http://vllm.internal/v1/chat/completions  LLM_MODEL=...
    #
    # `off` menolak memanggil sama sekali, untuk lingkungan yang tidak boleh menembak ke luar.
    # Backend yang tidak terdaftar membuat service menolak start, bukan gagal di permintaan
    # pertama — kesalahan setelan harus terlihat saat deploy.
    llm_backend: str | None = None
    llm_endpoint: str | None = None
    # Nama VARIABEL LINGKUNGAN yang memuat kuncinya, bukan kuncinya: setelan ini ikut terbaca di log.
    llm_api_key_env: str | None = None
    llm_timeout_seconds: float | None = None
    # Menimpa alamat backend `bedrock` — gateway internal, VPC endpoint, cloud berdaulat. Tidak
    # menggantikan kredensial: itu tetap datang dari environment.
    llm_region: str | None = None
    llm_entra_token_url: str | None = None
    llm_mantle_url: str | None = None
    llm_max_tokens: int | None = None
    llm_all_fields: bool | None = None

    # DARI MANA prompt dibaca (lihat app/prompts.py). Teksnya tidak pernah ada di kode atau setelan —
    # yang ada penunjuknya:
    #   file (bawaan): PROMPT_PATH, bawaannya services/structuring/prompts/slip_gaji.v1.md di dalam image;
    #                  boleh absolut, jadi prompt bisa dipasang sebagai ConfigMap/volume tanpa rebuild.
    #   db:            tabel `system_prompt` (DDL tim, migrasi 0010): PROMPT_VERSION kosong = satu-satunya
    #                  baris `is_active`; diisi = versi itu. PROMPT_NAME hanya label di log.
    prompt_source: str = "file"
    prompt_name: str = "slip_gaji"
    prompt_version: int | None = None
    prompt_path: str = str(DEFAULT_PROMPT_PATH)
    prompt_db_table: str = "nilam_ocr_slipgaji.system_prompt"
    # Basis data tidak terbaca saat start -> pakai PROMPT_PATH dengan peringatan (bawaan), supaya
    # gangguan basis data tidak ikut mematikan structuring. `false`: menolak start.
    prompt_db_fallback_to_file: bool = True

    scoring_service_url: str = "http://127.0.0.1:8033"
    scoring_api_key: str | None = None
    scoring_timeout_seconds: float = 10.0

    @model_validator(mode="after")
    def _guard_structuring(self) -> Self:
        self.reject_mock_backend_outside_local(structuring_backend=self.structuring_backend)
        self.reject_localhost_outside_local(scoring_service_url=self.scoring_service_url)
        if self.prompt_source == "db" and not self.database_url and not self.prompt_db_fallback_to_file:
            raise ValueError("PROMPT_SOURCE=db needs DATABASE_URL (or PROMPT_DB_FALLBACK_TO_FILE=true)")
        if not self.prompt_db_table.replace("_", "").replace(".", "").isalnum():
            raise ValueError("PROMPT_DB_TABLE must be a plain table name ([schema.]table)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
