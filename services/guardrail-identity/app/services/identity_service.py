from typing import Any

from starlette.concurrency import run_in_threadpool

from app.clients.reject_threshold import RejectThreshold
from app.config import Settings
from app.ml.base import IdentityCheck


class IdentityService:
    """Satu pertanyaan, satu jawaban: berkas ini slip gaji atau dokumen lain."""

    def __init__(self, model: IdentityCheck, settings: Settings, threshold: RejectThreshold):
        self._model = model
        self._settings = settings
        self._threshold = threshold

    async def check(self, text: str) -> dict[str, Any]:
        threshold = await self._threshold.get()
        report = await run_in_threadpool(self._model.check, text or "", threshold)
        return {**report, "model": getattr(self._model, "name", "identity")}
