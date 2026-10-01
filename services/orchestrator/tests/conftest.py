import pytest

from ocr_common.testing import auth_headers, make_client, set_test_env

set_test_env(AUTH_DISABLED="false")

from app.dependencies import get_extraction_client, get_pipeline_waiter  # noqa: E402
from app.main import app  # noqa: E402
from app.services.pipeline_waiter import WaitOutcome  # noqa: E402

# Hanya jenis dan ukuran dokumen yang diperiksa di sini; byte-nya tidak pernah sampai ke model.
PDF = b"%PDF-1.4 fake-pdf-bytes"
JPEG = b"\xff\xd8fake-jpeg-bytes"

# Laporan guardrail datang dari tahap OCR (modelnya membaca teks OCR), bukan dari orchestrator.
ACCEPTED_REPORT = {
    "passed": True,
    "reason": None,
    "verdict": "slip_gaji",
    "rejected_by": None,
    "document": {
        "verdict": "accepted",
        "confidence": 0.9934,
        "n_pages": 2,
        "threshold": 0.47,
        "threshold_target": "accept",
    },
    "checks": {"blank": {"passed": True}, "blur": {"passed": True}, "identity": {"passed": True}},
    "skipped": [],
    "unavailable": [],
    "pages": [],
}
REJECTED_REASON = "Dokumen ini bukan slip gaji. Mohon unggah slip gaji."

OCR_RESULT = {
    "engine": "rapidocr",
    "n_pages": 2,
    "pages": [{"page": 1, "text": "SLIP GAJI"}, {"page": 2, "text": "SLIP GAJI"}],
    "guardrails": ACCEPTED_REPORT,
}


def slip(slip_no=1, page=1, periode="2025-02"):
    return {
        "slip_no": slip_no,
        "page": page,
        "fields": {
            "nama_perusahaan": "PT SUMBER REJEKI MAKMUR",
            "periode": periode,
            "nama_karyawan": "ANDI SAPUTRA",
            "gaji_pokok": 4500000,
            "total_pendapatan": 5100000,
            "gaji_bersih": 4875000,
            "divisi": None,
        },
        "missing_mandatory_fields": [],
    }


def scores(slip_no=1, gaji_pokok=0.936, nama_karyawan=0.42):
    return {
        "slip_no": slip_no,
        "scores": {
            "nama_perusahaan": 0.882,
            "periode": 0.91,
            "nama_karyawan": nama_karyawan,
            "gaji_pokok": gaji_pokok,
            "total_pendapatan": 0.925,
            "gaji_bersih": 0.925,
        },
    }


STRUCTURING_RESULT = {
    "document_type": "slip_gaji",
    "n_slips": 2,
    "slips": [slip(1, 1, "2025-02"), slip(2, 2, "2025-03")],
    "llm_used": False,
}
SCORING_RESULT = {"slips": [scores(1), scores(2)], "threshold": 0.5, "column_confidence_threshold": None}
DONE = WaitOutcome(
    "SCORING",
    "DONE",
    results={"OCR": OCR_RESULT, "STRUCTURING": STRUCTURING_RESULT, "SCORING": SCORING_RESULT},
)


class StubExtraction:
    def __init__(self) -> None:
        self.submitted: list[dict] = []

    async def submit(
        self,
        request_id,
        document_type,
        filename,
        content_type,
        content,
        *,
        file_url=None,
        sequence=None,
        guardrail_thresholds=None,
        columns=None,
    ) -> dict:
        self.submitted.append(
            {
                "request_id": request_id,
                "document_type": document_type,
                "filename": filename,
                "content_type": content_type,
                "file_url": file_url,
                "sequence": sequence,
                "guardrail_thresholds": guardrail_thresholds,
                "columns": columns,
            }
        )
        return {"request_id": request_id, "stage": "OCR", "status": "PROCESSING", "duplicate": False}

    async def aclose(self) -> None:
        pass


class StubWaiter:
    def __init__(self) -> None:
        self.outcome = DONE
        self.snapshot_outcome: WaitOutcome | None = DONE
        self.snapshot_error: Exception | None = None
        self.calls: list[tuple[str, float]] = []
        self.snapshots: list[str] = []
        self.sequences: list = []

    async def wait(self, request_id: str, timeout: float, *, sequence=None) -> WaitOutcome:
        self.calls.append((request_id, timeout))
        self.sequences.append(sequence)
        if self.outcome.sequence is None and sequence is not None:
            from dataclasses import replace

            return replace(self.outcome, sequence=sequence)
        return self.outcome

    async def snapshot(self, request_id: str) -> WaitOutcome | None:
        self.snapshots.append(request_id)
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return self.snapshot_outcome


@pytest.fixture(scope="session")
def client():
    return make_client(app)


@pytest.fixture
def auth() -> dict[str, str]:
    return auth_headers()


@pytest.fixture(autouse=True)
def stub_extraction():
    stub = StubExtraction()
    app.dependency_overrides[get_extraction_client] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_extraction_client, None)


@pytest.fixture(autouse=True)
def stub_waiter():
    stub = StubWaiter()
    app.dependency_overrides[get_pipeline_waiter] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_pipeline_waiter, None)
