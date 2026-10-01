from typing import Any

from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.ml.base import BlankCheck


class BlankService:
    """Satu pertanyaan, satu jawaban: halaman ini kosong atau tidak."""

    def __init__(self, model: BlankCheck, settings: Settings):
        self._model = model
        self._settings = settings

    async def check(self, text: str) -> dict[str, Any]:
        # Aturannya murah, tetapi tetap lewat threadpool: bentuk pemanggilannya sama dengan dua
        # guardrail lain, jadi yang membacanya tidak perlu menebak mana yang memblokir event loop.
        report = await run_in_threadpool(self._model.check, text or "", self._settings.blank_max_chars)
        return {**report, "model": getattr(self._model, "name", "blank")}
