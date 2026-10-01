#!/usr/bin/env python3
"""One place the whole pipeline reads its settings from: ../config.yaml.

    from core import config
    config.OCR["render_dpi"]        # -> 200
    config.LLM["backend"]           # -> "bedrock"
    config.PROMPT                   # -> the prompt template string
    config.PROMPT_META              # -> {"source": "file", "name": "slip_gaji", "version": 1}

s1_ocr.py and s2_structure.py load this at import, so changing config.yaml and
re-running is all it takes to point at a different model, DPI, token budget or
prompt — no code edit.

`reload()` re-reads the file, for a long-running process (web/app.py) that wants a
config change to take effect without a restart.

THE PROMPT LIVES OUTSIDE THIS FILE. `prompt:` in config.yaml is a pointer, not the text:

    prompt:
      source: file
      name: slip_gaji
      version: 1
      path: prompts/slip_gaji.v1.md

It is written that way because the prompt is meant to move into a database. Keeping the text in
config.yaml would make that move a rewrite of the settings file, and would file every prompt edit in
the same history as DPI and model changes — two things that change for different reasons and at
different rates.

To serve prompts from a database, register a resolver instead of editing this module:

    from core import config
    config.register_prompt_source("db", lambda spec, base: my_query(spec["name"], spec["version"]))
    config.reload()

and set `source: db` in config.yaml. `PROMPT_META` travels with every extraction result, so a row in
the database can always be traced back to the prompt that produced it.

A plain string under `prompt:` still works — that is the old shape, and it resolves to itself.
"""

from collections.abc import Callable
from pathlib import Path

import yaml

# config.yaml sits at the ocr_main/ root, one level up from this package.
CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.yaml"

# `source` value -> how to turn the pointer into text. Registering is how a new source is added;
# nothing in this module needs to know that a database exists.
PROMPT_SOURCES: dict[str, Callable[[dict, Path], str]] = {}


def register_prompt_source(name: str, resolver: Callable[[dict, Path], str]) -> None:
    """Teach `resolve_prompt` one more `source`. Called before `reload()`."""
    PROMPT_SOURCES[name] = resolver


def _from_file(spec: dict, base: Path) -> str:
    """`source: file` — read `path`, relative to config.yaml's own folder."""
    rel = spec.get("path")
    if not rel:
        raise ValueError("prompt.source is 'file' but prompt.path is missing in config.yaml")
    path = (base / rel).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


register_prompt_source("file", _from_file)


def resolve_prompt(spec, base: Path) -> tuple[str, dict]:
    """(text, meta) for whatever `prompt:` holds.

    Meta names where the text came from, so a result can record which prompt produced it — the
    reason that matters more once prompts live in a database and change without a code release.
    """
    if isinstance(spec, str):
        return spec, {"source": "inline", "name": None, "version": None}
    if not isinstance(spec, dict):
        return "", {"source": "none", "name": None, "version": None}

    source = spec.get("source", "file")
    resolver = PROMPT_SOURCES.get(source)
    if resolver is None:
        known = ", ".join(sorted(PROMPT_SOURCES)) or "(none)"
        raise ValueError(
            f"unknown prompt source {source!r}; registered: {known}. "
            f"Add one with core.config.register_prompt_source({source!r}, resolver)."
        )
    meta = {"source": source, "name": spec.get("name"), "version": spec.get("version")}
    return resolver(spec, base), meta


def load(path: Path = CONFIG_FILE) -> dict:
    """Parse config.yaml and resolve the prompt pointer into text.

    `data["prompt"]` is always the text, whatever shape the file used, so callers never branch.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for section in ("ocr", "llm"):
        data.setdefault(section, {})
    data.setdefault("prompt", "")
    text, meta = resolve_prompt(data["prompt"], path.resolve().parent)
    data["prompt"] = text
    data["prompt_meta"] = meta
    return data


_DATA = load()

OCR = _DATA["ocr"]
LLM = _DATA["llm"]
PROMPT = _DATA["prompt"]
PROMPT_META = _DATA["prompt_meta"]


def reload(path: Path = CONFIG_FILE) -> dict:
    """Re-read config.yaml and refresh the module-level OCR / LLM / PROMPT / PROMPT_META."""
    global _DATA, OCR, LLM, PROMPT, PROMPT_META
    _DATA = load(path)
    OCR, LLM, PROMPT = _DATA["ocr"], _DATA["llm"], _DATA["prompt"]
    PROMPT_META = _DATA["prompt_meta"]
    return _DATA
