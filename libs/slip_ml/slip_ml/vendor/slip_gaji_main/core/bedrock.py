#!/usr/bin/env python3
"""Bedrock gpt-oss-120b behind Entra ID OIDC federation — no long-lived API key.

Three hops:

    Entra ID (client_credentials)  ->  bridge role (common-security)
                                   ->  target role (Bedrock account)
                                   ->  bedrock-runtime.invoke_model

Two things beyond a bare proof-of-concept:

  * Credentials are CACHED. STS hands out one hour; redoing all three hops per call,
    across thousands of documents, would be thousands of pointless token round-trips
    (and Entra rate-limits).
  * It is thread-safe, because web/app.py and annotate.py serve requests on a thread pool.

Config (Azure IDs, AWS role ARNs, region) comes from core/bedrock.env. It is deliberately
NOT named `.env`, so python-dotenv's load_dotenv() never picks it up — read_env_file()
reads it by path instead. Anything already exported in the real environment
(AWS_ROLE_ARN_TARGET, BEDROCK_MODEL_ID, …) wins over the file, so a single run can be
pointed elsewhere without editing it. Keep bedrock.env out of version control — it holds a
live client secret.

Quick check (from ocr_main/):
    python3 -c "from core import bedrock; print(bedrock.client().chat('Balas satu kata: halo'))"
"""

import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent
ENV_FILE = HERE / "bedrock.env"

DEFAULT_REGION = "ap-southeast-3"
DEFAULT_MODEL_ID = "openai.gpt-oss-120b-1:0"  # override in core/bedrock.env or config.yaml (llm.bedrock_model)

# Refresh this long before the STS credentials actually expire, so a call that starts
# just under the wire does not die mid-flight.
EXPIRY_MARGIN = 300  # seconds
HTTP_TIMEOUT = 120


# ── Config ──────────────────────────────────────────────────────────────────

_LINE = re.compile(r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$""")


def read_env_file(path: Path = ENV_FILE) -> dict:
    """Parse a KEY=VALUE file. Blank lines and # comments are skipped, quotes stripped."""
    out = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def load_env(path: Path = ENV_FILE) -> dict:
    """File values, overridden by anything already exported in the real environment.

    That order matters: it lets `AWS_ROLE_ARN_TARGET=... python3 s3_run.py` point a single
    run at prod without editing the file that everything else reads.
    """
    cfg = read_env_file(path)
    for key in list(cfg) + [
        "AWS_REGION",
        "BEDROCK_MODEL_ID",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AWS_ROLE_ARN_BRIDGE",
        "AWS_ROLE_ARN_TARGET",
    ]:
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    cfg.setdefault("AWS_REGION", DEFAULT_REGION)
    cfg.setdefault("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    return cfg


REQUIRED = ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AWS_ROLE_ARN_BRIDGE", "AWS_ROLE_ARN_TARGET"]


class BedrockError(RuntimeError):
    pass


# ── Reading the model's reply ───────────────────────────────────────────────

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)

# gpt-oss-120b returns its chain of thought inline, before the answer:
#     <reasoning>The user wants ... so answer {"a": 1}</reasoning>{"a": 1}
# The reasoning routinely contains DRAFT JSON. Searching the whole reply would find the
# draft first and return a half-formed answer that looks perfectly valid — so the reasoning
# is removed before anything is parsed.
_REASONING = re.compile(r"<(reasoning|think|thinking)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_DANGLING = re.compile(r"^\s*<(?:reasoning|think|thinking)\b[^>]*>.*?(?=\{|$)", re.S | re.I)


def strip_reasoning(text: str) -> str:
    """Drop <reasoning>...</reasoning> blocks. Also handles a block left unclosed by a
    max_tokens cut, which would otherwise swallow the entire reply."""
    if not text:
        return text or ""
    out = _REASONING.sub("", text)
    if "<reasoning" in out.lower() or "<think" in out.lower():
        out = _DANGLING.sub("", out)
    return out.strip()


def extract_json(text: str):
    """The first JSON object in a reply, or None.

    A model may return bare JSON, or answer in prose: fence the block, prefix it with a
    reasoning block, or add a sentence afterwards. Taking the outermost balanced {...}
    handles all of these, and brace-counting (rather than a regex) is what keeps a nested
    object from truncating the match at the first '}'.

    Answer-after-reasoning is tried first; the raw text is only a last resort, for a reply
    whose sole JSON really is inside the reasoning block.
    """
    if not text:
        return None

    clean = strip_reasoning(text)
    candidates = []
    for chunk in (clean, text):
        if not chunk:
            continue
        fenced = _FENCE.search(chunk)
        if fenced:
            candidates.append(fenced.group(1))
        candidates.append(chunk)

    for chunk in candidates:
        start = chunk.find("{")
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(chunk)):
                ch = chunk[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(chunk[start : i + 1])
                        except json.JSONDecodeError:
                            break  # not valid; try the next '{'
                        if isinstance(obj, dict):
                            return obj
                        break
            start = chunk.find("{", start + 1)
    return None


def reply_text(body: dict) -> str:
    """The assistant's text, whichever shape the model family returns."""
    if "choices" in body:  # OpenAI-compatible (gpt-oss)
        msg = (body["choices"][0] or {}).get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):  # some builds return segments
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        # gpt-oss can put the chain of thought in reasoning_content and leave content empty
        return content or msg.get("reasoning_content") or ""
    if "content" in body:  # Anthropic
        return "".join(c.get("text", "") for c in body["content"])
    if "output" in body:
        out = body["output"]
        return out if isinstance(out, str) else json.dumps(out)
    return ""


# ── The client ──────────────────────────────────────────────────────────────


class BedrockClient:
    """Federated Bedrock access. One instance is enough for a whole run."""

    def __init__(self, cfg: dict = None, region: str = None, model_id: str = None):
        self.cfg = cfg or load_env()
        self.region = region or self.cfg["AWS_REGION"]
        self.model_id = model_id or self.cfg["BEDROCK_MODEL_ID"]
        self._lock = threading.Lock()
        self._creds = None  # target-account credentials
        self._expires = 0.0  # unix seconds

        missing = [k for k in REQUIRED if not self.cfg.get(k)]
        if missing:
            raise BedrockError(f"missing {', '.join(missing)} — expected in {ENV_FILE} or the environment")

    # ── the three hops ──

    def _entra_token(self) -> str:
        import requests

        # ENTRA_TOKEN_URL boleh menimpanya (bedrock.env atau llm.entra_token_url di config.yaml):
        # tenant yang sama bisa disajikan lewat proksi atau cloud berdaulat dengan host berbeda.
        url = self.cfg.get("ENTRA_TOKEN_URL") or (
            f"https://login.microsoftonline.com/" f"{self.cfg['AZURE_TENANT_ID']}/oauth2/v2.0/token"
        )
        resp = requests.post(
            url,
            timeout=30,
            data={
                "grant_type": "client_credentials",
                "client_id": self.cfg["AZURE_CLIENT_ID"],
                "client_secret": self.cfg["AZURE_CLIENT_SECRET"],
                "scope": f"{self.cfg['AZURE_CLIENT_ID']}/.default",
            },
        )
        if resp.status_code != 200:
            try:
                detail = resp.json().get("error_description", resp.text)
            except Exception:
                detail = resp.text
            raise BedrockError(f"Entra ID token refused: {str(detail)[:200]}")
        return resp.json()["access_token"]

    def _assume_chain(self, token: str) -> dict:
        import boto3

        sts = boto3.client("sts", region_name=self.region)
        bridge = sts.assume_role_with_web_identity(
            RoleArn=self.cfg["AWS_ROLE_ARN_BRIDGE"],
            RoleSessionName="oidc-bridge-bedrock",
            WebIdentityToken=token,
            DurationSeconds=3600,
        )["Credentials"]

        sts_bridge = boto3.client(
            "sts",
            region_name=self.region,
            aws_access_key_id=bridge["AccessKeyId"],
            aws_secret_access_key=bridge["SecretAccessKey"],
            aws_session_token=bridge["SessionToken"],
        )
        return sts_bridge.assume_role(
            RoleArn=self.cfg["AWS_ROLE_ARN_TARGET"],
            RoleSessionName="bedrock-target-session",
            DurationSeconds=3600,
        )["Credentials"]

    def credentials(self, force: bool = False) -> dict:
        """Target-account credentials, refreshed only when they are about to expire."""
        with self._lock:
            if not force and self._creds and time.time() < self._expires:
                return self._creds
            creds = self._assume_chain(self._entra_token())
            expiry = creds.get("Expiration")
            if isinstance(expiry, datetime):
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                self._expires = expiry.timestamp() - EXPIRY_MARGIN
            else:
                self._expires = time.time() + 3600 - EXPIRY_MARGIN
            self._creds = creds
            return creds

    def session(self):
        import boto3

        c = self.credentials()
        return boto3.Session(
            aws_access_key_id=c["AccessKeyId"],
            aws_secret_access_key=c["SecretAccessKey"],
            aws_session_token=c["SessionToken"],
            region_name=self.region,
        )

    def whoami(self) -> str:
        return self.session().client("sts").get_caller_identity()["Arn"]

    # ── invoking ──

    def _body(self, prompt, max_tokens, temperature, include_model=False) -> dict:
        body = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        # None means "do not cap the answer" — the key is left out entirely and the model
        # stops when it is finished or when it reaches its own context limit. A reasoning
        # model shares this budget with its reasoning, so a cap that looks generous can
        # still cut the answer off mid-JSON.
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if include_model:
            body["model"] = self.model_id
        return body

    def _invoke_runtime(self, prompt, max_tokens, temperature) -> str:
        runtime = self.session().client("bedrock-runtime", region_name=self.region)
        resp = runtime.invoke_model(
            modelId=self.model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(self._body(prompt, max_tokens, temperature)),
        )
        return reply_text(json.loads(resp["body"].read()))

    def _invoke_mantle(self, prompt, max_tokens, temperature) -> str:
        """OpenAI-compatible endpoint, signed with SigV4.

        Kept because the proof-of-concept needed it: invoke_model can fail with
        ConnectionClosedError in ap-southeast-3 while Mantle answers fine.
        """
        import requests
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials

        c = self.credentials()
        # MANTLE_URL boleh menimpanya (bedrock.env atau llm.mantle_url di config.yaml), untuk
        # gateway internal atau VPC endpoint. Tetap ditandatangani SigV4 dengan cara yang sama.
        url = self.cfg.get("MANTLE_URL") or f"https://bedrock-mantle.{self.region}.api.aws/v1/chat/completions"
        body = json.dumps(self._body(prompt, max_tokens, temperature, include_model=True))

        signed = AWSRequest(
            method="POST",
            url=url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        SigV4Auth(
            Credentials(access_key=c["AccessKeyId"], secret_key=c["SecretAccessKey"], token=c["SessionToken"]),
            "bedrock",
            self.region,
        ).add_auth(signed)

        resp = requests.post(url, headers=dict(signed.headers), data=body, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            raise BedrockError(f"mantle HTTP {resp.status_code}: {resp.text[:200]}")
        return reply_text(resp.json())

    @staticmethod
    def _finish(text: str, raw: bool) -> str:
        return text if raw else strip_reasoning(text)

    def chat(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.0, raw: bool = False) -> str:
        """Model reply as text, with the reasoning block removed unless raw=True.

        Tries invoke_model, then Mantle. Both failures are reported together — knowing only
        that the second path failed would hide the reason the first one did.
        """
        try:
            return self._finish(self._invoke_runtime(prompt, max_tokens, temperature), raw)
        except Exception as primary:
            try:
                return self._finish(self._invoke_mantle(prompt, max_tokens, temperature), raw)
            except Exception as fallback:
                raise BedrockError(
                    f"invoke_model failed ({type(primary).__name__}: "
                    f"{str(primary)[:160]}); mantle also failed "
                    f"({type(fallback).__name__}: {str(fallback)[:160]})"
                ) from primary

    def chat_json(self, prompt: str, **kw):
        """chat() plus a tolerant JSON parse. Returns None when no object could be read."""
        return extract_json(self.chat(prompt, **kw))


# ── Module-level singleton, so a run federates once ─────────────────────────

_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def client() -> BedrockClient:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = BedrockClient()
        return _CLIENT


if __name__ == "__main__":
    import sys

    prompt = " ".join(sys.argv[1:]) or "Balas dalam satu kalimat: apa itu OIDC federation?"
    c = client()
    print(f"region  : {c.region}")
    print(f"model   : {c.model_id}")
    print(f"identity: {c.whoami()}")
    print(f"\nprompt  : {prompt}")
    print(f"reply   : {c.chat(prompt)}")
