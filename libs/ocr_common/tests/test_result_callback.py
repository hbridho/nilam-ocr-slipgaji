import json

import httpx
import pytest
from pydantic import ValidationError

from ocr_common.clients.remote import RemoteModelClient
from ocr_common.config import PipelineSettings
from ocr_common.pipeline.callbacks import (
    OrchestrationCallback,
    ResultCallback,
    result_callback_body,
    stage_callback_body,
)
from ocr_common.pipeline.factory import build_callback
from ocr_common.slip_gaji import final_result

RID = "OCR_9cb01af2-493d-446d-b191-af120333f6d0"
GUARDRAILS = {
    "passed": True,
    "reason": None,
    "document": {"verdict": "accepted", "proba_slip_gaji": 0.9934, "model": "slip_text"},
}
PATH = "/v1/ocr-callback"


def _slip(slip_no=1, page=1, gaji_pokok=4500000, gaji_pokok_score=93.6, periode="2025-02"):
    return {
        "slip_no": slip_no,
        "page": page,
        "fields": {
            "nama_perusahaan": "PT SUMBER REJEKI MAKMUR",
            "periode": periode,
            "gaji_pokok": gaji_pokok,
            "divisi": None,
        },
        "source": {},
        "checks": {},
        "counts": {},
        "llm": {},
        "missing_mandatory_fields": [] if gaji_pokok else ["gaji_pokok"],
    }


def _scores(slip_no=1, gaji_pokok=93.6):
    return {"slip_no": slip_no, "scores": {"nama_perusahaan": 88.2, "periode": 91.0, "gaji_pokok": gaji_pokok}}


def _final(slips=None, scores=None):
    structuring = {"slips": slips if slips is not None else [_slip()], "llm_used": False}
    scoring = {"slips": scores if scores is not None else [_scores()], "threshold": 80.0}
    return dict(final_result("slip_gaji", GUARDRAILS, structuring, scoring))


def test_scoring_done_becomes_the_completed_result_callback():
    body = result_callback_body(stage_callback_body(RID, "SCORING", "DONE", result=_final()))

    assert body == {
        "request_id": RID,
        "status": "completed",
        "result": {
            "total_slip": 1,
            "slip": [
                {
                    "page": 1,
                    "nama_perusahaan": {"value": "PT SUMBER REJEKI MAKMUR", "confidence": 88.2},
                    "periode": {"value": "2025-02", "confidence": 91.0},
                    "gaji_pokok": {"value": 4500000, "confidence": 93.6},
                    "divisi": {"value": None, "confidence": 0.0},
                    "missing_mandatory_fields": [],
                }
            ],
        },
        "guardrails": GUARDRAILS,
    }


def test_every_slip_of_a_three_month_document_is_reported():
    """Satu berkas berisi tiga bulan: hasilnya tiga entri, bukan satu himpunan field yang tercampur."""
    slips = [_slip(1, 1, periode="2025-02"), _slip(2, 2, periode="2025-03"), _slip(3, 3, periode="2025-04")]
    scores = [_scores(1), _scores(2), _scores(3)]
    body = result_callback_body(stage_callback_body(RID, "SCORING", "DONE", result=_final(slips, scores)))

    assert body is not None
    assert body["result"]["total_slip"] == 3
    assert [slip["page"] for slip in body["result"]["slip"]] == [1, 2, 3]
    assert [slip["periode"]["value"] for slip in body["result"]["slip"]] == ["2025-02", "2025-03", "2025-04"]


def test_scores_follow_slip_no_not_list_order():
    """Tahap scoring boleh melewati slip tanpa satu pun nilai; mencocokkan dengan indeks daftar akan
    menggeser skor satu slip ke slip lain tanpa ketahuan."""
    slips = [_slip(1, 1), _slip(2, 2), _slip(3, 3)]
    scores = [_scores(3, gaji_pokok=61.0), _scores(1, gaji_pokok=93.6)]  # slip 2 tidak dinilai, urutan diacak
    body = result_callback_body(stage_callback_body(RID, "SCORING", "DONE", result=_final(slips, scores)))

    assert body is not None
    by_page = {slip["page"]: slip for slip in body["result"]["slip"]}
    assert by_page[1]["gaji_pokok"]["confidence"] == 93.6
    assert by_page[2]["gaji_pokok"]["confidence"] == 0.0
    assert by_page[3]["gaji_pokok"]["confidence"] == 61.0


def test_a_field_that_was_not_found_is_null_with_confidence_0():
    body = result_callback_body(stage_callback_body(RID, "SCORING", "DONE", result=_final()))

    assert body is not None
    assert body["result"]["slip"][0]["divisi"] == {"value": None, "confidence": 0.0}


def test_the_callback_keeps_the_raw_score_not_the_0_1_flag():
    """Pemetaan ke 0/1 milik kontrak extract-ocr; membulatkannya di sini membuang informasi."""
    body = result_callback_body(
        stage_callback_body(RID, "SCORING", "DONE", result=_final(scores=[_scores(1, gaji_pokok=79.4)]))
    )

    assert body is not None
    assert body["result"]["slip"][0]["gaji_pokok"]["confidence"] == 79.4


@pytest.mark.parametrize(
    ("stage", "error_message", "error_code", "expected_code"),
    [
        ("OCR", "ekstraksi OCR model is unavailable", None, "OCR_FAILED"),
        ("SCORING", "Internal error in SCORING stage", None, "SCORING_FAILED"),
        (
            "OCR",
            "Dokumen ditolak guardrail: peluang slip gaji 0.13 di bawah ambang 0.53 (model text)",
            "DOWNSTREAM_VALIDATION_ERROR",
            "DOWNSTREAM_VALIDATION_ERROR",
        ),
    ],
)
def test_a_failed_stage_becomes_the_failed_result_callback(stage, error_message, error_code, expected_code):
    body = result_callback_body(
        stage_callback_body(RID, stage, "FAILED", error_message=error_message, error_code=error_code)
    )

    assert body == {
        "request_id": RID,
        "status": "failed",
        "result": None,
        "guardrails": {},
        "error_code": expected_code,
        "error_message": error_message,
    }


@pytest.mark.parametrize("stage", ["OCR", "STRUCTURING"])
def test_a_stage_that_does_not_end_the_request_sends_nothing(stage):
    assert result_callback_body(stage_callback_body(RID, stage, "DONE", result={"pages": []})) is None


def _recording_client(seen: list[httpx.Request], status: int = 200) -> RemoteModelClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={})

    return RemoteModelClient(
        "http://ocr-orchestration.ocr-dev.svc.cluster.local",
        1.0,
        name="orchestration result callback",
        headers={"X-Callback-Key": "secret"},
        passthrough_client_errors=True,
        transport=httpx.MockTransport(handler),
    )


async def test_notify_posts_the_result_with_the_callback_key_once_the_request_ends():
    seen: list[httpx.Request] = []
    callback = ResultCallback(_recording_client(seen), PATH, attempts=1, delay=0)

    assert await callback.notify(RID, "OCR", "DONE", result={"pages": []}) is False
    assert await callback.notify(RID, "SCORING", "DONE", result=_final()) is True

    [request] = seen
    assert request.url.path == PATH
    assert request.headers["X-Callback-Key"] == "secret"
    assert json.loads(request.content)["status"] == "completed"


async def test_outbox_send_turns_the_stored_stage_body_into_the_result():
    seen: list[httpx.Request] = []
    callback = ResultCallback(_recording_client(seen), PATH)

    await callback.send(stage_callback_body(RID, "STRUCTURING", "DONE", result={"slips": []}))
    await callback.send(
        stage_callback_body(RID, "OCR", "FAILED", error_message="dokumen tidak terbaca", error_code="X")
    )

    [request] = seen
    assert json.loads(request.content) == {
        "request_id": RID,
        "status": "failed",
        "result": None,
        "guardrails": {},
        "error_code": "X",
        "error_message": "dokumen tidak terbaca",
    }


def _settings(**overrides) -> PipelineSettings:
    return PipelineSettings(api_key="k", environment="local", _env_file=None, **overrides)


def test_the_callback_format_picks_the_callback_class():
    assert isinstance(build_callback(_settings(orchestration_url="http://orch")), OrchestrationCallback)
    result = build_callback(
        _settings(
            orchestration_url="http://orch", orchestration_callback_format="result", orchestration_callback_key="s"
        )
    )
    assert isinstance(result, ResultCallback)


def _production(callback_key: str | None) -> PipelineSettings:
    return PipelineSettings(
        api_key="k",
        environment="production",
        database_url="postgresql+asyncpg://u:p@db/x",
        orchestration_url="http://ocr-orchestration.ocr-dev.svc.cluster.local",
        orchestration_callback_format="result",
        orchestration_callback_key=callback_key,
        _env_file=None,
    )


def test_the_result_format_needs_the_callback_key_outside_local():
    with pytest.raises(ValidationError, match="ORCHESTRATION_CALLBACK_KEY must be set"):
        _production(None)
    assert _production("secret").orchestration_callback_key == "secret"
