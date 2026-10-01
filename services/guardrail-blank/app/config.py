from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator

from ocr_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    port: int = 8035

    # `slip_blank` memakai aturan yang sama dengan repo penelitian; `mock` hanya lokal.
    blank_backend: str = "slip_blank"

    # Batas panjang teks (setelah dipangkas) yang masih dihitung kosong. Kosong = angka yang ikut
    # tersimpan bersama gerbang mutu (20). Tidak ada ambang dari Orkestrasi pusat untuk pemeriksaan
    # ini: yang diputuskan bukan skor model melainkan panjang teks, dan angkanya tidak bergeser
    # bersama korpus seperti ambang model bergeser.
    blank_max_chars: int | None = Field(None, ge=0, le=10_000)

    @model_validator(mode="after")
    def _guard_blank(self) -> Self:
        self.reject_mock_backend_outside_local(blank_backend=self.blank_backend)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
