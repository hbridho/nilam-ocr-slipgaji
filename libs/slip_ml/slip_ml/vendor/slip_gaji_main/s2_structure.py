#!/usr/bin/env python3
"""Stage 2 of the pipeline: OCR text in, the 20 fields out.

    regex match_fields   ->  all MANDATORY fields found?  ->  done, no model, no cost
                                                          ->  llm_extract for the gaps
    derive_totals        ->  fill any of the 3 totals arithmetic can reach
    validate             ->  report whether the numbers agree; never rewrite

The LLM is Amazon Bedrock, reached via Entra ID OIDC federation (core/bedrock.py). The
model is a config knob — any model id available in the account's region, from
openai.gpt-oss-120b-1:0 to anthropic.claude-sonnet-5 to amazon.nova-pro-v1:0.

The regex layer is first because it is free, deterministic and explainable: every value
it produces can be traced back to the line it was read from. The LLM is asked only for
the fields the rules could not find, and only when a MANDATORY one is among them.

Which model, the token budgets, the OCR-char threshold, the temperature and the prompt
itself all come from config.yaml via core.config.
"""

import re
import sys

from core import config
from core import llm as _llm_mod
from core.fields import (
    ALL_FIELDS,
    DEDUCTION,
    DEDUCTION_HEAD,
    EARNINGS,
    GROSS,
    MANDATORY,
    MONEY_FIELDS,
    MONEY_TOKEN,
    NET,
    OTHER_ALLOWANCE,
    SPACED_FIELDS,
    SYNONYMS,
    as_int,
    blank,
    clean_company,
    clean_identity,
    empty_record,
    is_column_head,
    is_signature_line,
    is_weak_company,
    keep_content_lines,
    looks_like_company,
    looks_like_value,
    parse_money,
    parse_period,
    plausible_money,
    respace,
    same_value,
    split_id_name,
)

# ── Settings (config.yaml) ────────────────────────────────────────────────

_LLM = config.LLM

LLM_PROMPT = config.PROMPT
LLM_MODEL = _LLM["model"]  # any Bedrock model id in the account's region
LLM_MAX_TOKENS = _LLM["max_tokens"]
LLM_RETRY_TOKENS = _LLM["retry_tokens"]
MIN_OCR_CHARS = _LLM["min_ocr_chars"]  # below this, no LLM call — the page is blank
OCR_CHAR_LIMIT = _LLM["ocr_char_limit"]
LLM_TEMPERATURE = _LLM["temperature"]

# Fields where the model's answer overrules the rules', on a page where both answered and
# they disagree. Only ever consulted with llm_all on — without it the model is not asked
# about a field a rule already filled, so there is nothing to arbitrate. Defaults to the
# money fields; see the comment at the arbitration site for the measurement behind that.
# Distinguishes "argument not given" from an explicit None, which now means "no cap".
_UNSET = object()

LLM_ARBITRATES = tuple(_LLM.get("arbitrates") or ())

# Ask the model for all 20 fields rather than only the ones the rules missed. It is what
# makes LLM_ARBITRATES mean anything — without it the model is never asked about a field a
# rule filled — and it costs a call on every page, including pages that used to be free.
LLM_ALL_DEFAULT = bool(_LLM.get("all_fields", False))

# Which backend answers, from config.yaml (`llm.backend`). No longer pinned to Bedrock: core.llm
# holds the registry, so moving the model to another host — a self-hosted server, an internal
# gateway, another region — is a config change, not a code change.
LLM_BACKEND = _LLM.get("backend") or "bedrock"
LLM_BACKENDS = tuple(_llm_mod.CHAT_BACKENDS)
BACKEND_LABEL = {name: (LLM_MODEL if name == LLM_BACKEND else name) for name in LLM_BACKENDS}

# A short menu of Bedrock model ids for the UI pickers (web/app.py, labeling.py). Any
# other id can still be typed in / set in config.yaml — this is just the dropdown.
MODELS = [
    "openai.gpt-oss-120b-1:0",
    "openai.gpt-oss-20b-1:0",
    "anthropic.claude-sonnet-5",
    "anthropic.claude-haiku-4-5-20251001-v1:0",
    "amazon.nova-pro-v1:0",
    "amazon.nova-lite-v1:0",
    "qwen.qwen3-235b-a22b-2507-v1:0",
    "deepseek.v3.2",
]


# ── Label matcher (regex) ──────────────────────────────────────────────────
# Free, deterministic, and — unlike a model — able to say WHERE it got a value. Every
# field it fills carries the line it was read from, which is what the annotation page
# shows behind the "asal" button. The LLM only sees what this layer could not find.

_LABEL_CACHE = {}


def _label_pattern(synonym: str) -> re.Pattern:
    """Match a label allowing flexible spacing and dots, e.g. 'N A M A' == 'nama'.

    Words are separated by at most ONE space. Rebuilt OCR rows use two spaces for a column
    gap, and a greedy separator let "status karyawan" match across it in
    "Status  Karyawan Tetap" — swallowing half the value it was meant to introduce.
    """
    if synonym in _LABEL_CACHE:
        return _LABEL_CACHE[synonym]
    parts = []
    for word in synonym.split():
        parts.append(r"[.\s]?".join(re.escape(ch) for ch in word))
    # No \s* before the punctuation. Letting the pattern swallow trailing whitespace also
    # swallows the two-space column gap that _read_value uses to recognise a value on the
    # same line — "Status  Karyawan Tetap" then failed every test and the reader walked on
    # to the next line, returning the date of hire as the employment status.
    #
    # Guarded at BOTH ends. Without the trailing guard the short synonyms ate their
    # neighbours: "total b" matched the opening of "Total benefits" and "Total Biaya
    # Kesehatan Tahun 2023", so an income row and a health-cost heading were both read as
    # the deductions total — and the second one handed back 2023 as an amount. A digit or
    # punctuation may still follow, which is what a label glued to its own value looks like.
    pat = re.compile(r"(?<![A-Za-z])" + r"\s?".join(parts) + r"[.:：]?(?![A-Za-z])", re.I)
    _LABEL_CACHE[synonym] = pat
    return pat


_OWNERS = [(field, syn) for field, syns in SYNONYMS.items() for syn in syns]
_OWNERS.sort(key=lambda fs: -len(fs[1]))  # longest synonym wins


def all_labels(line: str):
    """Every label on this line, left to right, as (field, match, anchored).

    A rebuilt OCR row is a whole row of the printed table, and a two-column payslip puts
    the earnings label and the deductions label side by side on it:

        Sub Total Pendapatan  Rp. 8.642.807  Sub Total Potongan  Rp.  114.661

    Returning only the first label meant "Sub Total Potongan" did not exist as far as the
    reader was concerned — the line belonged to Total Pendapatan, and Total Potongan went
    looking elsewhere and came back with a number from another row.

    Overlaps are resolved longest-match-first, which is how a single label was chosen
    before: "sub total pendapatan" wins over the "total" inside it.
    """
    if is_signature_line(line):
        # "Diterima oleh," is where a person signs, not where a number is printed — and
        # it opens with the same word half this corpus uses to label the net pay.
        return []
    found = []
    for field, syn in _OWNERS:
        for m in _label_pattern(syn).finditer(line):
            found.append((field, m))
    found.sort(key=lambda fm: (-len(fm[1].group(0)), fm[1].start()))
    taken, out = [], []
    for field, m in found:
        if any(m.start() < e and s < m.end() for s, e in taken):
            continue  # already covered by a longer label
        taken.append((m.start(), m.end()))
        out.append((field, m, m.start() <= 1))
    out.sort(key=lambda fma: fma[1].start())
    return out


def best_label(line: str):
    """(field, match, anchored) for the most specific label on this line, or None."""
    labels = all_labels(line)
    if not labels:
        return None
    return max(labels, key=lambda fma: len(fma[1].group(0)))


_LEAD_SEP = re.compile(r"^[\s:：=|/,;.\-–]*")
_COLUMN_GAP = re.compile(r"\s{2,}")


def first_column(rest: str) -> str:
    """The value up to the next column, for the remainder of a label's own line.

    s1_ocr rebuilds a page in reading order and joins boxes that sit side by side with a
    two-space gap, so "Jabatan  :Pengawas Lapangan  Alpa  :0" is three columns, not one
    sentence. Taking the whole remainder returned "Pengawas Lapangan Alpa :0" — the job
    title with the attendance column welded onto it.

    best_label() already stops at the next KNOWN synonym, which is why "Nama ... Jabatan"
    was never a problem. What it cannot stop at is a label this schema has no field for —
    Alpa, Kehadiran, Group/Grade, Tax ID, PTKP, Absen — and those are most of the corpus.
    The gap is the layout's own boundary and needs no vocabulary at all.

    The leading separator run is kept, not consumed: the caller inspects it to decide
    whether this line is a label/value line at all.
    """
    lead = _LEAD_SEP.match(rest).end()
    body = rest[lead:]
    cut = _COLUMN_GAP.search(body)
    return rest[:lead] + (body[: cut.start()] if cut else body)


def _read_value(lines, idx, match, field, labels, consumed, backward=False, page=""):
    """Read the value for a label found at lines[idx].

    Three modes:
      1. same line   — "Gaji Pokok : 4.000.000"
      2. next line   — label alone, value on a following line
      3. table       — one line backwards, for layouts that put the value before the label

    Returns (value, consumed_index, source_index) so the caller can record which line the
    answer actually came from.
    """

    def accept(raw):
        raw = (raw or "").strip()
        if not looks_like_value(raw):
            return None
        if field in ("nama_karyawan", "nomor_induk_karyawan"):
            # A shared "ID / Name" cell holds both, and the label matcher only ever sees
            # one of the two words — so whichever field is asking, it is handed the pair.
            half = split_id_name(raw, "id" if field == "nomor_induk_karyawan" else "name")
            if half is not None:
                raw = half
        if field in MONEY_FIELDS:
            return plausible_money(parse_money(raw))
        if field == "nama_perusahaan":
            # No employer is called "Amount". These come out of a column-heading row that
            # first_column() has already cut down to one word — "Regular earnings  Amount
            # Total" becomes "Amount" — so is_column_head, which needs two words to be sure,
            # can no longer see what it is. A single heading word is never a company name,
            # and this is the one field where that can be said without qualification.
            if is_column_head(raw + " total"):
                return None
            # A company name is letters. "1.528 275" and "202407 /3R1 ER JKK 0,24% (accd)
            # 42,000" are a transaction row and a contribution row; both survived every
            # other test because they contain SOME letters. Requiring a real word, and
            # requiring the letters to outnumber the digits, is what separates a name from
            # a table row without needing to know any company's name.
            letters = sum(c.isalpha() for c in raw)
            if not re.search(r"[A-Za-z]{3}", raw) or letters < sum(c.isdigit() for c in raw):
                return None
            # Keeps the legal form and repairs the spacing: OCR writes
            # "PT.IMPLEMENTASI TEKNOLOGI", and it also writes "WARUNGMAKANKEDAI" with
            # every space inside the name gone. `page` lets the repair look for a
            # correctly spaced copy of the name elsewhere on the same page first.
            return clean_company(raw, page)
        if field == "periode":
            # Only take a period the parser could normalise. OCR damage like "0ctober2023"
            # is left unmatched so the LLM gets a chance at it, rather than locking in an
            # unusable string.
            #
            # `page` lets a bare month borrow the year printed elsewhere on the sheet, and
            # the fullmatch below now demands four real digits: a period whose year could
            # not be recovered anywhere is left to the LLM instead of being frozen as
            # "????-11", which is a value no downstream consumer can do anything with.
            period = parse_period(raw, page)
            return period if period and re.fullmatch(r"\d{4}-\d{2}", period) else None
        value = clean_identity(raw)
        if field in SPACED_FIELDS:
            # The same repair the company name gets. OCR drops the spaces inside a
            # division or a job title exactly as readily — "BAPENDAKABUPATENBREBES" is
            # right, and unreadable — and `page` lets it prefer a correctly spaced copy
            # printed elsewhere on the same page over anything a word list decides.
            value = respace(value, page)
        return value

    if backward:
        j = idx - 1
        if j >= 0 and not labels[j] and j not in consumed:
            value = accept(lines[j])
            if value is not None:
                return value, j, j
        return None, None, None

    # 1 — remainder of the label's own line. Only trusted when a real separator, a digit or
    # a wide gap follows: OCR turns "nama_karyawan" into "Nama Pegawal", and without this the
    # misread tail reads as the employee's name.
    rest = lines[idx][match.end() :]
    # The EARLIEST label after this one, not the longest anywhere in the remainder: on a
    # two-column row the value ends at whichever label comes next, and a longer label
    # further right would let the neighbouring column through.
    following = all_labels(rest)
    if following:
        rest = rest[: following[0][1].start()]
    if field not in MONEY_FIELDS:
        # Money is exempt: an amount is normally printed AFTER the column gap
        # ("Gaji Pokok        Rp 4.500.000"), so cutting at the gap throws the number
        # away. parse_money already takes the first amount it finds, which is the
        # equivalent boundary for a numeric column.
        rest = first_column(rest)
    # The label pattern absorbs a trailing colon, so by the time `rest` is taken the
    # separator that proves this is a label/value line has already been eaten. Without
    # checking the match itself, "Nama : Tri Wulandari" fails every test below and the
    # name is never read.
    sep_eaten = bool(re.search(r"[.:：]\s*$", match.group(0)))
    if sep_eaten or re.match(r"^\s*[:：=|]", rest) or re.search(r"\d", rest) or re.match(r"^\s{2,}\S", rest):
        value = accept(rest)
        if value is not None:
            return value, None, idx

    # 2 — forward, stopping at the next label
    for j in range(idx + 1, min(idx + 4, len(lines))):
        if labels[j]:
            break
        if j in consumed:
            continue
        value = accept(lines[j])
        if value is not None:
            return value, j, j
    return None, None, None


def match_fields(text: str, allow_backward: bool = False):
    """Rule-based extraction. Returns (record, unmatched, source).

    `source[field]` records how the value was found — the synonym that matched and the line
    it was read from — so the annotation page can show its origin instead of asking you to
    take it on trust.

    `allow_backward` enables the value-before-label read. It is off for OCR text: rows are
    already rebuilt left-to-right by s1_ocr.OCRService._reading_order, so a value on the
    previous line belongs to that line, and reaching back for it picks up a neighbour's
    number.
    """
    lines = keep_content_lines(text)
    # One list per line, not one label per line: a two-column row carries two of them.
    labels = [all_labels(ln) for ln in lines]
    # The page as the company cleaner sees it. A letterhead is often printed twice, and
    # the second copy is often the one that kept its spaces — see fields.respace.
    page = "\n".join(lines)

    # Everything from a deductions heading onwards is the deductions block, so
    # "Potongan Makan" is not read as "tunjangan_makan".
    deduction_start = next((i for i, ln in enumerate(lines) if DEDUCTION_HEAD.match(ln)), len(lines))

    record, source, consumed = empty_record(), {}, set()

    # Nama Perusahaan first, and by a different rule. A company line has no label — it is
    # the letterhead — and the legal form is part of the NAME: taking "PT" as a label and
    # the rest as the value returns "GELORA SUKSES BERSAMA" for a document that says
    # "PT GELORA SUKSES BERSAMA". So a line opening with a legal form or an institution
    # word is taken whole, spacing and all, and the earliest one wins because that is where
    # a letterhead sits.
    # Two passes over the same window: a bank line only wins when nothing stronger is
    # printed above the fold, because the bank on a payslip is usually the one paying the
    # money out, not the one the employee works for.
    head = [
        i
        for i, ln in enumerate(lines[:13])
        if (not labels[i] or any(f == "nama_perusahaan" for f, _, _ in labels[i])) and looks_like_company(ln)
    ]
    for i in sorted(head, key=lambda i: (is_weak_company(lines[i]), i)):
        # clean_company, not clean_identity: the legal form is kept and the spacing
        # repaired, so "PT.IMPLEMENTASI TEKNOLOGI" comes out as "PT. IMPLEMENTASI
        # TEKNOLOGI" and a letterhead OCR'd without any spaces at all becomes readable.
        #
        # first_column for the same reason as in _read_value: a letterhead row usually
        # carries the address, the document title or a page number in the next column, and
        # "PT. MEKAR DUNIA WISATA  BINTANG BALI RESORT - SALARY SLIP" is two of them.
        value = clean_company(first_column(lines[i]), page)
        if value:
            record["nama_perusahaan"] = value
            source["nama_perusahaan"] = {
                "how": "regex",
                "label": "badan usaha / instansi",
                "line_no": i + 1,
                "line": lines[i],
            }
            consumed.add(i)
        break

    def owned_lines(field):
        owned = [(i, m, anchored) for i in range(len(lines)) for f, m, anchored in labels[i] if f == field]

        def rank(item):
            i, match, anchored = item
            in_deductions = i >= deduction_start and field in EARNINGS
            return (in_deductions, not anchored, -len(match.group(0)), i)

        return sorted(owned, key=rank)

    # Two passes: same-line and forward reads for every field first, then the backward read
    # only for fields still empty. Otherwise an early field reaches back and takes the line
    # belonging to the label above it.
    for backward in (False, True) if allow_backward else (False,):
        for field in ALL_FIELDS:
            if record[field] is not None:
                continue
            for idx, match, _ in owned_lines(field):
                value, used, src = _read_value(lines, idx, match, field, labels, consumed, backward=backward, page=page)
                if value is not None:
                    record[field] = value
                    source[field] = {
                        "how": "regex",
                        "label": match.group(0).strip(),
                        "line_no": (src if src is not None else idx) + 1,
                        "line": lines[src if src is not None else idx],
                    }
                    if used is not None:
                        consumed.add(used)
                    break

    # Allowances that belong nowhere else go to Tunjangan Lain. A payslip prints whatever
    # it likes — Tunj.Apresiasi, Tunjangan Premium, Tunjangan Kerajinan — and this schema
    # has ten earnings slots, four of them named for specific allowances. An allowance that
    # matches none of them is still money the employee was paid, and dropping it makes
    # Total Pendapatan look wrong for a reason nobody can see.
    #
    # The LARGEST such row, not the sum of them. Summing was the first design and it reads
    # well, but it does not match how the field is actually filled in: a labeller looking at
    # three unnamed allowances puts ONE of them here and finds other homes for the rest, so
    # the sum was wrong every time more than one row was found. The largest is the one most
    # likely to be the row a reader would pick, and — unlike a sum — it is a number that
    # appears on the document, which is what makes it checkable.
    #
    # Only when the field is still empty: a Tunjangan Lain the document printed itself
    # wins, as everywhere else. And only above the deductions block, on lines carrying
    # exactly one number, which is what separates one allowance row from a merged one.
    if record["tunjangan_lain"] is None:
        extras = []
        for i, ln in enumerate(lines):
            if i >= deduction_start or i in consumed or labels[i]:
                continue
            if not OTHER_ALLOWANCE.match(ln) or len(MONEY_TOKEN.findall(ln)) != 1:
                continue
            value = plausible_money(parse_money(ln))
            if value:
                extras.append((i, ln, value))
        if extras:
            pick = max(extras, key=lambda e: e[2])
            record["tunjangan_lain"] = pick[2]
            source["tunjangan_lain"] = {
                # "regex" now, not "derived": the value is a row off the page again, and the
                # line it came from can be pointed at. The other candidates are still listed,
                # because "why this one" is the question this field always raises.
                "how": "regex",
                "label": "tunjangan tanpa field sendiri",
                "line_no": pick[0] + 1,
                "line": pick[1],
                "components": {ln: v for _, ln, v in extras},
            }
            # Only the row actually taken is consumed. The others were not used, and marking
            # them used would hide them from every field still looking for a value.
            consumed.add(pick[0])

    unmatched = [f for f, v in record.items() if v is None]
    return record, unmatched, source


# ── LLM, for what the rules could not find ─────────────────────────────────


def _ask_llm(prompt: str, model: str = None, max_tokens=_UNSET):
    """Raw reply text from the configured model, whichever backend serves it. Reasoning is
    already stripped.

    `max_tokens=None` sends no cap at all; omitting it uses config.yaml's llm.max_tokens.
    """
    # `or` would turn an explicit "no cap" (None) back into the configured one, and would
    # also swallow a deliberate 0. Only an omitted argument falls back to the config.
    if max_tokens is _UNSET:
        max_tokens = LLM_MAX_TOKENS
    # A model named per call overrides config.yaml's; core.llm builds a client for it.
    cfg = dict(config.LLM)
    if model:
        cfg["model"] = model
    client = _llm_mod.build_client(cfg)
    return client.chat(prompt, max_tokens=max_tokens, temperature=LLM_TEMPERATURE)


# The old name, kept because the eval harness (scripts/labeling.py) and older notebooks call it.
_ask_bedrock = _ask_llm


def _parse_llm_json(raw: str):
    """Tolerant JSON read. gpt-oss answers in prose that may carry ``` fences or a
    <reasoning> block; other Bedrock models fence or wrap differently. One reader for all."""
    from core import bedrock

    return bedrock.extract_json(raw)


def llm_extract(text: str, model: str = None, want=None):
    """Ask the Bedrock model for the fields the rules could not find. Returns (fields, error).

    `want` is the list to ask for — normally what match_fields left empty, so a page whose
    labels were all readable never reaches a model at all. Asking only for the gaps also
    keeps the reply small, which is what the token budget was being spent on.

    Every value is taken as the model returned it. There is no parsing step — "Rp 4.000.000"
    is stored as "Rp 4.000.000", and the difference from a labeller's "4000000" is absorbed
    at comparison time by fields.same_value(). Normalising here would quietly rewrite what
    the model said, which is the one thing an evaluation of the model must not do.

    Any failure yields an empty record plus a message, so the run degrades to "OCR text,
    no fields" rather than crashing — but the reason is reported, never swallowed.
    """
    record = empty_record()
    want = list(want) if want else list(ALL_FIELDS)
    model = model or LLM_MODEL

    if len(text.strip()) < MIN_OCR_CHARS:
        # Nothing readable on the page. Sending it costs a call to be told there are no
        # fields, which is a thing we already know.
        return record, f"OCR returned {len(text.strip())} chars — below {MIN_OCR_CHARS}, no LLM call"

    # Plain substitution, not str.format() — the prompt now carries few-shot examples
    # with literal { } JSON braces, and .format() would choke on those.
    prompt = (
        LLM_PROMPT.replace("{n}", str(len(want)))
        .replace("{fields}", "\n".join(f"- {f}" for f in want))
        .replace("{text}", text[:OCR_CHAR_LIMIT])
    )
    where = f"model={model}"
    try:
        raw = _ask_bedrock(prompt, model)
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        if len(detail) > 200:
            detail = detail[:200] + "…"
        error = f"{type(exc).__name__}: {detail}"
        print(f"    ! LLM call failed — {error}", file=sys.stderr)
        print(f"      ({where}) — keeping the OCR text, no fields", file=sys.stderr)
        return record, error

    parsed = _parse_llm_json(raw)
    if parsed is None:
        # No JSON at all usually means the reply was truncated mid-reasoning. One retry
        # with a bigger budget recovers the page instead of dropping it; it only ever
        # fires on a reply that already failed, so the normal path pays nothing.
        try:
            raw = _ask_bedrock(prompt, model, max_tokens=LLM_RETRY_TOKENS)
            parsed = _parse_llm_json(raw)
            if parsed is not None:
                print(f"    · LLM reply was truncated; retried at {LLM_RETRY_TOKENS} tokens", file=sys.stderr)
        except Exception:
            pass
    if parsed is None:
        # Reporting this matters: an unreadable reply and a reply saying "nothing found"
        # both produce zero fields, and only one of them is a bug.
        snippet = (raw or "").strip().replace("\n", " ")[:120]
        error = f"unreadable reply: {snippet!r}" if snippet else "empty reply"
        print(f"    ! LLM reply could not be parsed as JSON ({where})", file=sys.stderr)
        return record, error

    for field in want:
        value = parsed.get(field)
        # A model asked for one value sometimes answers with every candidate it saw.
        # Some models do this routinely — without the unwrap the stored value is the
        # literal string "['00110895', '0012367288']". A guard against malformed output,
        # not a parser: the chosen element is stored exactly as it arrived.
        if isinstance(value, (list, tuple)):
            value = next((v for v in value if not blank(v)), None)
        if isinstance(value, dict):
            value = None
        record[field] = None if blank(value) else value

    return record, None


# ── The two layers together ───────────────────────────────────────────────


def extract_fields(
    text: str, use_llm: bool, model: str = None, note=None, page: int = 1, of: int = 1, llm_all: bool = None
):
    """OCR text in, 20 fields out, by the cheapest route that works.

    `llm_all` defaults to config.yaml's llm.all_fields; pass True or False to override it
    for one call, which is what the "LLM semua field" checkbox in the labeling app does.

    `llm_all` asks the model for ALL {n} fields instead of only the ones the rules missed.
    The extracted record does not change: a value the rules read still wins, exactly as
    without the flag. What the flag buys is a SECOND OPINION on every field — the model's
    answer is kept beside the rules' in `info["llm_compare"]`, with a per-field agreement
    mark. Two readers that fail differently disagreeing on a field is a far stronger warning
    than either reader's own confidence, and it costs one call on pages that would otherwise
    have made none.

        regex  ->  every mandatory field found?  ->  done, no model, no cost
                                                 ->  LLM for whatever is still empty

    Returns (record, source, info). `source` says where each value came from — the line a
    rule read it off, or the model that supplied it — which is what makes an answer
    checkable rather than something to be trusted.

    Gating on MANDATORY rather than "any field missing" is the whole saving: most payslips
    print six or seven of the twenty, so "all 20 filled" would never be true and every page
    would reach a model. What matters is whether the page yielded the fields that make it a
    usable payslip at all. (Edit the MANDATORY list in fields.py to change what a page costs.)
    """
    if llm_all is None:
        llm_all = LLM_ALL_DEFAULT
    record, unmatched, source = match_fields(text, allow_backward=False)
    missing_required = [f for f in MANDATORY if blank(record.get(f))]

    info = {
        "matched": len(ALL_FIELDS) - len(unmatched),
        "missing_required": missing_required,
        "llm_used": False,
        "llm_asked": [],
        "llm_error": None,
        "backend": "none",
        "llm_all": bool(llm_all),
    }

    if not use_llm:
        info["llm_error"] = "LLM off (--no-llm)"
        info["derived"] = derive_totals(record, source, text)
        return record, source, info
    # With llm_all the call is made even when the rules found everything — that page is exactly
    # where a second opinion is worth having, because nothing has questioned the rules on it.
    if not llm_all and not missing_required:
        info["llm_error"] = None
        info["skipped_llm"] = "semua field wajib sudah terisi dari regex"
        info["derived"] = derive_totals(record, source, text)
        return record, source, info

    model = model or LLM_MODEL
    if note:
        note("llm", page=page, of=of, backend="bedrock", model=model, matched=info["matched"], missing=len(unmatched))
    asked = list(ALL_FIELDS) if llm_all else list(unmatched)
    filled, error = llm_extract(text, model, want=asked)
    info.update({"llm_used": True, "llm_asked": asked, "llm_error": error, "backend": "bedrock"})

    if llm_all:
        # The comparison, recorded before the record is touched. Only fields the RULES read
        # are compared — where the rules found nothing there is no second opinion to have,
        # just the model's answer, which lands in the record below like it always did.
        compare = {}
        for f in ALL_FIELDS:
            mine, theirs = record.get(f), filled.get(f)
            if blank(mine) or blank(theirs):
                continue
            compare[f] = {"llm": theirs, "agree": bool(same_value(f, mine, theirs))}
        info["llm_compare"] = compare
        info["llm_disagree"] = sorted(f for f, c in compare.items() if not c["agree"])

        # Arbitration, when the two readers disagree on a field they BOTH answered.
        #
        # Measured over the eval pile rather than chosen: the rules winning every
        # disagreement scores 87.8%, the model winning every one 89.4%, and splitting them
        # by kind 89.5%. The split is also the one with a reason behind it — a rule finds a
        # label and takes the nearest number, which is exactly the operation a two-column
        # table defeats, and the model lost every money duel it entered (Total Potongan
        # 6-0, Total Pendapatan 3-0, Lembur 3-0, Insentif 3-0). Identity values sit against
        # their own label, the rules read them well, and a rule can say which line it used.
        #
        # LLM_ARBITRATES lists the fields the model wins. Empty it in config.yaml to get the
        # old behaviour back, where a rule answer was never overruled.
        for f in LLM_ARBITRATES:
            c = compare.get(f)
            if not c or c["agree"]:
                continue
            value = parse_money(c["llm"]) if f in MONEY_FIELDS else c["llm"]
            if blank(value):
                continue  # nothing usable: the rule's answer stands
            source[f] = {
                "how": "llm",
                "backend": "bedrock",
                "model": model,
                "arbitrated": True,
                # what the rules said, so overruling them is never invisible
                "regex_value": record.get(f),
                "regex_line": (source.get(f) or {}).get("line"),
            }
            record[f] = value

    for f in unmatched:
        if blank(filled.get(f)):
            continue
        value = filled[f]
        origin = {"how": "llm", "backend": "bedrock", "model": model}
        if f in MONEY_FIELDS:
            # PENDAPATAN and TOTAL hold digits, from either layer. The regex side already
            # does this — plausible_money(parse_money(...)) returns an int — and the model
            # side did not, so the same column showed 400000 on one row and "Rp  400,000"
            # on the next depending on which layer answered. Only the formatting is
            # touched: same_value() compares money on digits alone, so no verdict moves.
            #
            # A value with no number in it at all ("Rp" on its own, which a model returns
            # for an empty row it can see the currency mark on) is not a small number —
            # it is no number, and it is dropped rather than stored as a value that would
            # be scored against a label.
            number = parse_money(value)
            if number is None or str(number) != str(value).strip():
                origin["raw"] = value
                origin["normalised"] = True
                if number is None:
                    origin["dropped"] = True
                value = number
        if f in SPACED_FIELDS:
            # The one place a model's answer is touched, and it is the one repair that
            # cannot change what the answer says: spaces are inserted, never removed and
            # never substituted, so the value compares identically under same_value()
            # before and after. It is done because the model is shown the same glued OCR
            # the scanner produced ("WARUNGMAKANKEDAI KOPIROBUSTA") and faithfully
            # repeats it — a correct value that reads as wrong, and gets marked wrong by
            # hand. What the model actually returned is kept in `raw`.
            spaced = respace(value, text)
            if spaced != value:
                origin["raw"] = value
                origin["respaced"] = True
                value = spaced
        record[f] = value
        source[f] = origin
    info["derived"] = derive_totals(record, source, text)
    return record, source, info


# ── The totals arithmetic can supply ──────────────────────────────────────
# The one place in this module that writes a number nobody read, and it is fenced in three
# ways: it only ever fills a field that is EMPTY, everything it writes is marked
# how="derived" rather than regex or llm, and the order below is the only one that is not
# circular.
#
#     Total Pendapatan   sum of the earnings components that were read
#     Gaji Bersih        Total Pendapatan − Total Potongan
#     Total Potongan     Total Pendapatan − Gaji Bersih
#
# Gross first because it depends on nothing else. Net second. Deduction last, and only
# when the net came from the document — deriving it from a net this module just derived
# would be restating our own assumption as if it were a reading.


def _page_prints(field: str, text: str) -> bool:
    """Does any line carry a label for this field, whatever else that line also carries?

    `match_fields` gives each line one label, so on a two-column row — "Total eamings
    4.052.300  Totaldeductions  211.000" — only one of the two totals can own it. This
    asks the weaker question the guards need: not "did we read it" but "is it printed".
    """
    return any(_label_pattern(syn).search(ln) for ln in keep_content_lines(text) for syn in SYNONYMS[field])


def derive_totals(record: dict, source: dict, text: str = ""):
    """Fill whichever of the three totals arithmetic can reach. Returns the field names."""
    filled = []
    if derive_gross(record, source, text):
        filled.append(GROSS)
    if derive_net(record, source, text):
        filled.append(NET)
    if derive_deduction(record, source):
        filled.append(DEDUCTION)
    return filled


def derive_gross(record: dict, source: dict, text: str = ""):
    """Total Pendapatan from the earnings components, when nothing read one.

    Two components minimum. One is not a sum — it is an assertion that no other earnings
    exist, which is exactly what a half-read earnings block looks like. Two or more is
    positive evidence that the block itself was read, which is the thing being relied on.
    """
    if not blank(record.get(GROSS)):
        return None
    if _page_prints(GROSS, text):
        # The page DOES print a total and we failed to read it — a mangled label, a
        # mangled number, or a two-column row that gave its one label slot to the
        # deductions side. Summing components then papers over a reading failure with a
        # number that looks like a reading, and the components are usually short for the
        # same reason the total was missed.
        return None
    parts = [(f, as_int(record[f])) for f in EARNINGS if not blank(record.get(f))]
    parts = [(f, v) for f, v in parts if v is not None]
    if len(parts) < 2:
        return None
    total = sum(v for _, v in parts)
    if total <= 0:
        return None
    # One component equal to the sum of the others is not a component: it is the total,
    # sitting in an earnings row because OCR mangled its label ("GAJI BRUTO" came back as
    # "GAlIBRUTO" and its number landed under Lembur). Summing that row in as well doubles
    # the answer, so the page is left for someone to look at instead.
    if any(v * 2 == total for _, v in parts):
        return None
    record[GROSS] = total
    origin = {
        "how": "derived",
        "formula": " + ".join(f for f, _ in parts),
        "components": {f: v for f, v in parts},
        "detail": " + ".join(f"{v:,}" for _, v in parts) + f" = {total:,}",
    }
    source[GROSS] = origin
    return origin


def derive_deduction(record: dict, source: dict):
    """Total Potongan from Total Pendapatan − Gaji Bersih.

    The schema has no individual deduction fields — only this total — so a subtraction is
    the only arithmetic there is. It runs last, and it declines in two cases:

        the net was derived   then this is our own subtraction read backwards
        the difference is 0   nothing was withheld, and a blank says that better than a
                              0 does. A labeller marks that field "tidak ada di dokumen",
                              and a 0 sitting there would score as a value invented.
    """
    if not blank(record.get(DEDUCTION)):
        return None
    if (source.get(NET) or {}).get("how") == "derived":
        return None
    gross, net = as_int(record.get(GROSS)), as_int(record.get(NET))
    if gross is None or net is None:
        return None
    diff = gross - net
    if diff <= 0:
        return None
    record[DEDUCTION] = diff
    origin = {
        "how": "derived",
        "formula": "Total Pendapatan − Gaji Bersih",
        "gross": gross,
        "net": net,
        # Worth saying out loud: a gross that was itself summed makes this a second-order
        # figure, and the page should show that rather than imply two readings.
        "gross_derived": (source.get(GROSS) or {}).get("how") == "derived",
        "detail": f"{gross:,} − {net:,} = {diff:,}",
    }
    source[DEDUCTION] = origin
    return origin


def derive_net(record: dict, source: dict, text: str = ""):
    """Fill Gaji Bersih from Total Pendapatan − Total Potongan when nothing read it.

    This is the one exception to the rule the rest of this module is built on, and it is
    deliberately narrow. `validate()` below still refuses to derive anything: a filled-in
    number is not something the extractor read, and a record that mixes the two cannot be
    used to judge a model.

    What makes this one different is that it is MARKED. `source[NET]["how"]` comes back
    "derived", not "regex" and not "llm", so nothing downstream mistakes it for an
    extraction — the OCR stage stays unjudged, the annotation page shows a Σ instead of an
    R or an AI badge, and the arithmetic check below reports "derived".

    Three refusals, each protecting a different way this could quietly go wrong:

        the document said otherwise   a value that was actually read always wins
        no gross to work from         nothing to subtract from, so nothing is invented
        deductions we failed to read  a page with a potongan heading but no total read
                                      off it is a page we did not understand; net = gross
                                      would be a confident wrong answer

    Returns the provenance entry it wrote, or None when it declined.
    """
    if not blank(record.get(NET)):
        return None  # the document said otherwise; it wins

    gross = as_int(record.get(GROSS))
    if gross is None:
        return None

    deduction = as_int(record.get(DEDUCTION))
    assumed = deduction is None
    if assumed:
        if any(DEDUCTION_HEAD.match(ln) for ln in keep_content_lines(text)):
            return None  # there were deductions; we just did not read them
        deduction = 0

    net = gross - deduction
    if net < 0:
        return None  # the two numbers disagree; do not paper over it

    record[NET] = net
    origin = {
        "how": "derived",
        "formula": "Total Pendapatan − Total Potongan",
        "gross": gross,
        "deduction": deduction,
        # True when no Total Potongan was read AND the page prints no deductions heading,
        # i.e. the payslip simply has none. Recorded because it is an assumption, and an
        # assumption that is not written down is indistinguishable from a fact.
        "assumed_zero_deduction": assumed,
        # The gross may have been summed rather than read, which makes this second-order.
        "gross_derived": (source.get(GROSS) or {}).get("how") == "derived",
        "detail": f"{gross:,} − {deduction:,} = {net:,}",
    }
    source[NET] = origin
    return origin


# ── Validation (reports, never rewrites) ───────────────────────────────────


def validate(record: dict, source: dict = None):
    """Do the model's own numbers agree with each other?

    This used to be `normalize()`, and it used to fill a missing total in from the
    components. It no longer does. A derived number is not something the model read,
    and a record that mixes the two cannot be used to judge the model — every filled-in
    total would score as a correct extraction that never happened.

    So the arithmetic is still checked and still reported; the record is left alone.
    A `mismatch` here is a useful signal on its own: it is the cheapest available hint
    that a page was read badly, without anyone having to look at it.
    """
    checks = {}

    components = [as_int(record[f]) for f in ALL_FIELDS if f in MONEY_FIELDS and f not in (GROSS, DEDUCTION, NET)]
    components = [c for c in components if c is not None]
    comp_sum = sum(components) if components else None
    total = as_int(record.get(GROSS))
    how = {f: ((source or {}).get(f) or {}).get("how") for f in (GROSS, DEDUCTION, NET)}

    if how[GROSS] == "derived":
        # This sum IS the total, so comparing them checks nothing.
        checks["earnings"] = ("derived", (source[GROSS] or {}).get("detail", "total dijumlah"))
    elif comp_sum is None:
        checks["earnings"] = ("skipped", "no earnings component found")
    elif total is None:
        checks["earnings"] = ("skipped", f"components sum to {comp_sum:,}, no total read")
    elif abs(total - comp_sum) <= 1:
        checks["earnings"] = ("ok", f"{len(components)} components sum to {total:,}")
    else:
        checks["earnings"] = ("mismatch", f"components {comp_sum:,} vs total {total:,}")

    deduction, net = as_int(record.get(DEDUCTION)), as_int(record.get(NET))
    if how[NET] == "derived" or how[DEDUCTION] == "derived":
        # One side of this subtraction is where the other came from, so checking it would
        # be checking our own arithmetic. Reported as its own outcome instead of a tick it
        # cannot fail.
        derived_field = NET if how[NET] == "derived" else DEDUCTION
        checks["netpay"] = ("derived", (source[derived_field] or {}).get("detail", "dihitung"))
    elif total is None or net is None or deduction is None:
        checks["netpay"] = ("skipped", "not enough totals to check")
    elif abs((total - deduction) - net) <= 1:
        checks["netpay"] = ("ok", f"{total:,} - {deduction:,} = {net:,}")
    else:
        checks["netpay"] = ("mismatch", f"{total:,} - {deduction:,} = {total - deduction:,}, net says {net:,}")
    return checks
