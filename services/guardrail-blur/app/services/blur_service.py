from typing import Any

from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.ml.base import BlurCheck


class BlurService:
    """Satu pertanyaan, satu jawaban: halaman ini masih cukup terbaca atau tidak."""

    def __init__(self, model: BlurCheck, settings: Settings):
        self._model = model
        self._settings = settings

    async def check(self, text: str, confidence: dict[str, Any] | None = None) -> dict[str, Any]:
        report = await run_in_threadpool(self._model.check, text or "", confidence, self._settings.blur_threshold)
        return {**report, "model": getattr(self._model, "name", "blur")}
