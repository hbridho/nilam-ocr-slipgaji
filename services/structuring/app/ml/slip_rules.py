"""Aturan extraction 20 field slip gaji, lewat `slip_ml.structure`.

Dua lapis, sama persis dengan pipeline penelitian:

    aturan   sinonim label + pembacaan kolom + turunan aritmetika (gaji bersih = total
             pendapatan - total potongan). Akurasi terukur ~81,5%.
    LLM      arbitrase nilai uang lewat Amazon Bedrock, opsional. Dengan arbitrase ~89,9%.

LLM mati secara default (`ENABLE_LLM=false`): service harus bisa berjalan tanpa kredensial AWS,
dan hasil yang lebih rendah tapi jujur lebih baik daripada service yang menolak start.
"""

from collections.abc import Sequence

from ocr_common.errors import BadRequest
from ocr_common.slip_gaji import DOCUMENT_TYPE
from ocr_common.types import OcrPage, StructuringResult

from slip_ml.structure import DEFAULT_LLM_MODEL, NoText, structure_pages


class SlipRulesStructurer:
    name = "slip_rules"

    def __init__(self, *, use_llm: bool = False, model: str | None = None):
        self.use_llm = use_llm
        self.model = model or DEFAULT_LLM_MODEL
        self.name = "slip_rules+llm" if use_llm else "slip_rules"

    def structure(self, pages: Sequence[OcrPage]) -> StructuringResult:
        try:
            result = structure_pages(pages, use_llm=self.use_llm, model=self.model)
        except NoText as exc:
            raise BadRequest(str(exc)) from exc
        return {
            "document_type": DOCUMENT_TYPE,
            "slips": result["slips"],
            "n_slips": result["n_slips"],
            "llm_used": result["llm_used"],
            "elapsed_ms": result["elapsed_ms"],
            "reject_reason": None,
        }  # ty: ignore[invalid-return-type]
