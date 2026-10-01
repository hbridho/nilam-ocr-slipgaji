import copy
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from ocr_common.web.app import API_CONVENTIONS

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "api" / "gateway.openapi.yaml"
# The orchestrator owns every operation; the stages are listed for the callback webhook they send.
SERVICES = ("orchestrator", "extraction", "structuring", "scoring")

OPERATIONS: list[tuple[str, str, str, str]] = [
    ("orchestrator", "post", "/v1/extract-ocr", "1. Start the pipeline"),
    ("orchestrator", "get", "/v1/extract-ocr/{request_id}", "3. Status"),
]
CALLBACK_TAG = "2. Callbacks (you implement this)"

TAGS = [
    {
        "name": "1. Start the pipeline",
        "description": (
            "The call you make: OCR -> guardrails (blank, blur, identity) -> structuring -> scoring, waited on "
            "for up to "
            "PIPELINE_WAIT_SECONDS; answers in the NILAM extract-ocr contract (API spec [07]). 200: the result "
            "of the last service. 202: still running, the result arrives by callback. 400: rejected. 422: a stage "
            "failed."
        ),
    },
    {
        "name": CALLBACK_TAG,
        "description": "Listed under **Webhooks**: the request each stage sends to you when it finishes.",
    },
    {
        "name": "3. Status",
        "description": (
            "Where a request is now, in the same contract, without waiting: e.g. after a 202 whose callback did "
            "not arrive."
        ),
    },
]

DESCRIPTION = """
Everything the central orchestrator / gateway needs to integrate the **slip gaji** OCR pipeline, merged from
the services' own specs. The contract follows the NILAM API spec [07] (NPWP), with a `data` shaped for slip
gaji. Each service also serves its full Swagger UI at `/docs`.

## The flow

1. **`POST /v1/extract-ocr`** on `ms-bribrain-nilam-ocr-slipgaji-orchestrator` (port `8034`) with your
   `request_id` and the document (`file` or `file_url`; optional `params`, `pipeline_name_sequence`,
   `guardrails_confidence_threshold` + `guardrails_tendency`, `column_confidence_threshold`). The document is
   checked (type, 2,5 MB, page limit), then read by OCR, judged by the three guardrail services
   (blank, blur, identity — they read OCR text, so they run right after OCR), structured and scored. The
   service waits up to `PIPELINE_WAIT_SECONDS` (15 s):
   - finished -> **200**, `data` = `{total_slip, slip[]}`, every field `{value, confidence 0/1}`;
   - rejected by a guardrail -> **400** `DOWNSTREAM_VALIDATION_ERROR`, `guardrails: 1`,
     `pipeline_last_stage: guardrails`; by the structuring rules -> same, `pipeline_last_stage: structuring`;
   - a stage failed -> **422** `OCR_FAILED` / `STRUCTURING_FAILED` / `SCORING_FAILED`;
   - still running -> **202**; the result arrives by callback.
2. **Receive the callback** (see *Webhooks*): one result per request, with the raw 0-1 probabilities.
3. **Read the status when needed** with `GET /v1/extract-ocr/{request_id}`: the same contract, without waiting.

## Scores

Every score is a probability on a 0-1 scale. `confidence` in `data` is 1 when the confidence model's
probability that the value is correct reaches the field's threshold (`column_confidence_threshold`, then
`all_field`, then `FIELD_CONFIDENCE_THRESHOLD` = 0.5). There is no document-level score.

## Addresses

The orchestrator is the only service you call. From another namespace in GKE:
`http://ms-bribrain-nilam-ocr-slipgaji.nilam-ocr-slipgaji.svc.cluster.local:8034` (the release's entry
Service). The guardrail and stage services are internal. Nothing is exposed outside the cluster.
"""

REF = re.compile(r"#/components/schemas/([A-Za-z0-9_]+)")


def _load(service: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / "services" / service / "openapi.yaml").read_text(encoding="utf-8"))


def _refs(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.update(REF.findall(value))
            else:
                found |= _refs(value)
    elif isinstance(node, list):
        for item in node:
            found |= _refs(item)
    return found


def _rewrite(node: Any, rename: dict[str, str]) -> Any:
    if isinstance(node, dict):
        return {
            key: (
                REF.sub(lambda m: f"#/components/schemas/{rename.get(m.group(1), m.group(1))}", value)
                if key == "$ref" and isinstance(value, str)
                else _rewrite(value, rename)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_rewrite(item, rename) for item in node]
    return node


def _closure(names: set[str], schemas: dict[str, Any]) -> set[str]:
    pending, seen = list(names), set()
    while pending:
        name = pending.pop()
        if name in seen or name not in schemas:
            continue
        seen.add(name)
        pending.extend(_refs(schemas[name]))
    return seen


def build() -> dict[str, Any]:
    specs = {service: _load(service) for service in SERVICES}
    merged_schemas: dict[str, Any] = {}
    paths: dict[str, Any] = {}
    sent_when: list[str] = []
    callback_bodies: list[str] = []
    shared_description: str | None = None

    for service in SERVICES:
        spec = specs[service]
        schemas = spec["components"]["schemas"]
        wanted = [(m, p, tag) for s, m, p, tag in OPERATIONS if s == service]
        nodes: list[Any] = [spec["paths"][p][m] for m, p, _ in wanted]
        webhook = (spec.get("webhooks") or {}).get("stageCallback", {}).get("post")
        if webhook:
            nodes.append(webhook)
        needed = _closure(set().union(*(_refs(node) for node in nodes)) if nodes else set(), schemas)

        rename = {
            name: f"{service.capitalize()}{name}"
            for name in needed
            if name in merged_schemas and merged_schemas[name] != _rewrite(schemas[name], {})
        }
        for name in needed:
            merged_schemas[rename.get(name, name)] = _rewrite(copy.deepcopy(schemas[name]), rename)

        servers = [server for server in spec.get("servers", []) if server["url"] != "/"]
        for method, path, tag in wanted:
            operation = _rewrite(copy.deepcopy(spec["paths"][path][method]), rename)
            operation["tags"] = [tag]
            operation["servers"] = servers
            paths.setdefault(path, {})[method] = operation

        if webhook:
            description = webhook["description"]
            when = re.search(r"\*\*When\.\*\* (.*?)\n\n", description, re.S)
            sent_when.append(f"- **{service}**: {when.group(1) if when else ''}")
            body = _rewrite(webhook["requestBody"]["content"]["application/json"]["schema"], rename)
            callback_bodies.append(body["$ref"])
            shared_description = description

    if shared_description is None:
        raise SystemExit("no service spec publishes the stageCallback webhook: regenerate the service specs first")
    webhook_template = copy.deepcopy(specs["extraction"]["webhooks"]["stageCallback"]["post"])
    webhook_template["tags"] = [CALLBACK_TAG]
    webhook_template["description"] = re.sub(
        r"\*\*When\.\*\* .*?\n\n",
        "**When.** Once per stage, from the service that ran it:\n\n" + "\n".join(sent_when) + "\n\n",
        shared_description,
        flags=re.S,
    )
    unique_bodies = list(dict.fromkeys(callback_bodies))
    webhook_template["requestBody"]["content"]["application/json"]["schema"] = {
        "oneOf": [{"$ref": ref} for ref in unique_bodies],
        "description": "`stage: SCORING` carries the final result in `result`; for the other stages `result` is null.",
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "NILAM OCR Slip Gaji: gateway integration API",
            "version": specs["extraction"]["info"]["version"],
            "description": DESCRIPTION.strip() + "\n" + API_CONVENTIONS,
        },
        "tags": TAGS,
        "paths": paths,
        "webhooks": {"stageCallback": {"post": webhook_template}},
        "components": {
            "schemas": dict(sorted(merged_schemas.items())),
            "securitySchemes": specs["extraction"]["components"]["securitySchemes"],
        },
    }


def main() -> int:
    text = yaml.safe_dump(build(), sort_keys=False, allow_unicode=True, width=100)
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if yaml.safe_load(current or "{}") != yaml.safe_load(text):
            print(f"{TARGET.relative_to(ROOT)} ketinggalan dari spec service; jalankan `make openapi`")
            return 1
        print(f"{TARGET.relative_to(ROOT)} sesuai")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(text, encoding="utf-8")
    print(f"ditulis: {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
