"""The three guardrails are three services: each may be switched off, or be down, without taking the others
(or the pipeline) with it."""

import httpx
import pytest

from ocr_common.clients.guardrails import GuardrailEndpoint, GuardrailsFanout
from ocr_common.clients.remote import RemoteModelClient
from ocr_common.errors import UpstreamUnavailable

BLANK_OK = {"check": "blank", "verdict": "ok", "passed": True, "reason": None, "chars": 900, "max_chars": 20}
BLANK_HIT = {**BLANK_OK, "verdict": "blank", "passed": False, "reason": "Dokumen kosong", "chars": 0}
BLUR_OK = {"check": "blur", "verdict": "ok", "passed": True, "reason": None, "p_broken": 0.02, "threshold": 0.9584}
BLUR_HIT = {**BLUR_OK, "verdict": "blur", "passed": False, "reason": "Dokumen terlalu buram", "p_broken": 0.99}
ID_OK = {
    "check": "identity",
    "verdict": "slip_gaji",
    "passed": True,
    "reason": None,
    "proba_slip_gaji": 0.97,
    "reject_threshold": 0.47,
}
ID_HIT = {**ID_OK, "verdict": "bukan_slip_gaji", "passed": False, "reason": "Bukan slip gaji", "proba_slip_gaji": 0.1}


def _service(name, answer):
    """A guardrail service: `answer` is its report, or an int HTTP status, or 'down'."""

    def handler(request: httpx.Request) -> httpx.Response:
        if answer == "down":
            raise httpx.ConnectError("refused")
        if isinstance(answer, int):
            return httpx.Response(answer, json={"message": "boom"})
        return httpx.Response(200, json={"data": answer})

    client = RemoteModelClient(
        f"http://guardrail-{name}", 1.0, name=f"guardrail-{name} service", transport=httpx.MockTransport(handler)
    )
    return GuardrailEndpoint(name, client)


def _fanout(blank=BLANK_OK, blur=BLUR_OK, identity=ID_OK, *, fail_open=True):
    endpoints = [
        GuardrailEndpoint("blank", None) if blank is None else _service("blank", blank),
        GuardrailEndpoint("blur", None) if blur is None else _service("blur", blur),
        GuardrailEndpoint("identity", None) if identity is None else _service("identity", identity),
    ]
    return GuardrailsFanout(endpoints, fail_open=fail_open)


async def _check(fanout, **kwargs):
    return await fanout.check("REQ", "SLIP GAJI", {"mean": 0.98, "n_boxes": 40}, n_pages=1, **kwargs)


async def test_all_three_pass():
    report = await _check(_fanout())
    assert report["passed"] is True
    assert report["verdict"] == "slip_gaji"
    assert report["document"] == {
        "verdict": "accepted",
        "confidence": 0.97,
        "n_pages": 1,
        "threshold": 0.47,
    }
    assert (report["skipped"], report["unavailable"]) == ([], [])


async def test_blank_comes_before_blur_and_identity():
    report = await _check(_fanout(blank=BLANK_HIT, blur=BLUR_HIT, identity=ID_HIT))
    assert (report["passed"], report["rejected_by"], report["reason"]) == (False, "blank", "Dokumen kosong")
    assert report["document"]["verdict"] == "reject"


async def test_blur_comes_before_identity():
    report = await _check(_fanout(blur=BLUR_HIT, identity=ID_HIT))
    assert report["rejected_by"] == "blur"
    assert report["document"]["confidence"] == 0.99


@pytest.mark.parametrize("off", ["blank", "blur", "identity"])
async def test_one_guardrail_switched_off_leaves_the_other_two_deciding(off):
    report = await _check(_fanout(**{off: None}))
    assert report["passed"] is True
    assert report["skipped"] == [off]
    assert report["checks"][off] is None


async def test_a_switched_off_guardrail_does_not_hide_a_rejection_by_another():
    report = await _check(_fanout(blank=None, blur=None, identity=ID_HIT))
    assert (report["passed"], report["rejected_by"], report["skipped"]) == (False, "identity", ["blank", "blur"])


async def test_all_switched_off_lets_the_document_through_and_says_so():
    report = await _check(_fanout(blank=None, blur=None, identity=None))
    assert (report["passed"], report["verdict"]) == (True, "skipped")
    assert report["skipped"] == ["blank", "blur", "identity"]


@pytest.mark.parametrize("failure", ["down", 500])
async def test_a_guardrail_that_does_not_answer_is_unavailable_and_the_others_decide(failure):
    report = await _check(_fanout(blur=failure, identity=ID_HIT))
    assert report["unavailable"] == ["blur"]
    assert (report["passed"], report["rejected_by"]) == (False, "identity")


async def test_fail_closed_raises_instead():
    with pytest.raises(UpstreamUnavailable):
        await _check(_fanout(blur="down", fail_open=False))


async def test_the_requests_threshold_is_applied_to_the_returned_probability():
    """P(slip gaji) 0.97 passes the service's own 0.47 but not a request asking for 0.99."""
    report = await _check(_fanout(), thresholds={"identity": 0.99})
    assert (report["passed"], report["rejected_by"]) == (False, "identity")
    assert report["document"]["threshold"] == 0.99
    assert report["checks"]["identity"]["threshold_source"] == "request"


async def test_blur_threshold_on_the_accept_side():
    """p_broken 0.02 = P(readable) 0.98: passes 0.5, rejected when the request asks for 0.99."""
    lenient = await _check(_fanout(), thresholds={"blur": 0.5})
    strict = await _check(_fanout(), thresholds={"blur": 0.99})
    assert lenient["passed"] is True
    assert (strict["passed"], strict["rejected_by"]) == (False, "blur")
