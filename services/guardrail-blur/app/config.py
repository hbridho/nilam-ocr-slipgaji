from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator

from ocr_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    port: int = 8036

    # `slip_blur` memuat gerbang mutu terlatih dari slip_ml; `mock` hanya lokal.
    blur_backend: str = "slip_blur"

    # Ambang buram. Kosong = ambang yang ikut tersimpan bersama model (0,958), yaitu ambang
    # tertinggi yang masih meloloskan halaman yang benar-benar terbaca.
    #
    # Tidak ada ambang dari Orkestrasi pusat di sini, sengaja: skala skor gerbang mutu
    # (P(halaman terlalu rusak)) berlawanan arah dengan skala model identitas (P(slip gaji)), dan
    # satu angka `reject_threshold` untuk keduanya akan menggeser titik operasi salah satunya tanpa
    # ada yang menyadari.
    blur_threshold: float | None = Field(None, gt=0, lt=1)

    @model_validator(mode="after")
    def _guard_blur(self) -> Self:
        self.reject_mock_backend_outside_local(blur_backend=self.blur_backend)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
