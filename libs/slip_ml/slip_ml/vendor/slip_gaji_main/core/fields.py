"""The 20 output fields: their schema, their Indonesian vocabulary, and the value parsers.

Extraction is RapidOCR -> regex -> LLM. The regex layer is first because it is free,
deterministic and explainable: every value it produces can be traced back to the line it
was read from, which is what the "asal nilai" button in the annotation page shows. The
LLM is asked only when a MANDATORY field is still missing after the rules have run.

Comparison lives here too, in `same_value`. It is deliberately forgiving about the two
things OCR gets wrong most often and that mean nothing:

    spacing      "TriWulandari" vs "Tri Wulandari"   — the scan lost a space
    punctuation  "PT.HM Sampoerna" vs "PT HM Sampoerna"

Neither is a reading error worth counting, and scoring them as misses measures the
scanner's kerning rather than the extractor.
"""

import re
import unicodedata
from collections import Counter, OrderedDict
from functools import lru_cache

# ── The 20 fields, in output order ──────────────────────────────────────────
# These identifiers ARE the JSON keys the service returns. snake_case, and four of them
# are not a mechanical transliteration of the label a human reads — `periode` not
# `periode_gaji`, `nama_karyawan` not `nama_pegawai`, `nomor_induk_karyawan` not `nip`,
# `gaji_bersih` without the "(Take Home Pay)" — so no "lowercase and swap spaces" helper
# can stand in for this list. DISPLAY below carries the human labels for the UI.

IDENTITY = [
    "nama_perusahaan",
    "periode",
    "nama_karyawan",
    "nomor_induk_karyawan",
    "jabatan",
    "divisi",
    "status_pegawai",
]
EARNINGS = [
    "gaji_pokok",
    "tunjangan_jabatan",
    "tunjangan_transport",
    "tunjangan_makan",
    "tunjangan_komunikasi",
    "tunjangan_lain",
    "bonus",
    "insentif",
    "lembur",
    "thr",
]
# Named, because the three of them are related by arithmetic and that arithmetic is
# written out in several places — gross - deduction = net.
GROSS = "total_pendapatan"
DEDUCTION = "total_potongan"
NET = "gaji_bersih"
TOTALS = [GROSS, DEDUCTION, NET]

# What a person reads. Used by the labeling page and by anything printing a table; never
# by the pipeline itself, which speaks only the keys above.
DISPLAY = {
    "nama_perusahaan": "Nama Perusahaan",
    "periode": "Periode Gaji",
    "nama_karyawan": "Nama Pegawai",
    "nomor_induk_karyawan": "NIP",
    "jabatan": "Jabatan",
    "divisi": "Divisi",
    "status_pegawai": "Status Pegawai",
    "gaji_pokok": "Gaji Pokok",
    "tunjangan_jabatan": "Tunjangan Jabatan",
    "tunjangan_transport": "Tunjangan Transport",
    "tunjangan_makan": "Tunjangan Makan",
    "tunjangan_komunikasi": "Tunjangan Komunikasi",
    "tunjangan_lain": "Tunjangan Lain",
    "bonus": "Bonus",
    "insentif": "Insentif",
    "lembur": "Lembur",
    "thr": "THR",
    "total_pendapatan": "Total Pendapatan",
    "total_potongan": "Total Potongan",
    "gaji_bersih": "Gaji Bersih (Take Home Pay)",
}

# The reverse, for reading data written before the rename: 991 label files and ~3,000
# stored extraction results are all keyed by the old display names. Everything that loads
# stored JSON runs it through `rename_keys` rather than being migrated blindly in place,
# so a file that was never migrated still opens.
LEGACY = {v: k for k, v in DISPLAY.items()}


def rename_keys(d):
    """{old display name: v} -> {snake_case key: v}. Leaves unknown keys untouched."""
    if not isinstance(d, dict):
        return d
    return {LEGACY.get(k, k): v for k, v in d.items()}


def rename_list(xs):
    """Same, for a list of field names (llm.asked, llm.disagree, not_exist, ...)."""
    if not isinstance(xs, list):
        return xs
    return [LEGACY.get(x, x) for x in xs]


ALL_FIELDS = IDENTITY + EARNINGS + TOTALS
MONEY_FIELDS = set(EARNINGS + TOTALS)
SECTIONS = [("identitas", IDENTITY), ("pendapatan", EARNINGS), ("total", TOTALS)]

# ── Which fields decide whether the LLM is worth a call ─────────────────────
# EDIT THIS to change what an extraction costs. A page whose mandatory fields the regex
# layer already found never reaches a model at all; miss one and the LLM is asked for
# everything still empty. These six are the ones that make a payslip a payslip — who paid,
# who was paid, for when, and how much — so a page missing any of them is not usable
# regardless of how many allowances were read.
MANDATORY = ["nama_perusahaan", "nama_karyawan", "periode", "gaji_pokok", "total_pendapatan", NET]

# ── Label vocabulary ────────────────────────────────────────────────────────
# Matched longest-first, so a specific label wins over a generic one ("tunjangan makan"
# before "makan", "total potongan" before "potongan").

SYNONYMS = OrderedDict(
    [
        (
            "nama_perusahaan",
            [
                "nama perusahaan",
                "nama instansi",
                "nama toko",
                "nama kantor",
                "nama pt",
                "nama cv",
                "nama usaha",
                "nama badan hukum",
                "perusahaan/instansi",
                "nama badan usaha",
                "nama unit",
                "nama satuan kerja",
                "nama lembaga",
                "perusahaan",
                "instansi",
                "lembaga",
                "employer",
                "company name",
                "company",
                "unit usaha",
                "pemberi kerja",
                "tempat kerja",
                "nama tempat kerja",
            ],
        ),
        (
            "periode",
            [
                "periode gaji",
                "periode pembayaran",
                "periode penggajian",
                "periode bulan",
                "bulan gaji",
                "masa gaji",
                "gaji bulan",
                "untuk bulan",
                "bulan/tahun",
                "bulan dan tahun",
                "payroll period",
                "pay period",
                "period",
                "periode",
                "tgl gaji",
                "tanggal gaji",
                "bulan",
                "masa kerja gaji",
            ],
        ),
        (
            "nama_karyawan",
            [
                "nama pegawai",
                "nama karyawan",
                "nama lengkap",
                "nama penerima",
                "nama penerima gaji",
                "nama pekerja",
                "employee name",
                "name",
                "n a m a",
                "nama",
            ],
        ),
        (
            "nomor_induk_karyawan",
            [
                "nip",
                "n i p",
                "nik",
                "n i k",
                "no. induk",
                "nomor induk",
                "no induk",
                "nomor induk pegawai",
                "nomor induk karyawan",
                "no. induk karyawan",
                "id pegawai",
                "id karyawan",
                "npp",
                "no. urut",
                "no urut",
                "employee id",
                "no. pegawai",
                "no pegawai",
                "nomor pegawai",
                "employee no",
                "badge",
            ],
        ),
        (
            "jabatan",
            [
                "jabatan",
                "posisi",
                "pangkat",
                "golongan",
                "gol.",
                "job title",
                "position",
                "nama jabatan",
                "jabatan/posisi",
                "pangkat/golongan",
            ],
        ),
        (
            "divisi",
            [
                "divisi",
                "departemen",
                "department",
                "dept",
                "bagian",
                "unit kerja",
                "seksi",
                "sub bagian",
                "sub-bagian",
                "bidang",
                "satuan kerja",
                "satker",
                "unit",
            ],
        ),
        (
            "status_pegawai",
            [
                "status pegawai",
                "status karyawan",
                "status kepegawaian",
                "status kerja",
                "status pekerja",
                "jenis kepegawaian",
                "status",
                "employment status",
            ],
        ),
        (
            "gaji_pokok",
            [
                "gaji pokok",
                "upah pokok",
                "gaji dasar",
                "gapok",
                "gj pokok",
                "gaji pokok/bulan",
                "basic salary",
                "base salary",
                "pokok",
            ],
        ),
        (
            "tunjangan_jabatan",
            [
                "tunjangan jabatan",
                "tunj. jabatan",
                "tunj jabatan",
                "t. jabatan",
                "tj jabatan",
                "position allowance",
                "tunjangan struktural",
                "tunjangan fungsional",
            ],
        ),
        (
            "tunjangan_transport",
            [
                "tunjangan transport",
                "tunjangan transportasi",
                "tunj. transport",
                "tunj transport",
                "t. transport",
                "uang transport",
                "uang jalan",
                "transportasi",
                "transport",
                "transpor",
                "tunjangan bbm",
                "bbm",
            ],
        ),
        (
            "tunjangan_makan",
            [
                "tunjangan makan",
                "tunj. makan",
                "tunj makan",
                "t. makan",
                "uang makan",
                "uang konsumsi",
                "konsumsi",
                "tunjangan konsumsi",
                "makan",
                "meal allowance",
            ],
        ),
        (
            "tunjangan_komunikasi",
            [
                "tunjangan komunikasi",
                "tunj. komunikasi",
                "t. komunikasi",
                "uang komunikasi",
                "tunjangan pulsa",
                "komunikasi",
                "pulsa",
                "telepon",
            ],
        ),
        (
            "tunjangan_lain",
            [
                "tunjangan lain-lain",
                "tunjangan lainnya",
                "tunjangan lain",
                "tunjangan tetap",
                "tunjangan umum",
                "tunj. lain",
                "tunj lain",
                "lain-lain",
                "lainnya",
                "tunjangan khusus",
                "tunjangan kesejahteraan",
            ],
        ),
        ("bonus", ["bonus", "uang bonus", "bonus tahunan", "bonus kinerja"]),
        (
            "insentif",
            [
                "insentif",
                "incentive",
                "uang insentif",
                "insentif kinerja",
                "premi",
                "tunjangan kinerja",
                "tukin",
            ],
        ),
        (
            "lembur",
            [
                "upah lembur",
                "uang lembur",
                "lembur",
                "overtime",
                "ot",
                "jam lembur",
            ],
        ),
        (
            "thr",
            [
                "tunjangan hari raya",
                "thr",
                "t.h.r",
                "tunjangan hari raya keagamaan",
            ],
        ),
        (
            "total_pendapatan",
            [
                "total pendapatan",
                "total penghasilan",
                "jumlah pendapatan",
                "jumlah penghasilan",
                "total gaji",
                "jumlah gaji",
                "penghasilan bruto",
                "pendapatan bruto",
                "gaji kotor",
                "gaji bruto",
                "total bruto",
                "bruto",
                "gross income",
                "gross salary",
                "total earnings",
                "total pengbasilan",
                "jumlah kotor",
                "total a",
                "sub total",
                # Gross goes by a dozen names, and the (A) that labels its column is one of them.
                "total penghasilan bruto",
                "jumlah penghasilan bruto",
                "total pendapatan bruto",
                "total penerimaan",
                "jumlah penerimaan",
                "penerimaan kotor",
                "penerimaan",
                "total upah",
                "jumlah upah",
                "upah bruto",
                "penghasilan kotor",
                "gaji brutto",
                "brutto",
                "total brutto",
                "total pendapatan (a)",
                "total penghasilan (a)",
                "jumlah pendapatan (a)",
                "total (a)",
                "jumlah (a)",
                "sub total a",
                "subtotal a",
                "sub total pendapatan",
                "subtotal pendapatan",
                "subtotal earnings",
                "total gross",
                "gross pay",
                "gross earnings",
                "total income",
                "total pay",
                "jumlah seluruh pendapatan",
                "gaji bruto/bulan",
                # OCR damage seen in this corpus, kept as data rather than patched into the
                # matcher: "rn" reads as "m" ("earnings" -> "eamings"), and a capital J reads as a
                # lower l ("GAJI BRUTO" -> "GAlIBRUTO"). Without these the total is not read at
                # all — and a total that is not read is worse than one read imperfectly, because
                # it gets filed as whatever component happens to share its line.
                "total eamings",
                "total earmings",
                "total eaming",
                "sub total eamings",
                "subtotal eamings",
                "gali bruto",
                "gaii bruto",
                "gajl bruto",
            ],
        ),
        (
            "total_potongan",
            [
                "total potongan",
                "jumlah potongan",
                "total pengurangan",
                "jumlah pengurangan",
                "total deduction",
                "total deductions",
                "potongan gaji",
                "jumlah dipotong",
                "total b",
                "potongan",
                # Same story on the deductions column, plus the English a payroll system prints.
                "total seluruh potongan",
                "jumlah seluruh potongan",
                "total potongan gaji",
                "total potongan (b)",
                "jumlah potongan (b)",
                "total (b)",
                "jumlah (b)",
                "sub total b",
                "subtotal b",
                "sub total potongan",
                "subtotal potongan",
                "subtotal deductions",
                "sub total deductions",
                "total deduksi",
                "jumlah deduksi",
                "total pot",
                "jml potongan",
                "jml pengurangan",
                "potongan-potongan",
                "total pot gaji",
                "total dipotong",
                # Same story on this side: "deductions" comes back transposed often enough to list.
                "total deducatoins",
                "total deducations",
                "sub total deducatoins",
                "subtotal deducatoins",
                "total deduetions",
            ],
        ),
        (
            NET,
            [
                "gaji bersih",
                "take home pay",
                "take-home pay",
                "jumlah diterima",
                "jumlah yang diterima",
                "gaji diterima",
                "gaji yang diterima",
                "gaji di terima",
                "penghasilan bersih",
                "pendapatan bersih",
                "total diterima",
                "netto",
                "net pay",
                "net salary",
                "thp",
                "tanda terima gaji",
                "yang diterima",
                "diterima",
                "gaji netto",
                "jumlah bersih",
                # Longest-first matching means these run BEFORE the bare words above, which is the
                # whole point: OCR glues a label to the word in front of it
                # ("JumlahPenghasilanbersih"), and the lookbehind in _label_pattern refuses to
                # start a match mid-word. Spelling the full phrase out is what lets it anchor at
                # the start of the line instead.
                "jumlah penghasilan bersih yang dibayarkan",
                "penghasilan bersih yang dibayarkan",
                "jumlah penghasilan bersih",
                "total penghasilan bersih",
                "jumlah penerimaan bersih",
                "total penerimaan bersih",
                "penerimaan bersih",
                "jumlah pendapatan bersih",
                "total pendapatan bersih",
                "total gaji bersih",
                "jumlah gaji bersih",
                "gaji bersih diterima",
                "total yang diterima",
                "jumlah yang harus diterima",
                "gaji yang harus diterima",
                "total gaji diterima",
                "total terima",
                "jumlah terima",
                "total take home pay",
                "total thp",
                "take home",
                "takehome pay",
                "gaji yang dibayarkan",
                "jumlah yang dibayarkan",
                "upah bersih",
                "jumlah netto",
                "penerimaan netto",
                "gaji nett",
                "nett pay",
                "net income",
                "net amount",
                "total net pay",
                "total net",
                "netto diterima",
                "sisa gaji",
                "gaji akhir",
            ],
        ),
    ]
)


# ── Signature blocks ────────────────────────────────────────────────────────
# Every payslip in this corpus ends with "Diterima oleh, <name>" over a signature line,
# and "diterima" is also how half of them label the net pay. Without this, the reader
# takes the signature block for the net-pay label and then reads whatever number happens
# to sit near the bottom of the page.
#
# The digit test is what keeps the guard from eating a real row: a line that carries a
# number is a value line whatever else it says, and only a line with no number at all is
# dismissed as a place for someone to sign.
SIGNATURE = re.compile(
    r"\b(di\s?terima|di\s?bayar(?:kan)?|dibuat|disetujui|disahkan|mengetahui|"
    r"hormat\s+kami|penerima|pemberi|yang\s+menerima)\b"
    # Either the line ends right there on a comma — the caption over a signature — or the
    # next word is "oleh". Neither is true of "Total Diterima", which is a real label with
    # its value on the following line, and which an end-anchor alone would have eaten.
    r"(?:\s*[,.]\s*$|\s*[,.]?\s+(?:oleh|olch|ol[ea]h|oieh|kami)\b)",
    re.I,
)


def is_signature_line(line) -> bool:
    """True for a line that is somewhere to sign, not somewhere a value is printed."""
    s = (line or "").strip()
    return bool(s) and not re.search(r"\d", s) and bool(SIGNATURE.search(s))


# ── Company names ───────────────────────────────────────────────────────────
# "PT" and "CV" are NOT labels. Treating them as one makes the matcher hand back
# "IMPLEMENTASI TEKNOLOGI INDONESIA" for a line reading "PT. IMPLEMENTASI TEKNOLOGI
# INDONESIA" — the legal form is part of the name, and dropping it changes the answer.
# So a line that OPENS with one of these is taken whole, spacing and all.
COMPANY_PREFIX = re.compile(
    r"^\s*("
    r"p\.?\s?t\.?|c\.?v\.?|u\.?d\.?|p\.?d\.?|n\.?v\.?|f\.?a\.?"  # badan usaha
    r"|perum|persero|perseroan|koperasi|kopkar|yayasan|firma"
    r"|pemerintah|pemkab|pemkot|pemprov|dinas|badan|kantor|balai|sekretariat"  # instansi
    r"|kementerian|lembaga|direktorat|puskesmas|rsud?|rs\.?u?|klinik|apotek"
    r"|universitas|sekolah|smk|sma|smp|sd|madrasah|pesantren|akademi|politeknik"
    r"|toko|warung|bengkel|hotel|restoran|rumah\s+makan|salon|percetakan"
    r")(?![a-z])",
    re.I,
)

# A trailing legal form, so "… Tbk" and "… (Persero)" survive as part of the name.
COMPANY_SUFFIX = re.compile(r"\b(tbk\.?|persero|\(persero\)|indonesia)\s*$", re.I)


# A letterhead OCR'd without its spaces: "PTGELORASUKSESBERSAMA". The normal rule refuses
# this on purpose — it cannot tell where "PT" ends — but the line is still the employer,
# and attributing it to a rule with a source line beats leaving it to a model. Restricted
# to all-caps runs so ordinary prose starting with those letters cannot match.
COMPANY_GLUED = re.compile(r"^(PT|CV|UD|PD|NV|FA)[A-Z][A-Z0-9.\-]{5,}$")


def looks_like_company(line: str) -> bool:
    """Does this line open with a legal form or an institution word?

    Deliberately anchored: "PT" inside a sentence is not a company name, and a line that
    merely mentions one is not the employer field.
    """
    if not line:
        return False
    s = line.strip()
    if len(s) < 4:
        return False
    if COMPANY_GLUED.match(s):
        return True
    if not COMPANY_PREFIX.match(s):
        return False
    # "PT" alone, or a prefix with nothing after it, names nobody.
    rest = COMPANY_PREFIX.sub("", s, count=1).strip(" .:،,-")
    return len(rest) >= 3


# An allowance line that no field of its own claimed. "Tunj.Apresiasi",
# "TunjanganPremium" — real earnings with names this 20-field schema has no slot for, and
# which would otherwise be dropped on the floor.
OTHER_ALLOWANCE = re.compile(
    r"^(?:[A-Z0-9]{1,3}[.):]\s*)?" r"(tunjangan|tunj\s*\.?|tj\s*\.?|uang|allowance|allow\s*\.?)\s*\S",
    re.I,
)

# Money-shaped tokens, for counting rather than parsing. More than one on a line means a
# merged table row — "TUNJ.TERPENCIL  TUNJ.BERAS  TUNI.PAJAK  T2.420  13,555  144,84" is
# three columns of a PNS register, not one allowance, and summing it would be nonsense.
MONEY_TOKEN = re.compile(r"\d[\d.,]{2,}")


# Lines that are page furniture in this corpus, not payslip content.
CHROME = re.compile(
    r"^(foto\s+slip\s+gaji|brispot|halaman\s+\d+(\s+dari\s+\d+)?|page\s+\d+(\s+of\s+\d+)?"
    r"|confidential|rahasia|\*+|-+|=+)$",
    re.I,
)

# Headings that start the deductions block. Earnings are read preferentially from outside
# it, so "Potongan Makan" is not mistaken for "tunjangan_makan".
DEDUCTION_HEAD = re.compile(
    r"^([A-Z0-9]{1,3}[.):]\s*)?"
    r"(potongan|pengurangan|deduction[s]?|komponen\s+potongan|daftar\s+potongan"
    r"|iuran|bpjs)\b",
    re.I,
)

# The words a payslip uses for its COLUMN headings. A line built out of nothing but these
# is the table's header row, not anybody's value — "INCOME  DEDUCTION" is two column titles
# that the reading-order pass joined into one line, and it was being read as the employee's
# division. SECTION_HEAD cannot catch it: that one anchors a single heading to the whole
# line, and this is two of them side by side.
_HEAD_WORDS = {
    "income",
    "incomes",
    "deduction",
    "deductions",
    "earning",
    "earnings",
    "pendapatan",
    "penghasilan",
    "potongan",
    "pengurangan",
    "penerimaan",
    "amount",
    "jumlah",
    "total",
    "subtotal",
    "sub",
    "nominal",
    "nilai",
    "keterangan",
    "uraian",
    "rincian",
    "komponen",
    "item",
    "items",
    "description",
    "remarks",
    "no",
    "nomor",
    "rp",
    "idr",
    "gaji",
    "upah",
}


def is_column_head(line) -> bool:
    """True when the line is nothing but two or more column headings.

    A digit anywhere disqualifies it: headings carry no numbers, and without that guard a
    money line like "Rp 4.500.000" is all-heading-words too and every amount disappears.
    One word alone is left to SECTION_HEAD, which knows which single words are headings in
    their own right — "Total" on its own is a heading, but so is a value someone typed.
    """
    s = str(line or "")
    if re.search(r"\d", s):
        return False
    words = [w for w in re.split(r"[^A-Za-z]+", s) if w]
    return len(words) >= 2 and all(w.lower() in _HEAD_WORDS for w in words)


# A bare section heading is never a value.
SECTION_HEAD = re.compile(
    r"^([A-Z0-9]{1,3}[.):]\s*)?"
    r"(penghasilan|pendapatan|upah|gaji|potongan|pengurangan|deduction[s]?|earnings?|"
    r"data\s*pegawai|data\s*karyawan|komponen[\s-]*komponen\s*gaji|komponen\s*gaji|"
    r"rincian|keterangan|uraian)\s*:?\s*$",
    re.I,
)

# ── Company names ───────────────────────────────────────────────────────────
# Indonesian legal forms. Most payslips in this corpus print the company at the top of the
# page with NO label in front of it, so a synonym list can never reach it — the form itself
# is the only marker there is. OCR also glues the form to the name ("PT.IMPLEMENTASI") and
# splits it ("P T . H M"), which is why the pattern tolerates dots and spaces inside it.

COMPANY_FORM = re.compile(
    r"^\s*(?P<form>"
    # The form never swallows its own trailing dot — that dot is the separator, and
    # "PT.IMPLEMENTASI" has nothing else between the form and the name.
    r"P\s*\.?\s*T"  # PT, P.T., P T
    r"|C\s*\.?\s*V|U\s*\.?\s*D|P\s*\.?\s*D"  # CV, UD, PD
    r"|PERUM|PERSEROAN|PERSERO|KOPERASI|KOPKAR|YAYASAN|FIRMA|FA"
    r"|RUMAH\s+SAKIT|RSUD|RSUP|RS|APOTEK|TOKO|CAFE|RESTORAN|RESTO"
    # Uniformed services pay their staff on slips that name the unit and nothing else —
    # without these the letterhead scan walked past "POLRES BANJARNEGARA" and settled on
    # the payroll bank printed further down.
    r"|POLRES|POLSEK|POLDA|POLRESTA|KODIM|KOREM|KODAM|LANAL|LANUD"
    r"|BANK|BPR|BMT|LSM|LPD"
    # Either a punctuation separator (glued names) or whitespace. Never nothing: without
    # this "PTERA" would read as PT + ERA.
    r")(?:\s*[.\-:]\s*|\s+)(?P<name>\S.*)$",
    re.I,
)

# Canonical spelling per form, so "P.T.IMPLEMENTASI" and "PT IMPLEMENTASI" come out the
# same. Spacing after the form is normalised too — it is the difference between
# "PT.IMPLEMENTASI TEKNOLOGI" and "PT. IMPLEMENTASI TEKNOLOGI", and that gap is exactly
# what makes a company name readable.
_FORM_CANON = {
    "pt": "PT.",
    "cv": "CV.",
    "ud": "UD.",
    "pd": "PD.",
    "fa": "Fa.",
    "perum": "Perum",
    "persero": "Persero",
    "perseroan": "Perseroan",
    "koperasi": "Koperasi",
    "kopkar": "Kopkar",
    "yayasan": "Yayasan",
    "firma": "Firma",
    "rumahsakit": "Rumah Sakit",
    "rs": "RS.",
    "rsud": "RSUD",
    "rsup": "RSUP",
    "apotek": "Apotek",
    "toko": "Toko",
    "cafe": "Cafe",
    "resto": "Resto",
    "restoran": "Restoran",
    "bank": "Bank",
    "bpr": "BPR",
    "bmt": "BMT",
    "lsm": "LSM",
    "lpd": "LPD",
}

# The trailing marks a company name may legitimately carry.
_SUFFIX = re.compile(r"\b(tbk|persero|group|indonesia|nusantara)\b\.?$", re.I)


# The identity fields whose spacing is repaired. Periode Gaji is left out: it is
# normalised to YYYY-MM, where a space would be a bug rather than a repair.
SPACED_FIELDS = [f for f in IDENTITY if f != "periode"]


def clean_company(text, context=""):
    """Tidy a company name, keeping its legal form and fixing the spacing around it.

        'PT.IMPLEMENTASI TEKNOLOGI'   -> 'PT. IMPLEMENTASI TEKNOLOGI'
        'P.T. HM Sampoerna  Tbk'      -> 'PT. HM Sampoerna Tbk'
        'cv sumber  rejeki'           -> 'CV. Sumber  rejeki'  (case of the name is kept)
        'WARUNGMAKANKEDAI KOPIROBUSTA'-> 'WARUNG MAKAN KEDAI KOPI ROBUSTA'

    The form is normalised because OCR spells it a dozen ways and none of them mean
    anything different. The NAME keeps every letter it was printed with — the only thing
    added to it is the spaces the scan dropped, by `respace`, and `context` (the
    page's OCR text) is what lets that prefer the document's own spacing over a guess.
    """
    base = clean_identity(text)
    if not base:
        return None
    base = respace(base, context)
    m = COMPANY_FORM.match(base)
    if not m:
        return base
    key = re.sub(r"[^a-z]", "", m.group("form").lower())
    form = _FORM_CANON.get(key)
    if not form:
        return base
    name = re.sub(r"\s+", " ", m.group("name")).strip(" .,:-")
    return f"{form} {name}".strip() if name else base


# A bare "Bank X" line is almost always the PAYROLL bank, not the employer — nearly every
# slip in this corpus prints one. Where the employer really is a bank the slip writes the
# legal form too ("PT. BANK RAKYAT INDONESIA (PERSERO) Tbk"), which matches on PT instead.
# So these are kept as candidates but ranked last, rather than being thrown away.
_WEAK_FORM = re.compile(r"^\s*(BANK|BPR|BMT|LPD)(?![A-Za-z])", re.I)


def looks_like_company(line):
    """True when a line is a company name standing on its own, with no label in front."""
    if not line or len(line) < 5:
        return False
    m = COMPANY_FORM.match(line.strip())
    if not m:
        return False
    name = m.group("name").strip()
    # A form followed by digits is an address or an account number, not a name.
    if not re.search(r"[A-Za-z]{3}", name) or re.match(r"^\d", name):
        return False
    # "Bank Acc :0242948696" satisfies every test above — BANK is a form and "Acc" is three
    # letters — and it is a bank account row, not the employer. What separates it from a
    # real name is the account number, not the length: "BANK BRI" and "BPR Arta" are short
    # and genuine. So the weak forms reject a colon or a digit in the name and nothing else.
    if _WEAK_FORM.match(line.strip()) and re.search(r"[\d:：]", name):
        return False
    return True


def is_weak_company(line) -> bool:
    """True for a company line that names a bank and nothing stronger."""
    return bool(_WEAK_FORM.match(str(line or "").strip()))


# ── Putting the spaces back into a glued company name ───────────────────────
# RapidOCR loses the spaces inside a letterhead often enough that it is the single most
# common reason a CORRECT company name looks wrong on the annotation page:
#
#     WARUNGMAKANKEDAI KOPIROBUSTA     the right employer, spelled right, unreadable
#     PTGELORASUKSESBERSAMA
#     PT.BINTANGINDOKARYAGEMILANG
#
# same_value() already forgives this — it compares letters only — so the machine scores
# these correct. A human reading the column does not, and marks them false by hand. That
# manual flag is the thing being fixed here: the value is right, so it should LOOK right.
#
# The damage survives into the model's answer too, because the model is shown the same
# glued OCR text and repeats it. So the repair is applied to both layers.
#
# Two ways to put the spaces back, tried in this order, because only one of them is free
# of invention:
#
#   1. the page itself. A letterhead is usually printed more than once — a header and a
#      stamp, or a header and an English "PAYSLIP" block — and the second copy is often
#      spaced correctly. PT.BINTANGINDOKARYAGEMILANG sits four lines above
#      "PT Bintang Indokarya Gemilang" on the same page. Matching the glued value against
#      the page's own text returns the DOCUMENT's spacing, not ours.
#   2. a word list, and only when the split is fully accounted for. Every piece must be a
#      word on the list, or the run is left exactly as it was — half a guess is worse than
#      an honest original. The one relaxation is a single unknown piece at the FRONT, for
#      the very common shape of a brand name followed by generic words: FINNSBEACHCLUB is
#      "FINNS" + beach + club, and no word list will ever contain "finns".
#
# Nothing here rewrites letters. Only spaces are inserted, never removed, never changed —
# so a re-spaced value is always the same string to same_value() as the raw one, and the
# raw one is kept in the provenance either way.

# Indonesian (and the English that shows up in Indonesian letterheads) business
# vocabulary. This is DATA, not logic: add a word when a real letterhead needs it. A word
# only ever makes a split possible, never forces one — the segmenter refuses any split it
# cannot account for end to end.
COMPANY_VOCAB = set(
    """
pt cv ud pd nv fa perum persero perseroan koperasi kopkar yayasan firma tbk group grup
warung warteg kedai kantin makan makanan minuman kopi robusta arabika teh susu jus es
cafe kafe coffee resto restoran restaurant rumah makan sakit bersalin apotek apotik
klinik laboratorium optik hotel motel losmen wisma griya graha puri pondok villa
toko tokoh mini market minimarket swalayan supermarket grosir gudang depot agen
bengkel servis salon barbershop laundry binatu katering catering bakery roti kue
pabrik percetakan printing konveksi garmen tekstil furniture mebel keramik plastik
sekolah madrasah pesantren universitas akademi politeknik institut kampus bimbel
bank bpr bmt lsm lpd rs rsu rsud rsup puskesmas posyandu
dinas kantor badan lembaga instansi pemerintah kabupaten kota provinsi kecamatan
desa kelurahan departemen kementerian direktorat sekretariat balai unit pusat daerah
agama pendidikan kesehatan keuangan pertanian perhubungan sosial tenaga kerja
karya karsa cipta bakti guna daya jasa sarana prasarana solusi sistem teknologi teknik
teknindo industri manufaktur konstruksi kontraktor properti realty land estate
logistik transport transportasi trans kargo cargo ekspedisi ekspres express delivery
media digital kreatif studio desain konsultan consulting management manajemen mandiri
sumber daya alam mineral energi tambang batubara sawit kelapa karet rubber kayu
tani pertanian ternak peternakan perikanan pangan boga rasa nikmat sedap lezat
jaya abadi sejahtera makmur sentosa bersama mandiri utama perkasa sukses gemilang
selaras lestari prima primer inti indo indah agung mulia megah raya besar baru maju
sejati sentral central global nusantara indonesia internasional nasional
anugerah anugrah berkah barokah amanah rahmat rezeki rejeki berkat syukur
bintang cahaya sinar surya matahari terang fajar pagi senja bulan langit awan
tirta tirto air samudra samudera bahari laut mitra partner rekan sahabat kawan
putra putri anak keluarga saudara bina pembina duta citra kencana permata mutiara
mas emas perak logam baja besi beton semen bata pasir
alam hijau asri sejuk segar bersih suci murni
pan java jawa sunda bali medan batak minang bugis papua borneo kalimantan sumatera
sulawesi lombok madura banten cirebon solo jogja yogya semarang surabaya bandung
jakarta bekasi bogor depok tangerang malang kediri jember gresik sidoarjo
beach club resort spa lounge bar kitchen house home school world city center centre
food drink fresh green blue red gold silver star sun moon sea sky
tama loka wangi sari dewi ratu raja king queen prince royal grand mega super ultra
software hardware
kab kabupaten prov kec kel jl jln
staf staff pegawai karyawan guru kepala wakil waka admin administrasi operator teknisi
supervisor manager manajer direktur asisten assistant executive ceo cfo hrd payroll
software hardware team tim maintenance produksi marketing sales kasir satpam security
driver sopir office cleaning service helper crew leader anggota staff pelaksana
pengawas koordinator kepegawaian umum keuangan personalia akuntansi perpajakan
tetap kontrak harian borongan magang tidak masuk belum sudah kawin menikah lajang
percobaan probation permanen aktif nonaktif honorer tenaga pns pppk asn
daftar rincian keterangan satuan wilayah cabang seksi bidang bagian sub
kurikulum kesiswaan sarana prasarana humas perpustakaan laboratorium
""".split()
)

# Everything above is split on whitespace, so ONLY words belong inside those quotes —
# a line of prose in there becomes vocabulary, and "…they are fragments…" once put "are"
# on the list and turned "A22ITSOFTWARETEAM" into "A22ITSOFTW ARE TEAM". Hence the guard.
#
# Deliberately absent for the same reason: soft, ware, net, work, link, info, data, web,
# tech. Each is a fragment rather than a word, and each buys exactly one bad split.
assert all(w.isalpha() and len(w) >= 2 for w in COMPANY_VOCAB), sorted(
    w for w in COMPANY_VOCAB if not w.isalpha() or len(w) < 2
)

# The longest word on the list, so the segmenter never looks further back than it must.
_VOCAB_MAX = max(len(w) for w in COMPANY_VOCAB)

# Two-letter words are legal forms, and a legal form only ever opens a name. Allowing
# them anywhere lets "SENTOSA" become "sen"+"tosa"-shaped nonsense elsewhere.
_VOCAB_SHORT_HEAD = {"pt", "cv", "ud", "pd", "nv", "fa", "rs", "jl"}

# Below six letters there is nothing to split: "KOPI", "BONE" and "FINNS" are words.
_GLUED_RUN = re.compile(r"[A-Za-z]{6,}")

# Words that split a run even when their neighbours are unknown. The full-coverage rule
# above cannot touch "BAPENDAKABUPATENBREBES" — an agency abbreviation and a regency name
# will never be on a word list — but "KABUPATEN" sitting in the middle of it is not a
# coincidence, and cutting there is right whatever the pieces around it turn out to be.
#
# Restricted to administrative and organisational words on purpose. A generic word used
# this way would cut people's names in half; these do not appear inside one. Every piece
# left over still has to be at least three characters, which is what keeps "DINASTI" from
# becoming "DINAS TI".
_ANCHORS = sorted(
    {
        "kabupaten",
        "kecamatan",
        "kelurahan",
        "provinsi",
        "pemerintah",
        "pemkab",
        "pemkot",
        "pemprov",
        "kementerian",
        "departemen",
        "direktorat",
        "sekretariat",
        "inspektorat",
        "puskesmas",
        "universitas",
        "politeknik",
        "madrasah",
        "pesantren",
        "sekolah",
        "yayasan",
        "koperasi",
        "dinas",
        "badan",
        "bagian",
        "bidang",
        "satuan",
        "daerah",
        "wilayah",
        "cabang",
        "kantor",
        "pendapatan",
        "keuangan",
        "pendidikan",
        "kesehatan",
        "pegawai",
        "karyawan",
        "daftar",
        "jabatan",
        "gaji",
        "kerja",
    },
    key=len,
    reverse=True,
)


def _anchor_split(run):
    """Cut a glued run at an administrative word, keeping the unknown pieces whole."""
    low = run.lower()
    for anchor in _ANCHORS:  # longest first
        i = low.find(anchor)
        if i < 0:
            continue
        left, mid, right = run[:i], run[i : i + len(anchor)], run[i + len(anchor) :]
        if (left and len(left) < 3) or (right and len(right) < 3):
            continue
        parts = [
            p
            for p in (
                _segment(left) or left if left else None,
                mid,
                _anchor_split(right) or _segment(right) or right if right else None,
            )
            if p
        ]
        if len(parts) > 1:
            return " ".join(parts)
    return None


def _loose_index(text):
    """(loose form, index map) — `index[i]` is where `loose[i]` came from in `text`.

    The same folding `_loose` does (letters and digits only, lowercased, accents
    dropped), but character by character so a match in the folded string can be sliced
    back out of the original WITH its spacing intact. That slice is the whole point: the
    spacing returned is the document's, not something this module made up.
    """
    return _loose_index_cached(str(text))


@lru_cache(maxsize=8192)
def _loose_index_cached(s: str):
    """Kept apart from `_loose_index` so the cache only ever sees a hashable str.

    Memoised because `_respace_from_context` refolds EVERY line of a page for EVERY value
    it is asked about — five identity fields per page, each rescanning the same eighty
    lines. The folding is per character and so cannot be vectorised away; not repeating it
    is the whole win. The index map is returned as a tuple: a cached list handed to a
    caller that mutated it would corrupt every later hit, and callers only ever index it.
    """
    loose, index = [], []
    if s.isascii():
        # The overwhelming majority of OCR text. NFKD is the identity on ASCII and there
        # are no combining marks to drop, so the general path below is pure overhead —
        # and it was calling unicodedata twice per character, two million times a table.
        for i, ch in enumerate(s):
            c = ch.lower()
            if c.isalnum():
                loose.append(c)
                index.append(i)
    else:
        for i, ch in enumerate(s):
            for c in unicodedata.normalize("NFKD", ch).lower():
                if c.isascii() and c.isalnum():
                    loose.append(c)
                    index.append(i)
    return "".join(loose), tuple(index)


def _respace_from_context(value, context):
    """The same name as `value`, spelled with its spaces, if the page prints it that way.

    Searches each line of the page text for a run of characters that folds to exactly the
    same letters as `value`, and returns the best-spaced one found. Line by line rather
    than across the whole text, because joining two lines would invent a space at a line
    break that may not be a word boundary at all.
    """
    key, _ = _loose_index(value)
    if len(key) < 8:
        return None  # too short to identify a letterhead uniquely
    have = len(str(value).split())
    best = None
    for line in str(context).splitlines():
        line = line.strip()
        if len(line) < len(key):
            continue
        loose, index = _loose_index(line)
        at = loose.find(key)
        while at != -1:
            cand = line[index[at] : index[at + len(key) - 1] + 1].strip()
            if len(cand.split()) > max(have, len(best.split()) if best else 0):
                best = cand
            at = loose.find(key, at + 1)
    return best


def _known_split(run):
    """Split `run` into vocabulary words only, fewest pieces first. None if it cannot be
    accounted for end to end.

    Fewest pieces is what keeps a word that is on the list from being broken into two
    that also are: "indonesia" stays whole and never becomes "indo" + "nesia".
    """
    low = run.lower()
    n = len(low)
    best = [None] * (n + 1)  # best[i] = cut points covering low[:i]
    best[0] = []
    for i in range(1, n + 1):
        for j in range(max(0, i - _VOCAB_MAX), i):
            if best[j] is None:
                continue
            piece = low[j:i]
            if piece not in COMPANY_VOCAB:
                continue
            if len(piece) < 3 and not (j == 0 and piece in _VOCAB_SHORT_HEAD):
                continue
            cut = best[j] + [(j, i)]
            if best[i] is None or len(cut) < len(best[i]):
                best[i] = cut
    if best[n] is None:
        return None
    return [run[a:b] for a, b in best[n]]


# A legal form glued to the front of a name is always its own word. Only PT and CV get
# the unconditional peel: no Indonesian word starts with either, whereas "FAkultas" and
# "UDayana" would be cut in half by the same rule applied to FA or UD.
_FORM_HEAD = re.compile(r"^(PT|CV)(?=[A-Za-z])", re.I)


def _segment(run, min_tail=2):
    """Re-space one glued run of letters, or return None to leave it alone."""
    words = _known_split(run)
    if words:
        # A run that IS one word on the list is a word, not a run. Saying so here is what
        # stops "PEMERINTAH" from being handed to the guesswork below.
        return " ".join(words) if len(words) > 1 else None

    # A legal form glued to the front: "PTGELORASUKSESBERSAMA" is PT and then a name,
    # whatever the name turns out to be. Once the form is off, the rest is known to be a
    # company name, so one known word is enough to trust a split of it.
    m = _FORM_HEAD.match(run)
    if m:
        rest = run[m.end() :]
        if len(rest) >= 4:
            return f"{m.group(0)} {_segment(rest, min_tail=1) or rest}"

    # One unknown piece, at the front only: a brand name followed by what the business
    # is — FINNSBEACHCLUB, and no word list will ever hold "finns". Known words have to
    # follow it, so "SAMPOERNA" cannot become "SAMPO" + "erna" on the strength of one
    # lucky match, and the unknown head is never re-entered for a second guess.
    for cut in range(4, len(run) - 2):
        tail = _known_split(run[cut:])
        if tail and len(tail) >= min_tail:
            return " ".join([run[:cut]] + tail)

    # Last resort: cut at an administrative word even when its neighbours are unknown.
    return _anchor_split(run)


def _respace_token(token):
    """Re-space one whitespace-free token, leaving its punctuation exactly where it is."""
    out = []
    for piece in re.split(r"([^A-Za-z]+)", token):
        if len(piece) >= 6 and piece.isalpha():
            out.append(_segment(piece) or piece)
        else:
            out.append(piece)
    return "".join(out)


def respace(value, context=""):
    """Insert the missing spaces into a company name. Never changes a letter.

        'WARUNGMAKANKEDAI KOPIROBUSTA'  -> 'WARUNG MAKAN KEDAI KOPI ROBUSTA'
        'PT.BINTANGINDOKARYAGEMILANG'   -> 'PT Bintang Indokarya Gemilang'  (from the page)
        'PT.HM Sampoerna Tbk'           -> unchanged, nothing is glued
        'HOMETCHOOLNG'                  -> unchanged, OCR damage is not spacing

    `context` is the page's OCR text, and is what makes the second example possible: the
    name is already printed with its spaces further down the page. Without it the word
    list does the work alone, which is enough for the first example and refuses the third.
    """
    if blank(value):
        return value
    text = str(value).strip()
    if not _GLUED_RUN.search(text):
        return text  # nothing glued, nothing to repair
    if context:
        found = _respace_from_context(text, context)
        if found:
            return clean_identity(found) or text
    spaced = " ".join(_respace_token(t) for t in text.split())
    return spaced if spaced != text else text


# A bare currency marker or separator carries no value.
NOISE = re.compile(r"^(rp\.?|idr|:|-|–|—|\.+|,-|\|| |x)$", re.I)

MONTHS = {
    "jan": 1,
    "januari": 1,
    "january": 1,
    "feb": 2,
    "februari": 2,
    "february": 2,
    "peb": 2,
    "pebruari": 2,
    "mar": 3,
    "maret": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "mei": 5,
    "may": 5,
    "jun": 6,
    "juni": 6,
    "june": 6,
    "jul": 7,
    "juli": 7,
    "july": 7,
    "agu": 8,
    "ags": 8,
    "agt": 8,
    "agustus": 8,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "okt": 10,
    "oktober": 10,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "nop": 11,
    "nopember": 11,
    "des": 12,
    "desember": 12,
    "dec": 12,
    "december": 12,
}

# A single number. A space is only allowed inside it when a digit follows, so two numbers
# side by side in a table cell ("2,715,492.00  0.00") are not glued into one.
_MONEY = re.compile(r"\d(?:[\d.,]|\s(?=\d))*\d|\d")

# ── Cents, which this corpus does not have ─────────────────────────────────
# Every amount on these payslips is whole rupiah and every cents pair is ",00". It means
# nothing, it is not what the labels record, and read as part of the number it is a
# hundredfold error — "Rp 600.000,00" becoming 60,000,000.
#
# The separator is the whole difficulty, because OCR loses it three different ways:
#
#     600.000,00     intact
#     600.000.00     the comma became a dot
#     600.00000      it vanished — five digits in a group that holds three
#
# The third is why this is not a one-line strip. With no separator left to see, the only
# evidence is the shape of the number itself: an Indonesian thousands group is three
# digits, so a five-digit final group is three digits plus a cents pair that lost its
# comma. A four-digit group is ambiguous and is left alone.
_CENTS_SEP = re.compile(r"(.*\d)[.,](\d{2})$")  # …000,00  /  …000.00
_CENTS_GLUED = re.compile(r"(.*[.,]\d{3})(\d{2})$")  # …000 00 with nothing between


def digits_without_cents(raw) -> str:
    """The digits of a money token, with a trailing cents pair removed.

        '4.000.000,00' -> '4000000'      '600.00000'  -> '600000'
        '2,715,492.00' -> '2715492'      '4.000.000'  -> '4000000'  (untouched)

    Nothing is dropped unless at least three digits precede the pair, so a bare "1,00"
    is not turned into a lone 1.
    """
    raw = str(raw).strip()
    for rule in (_CENTS_SEP, _CENTS_GLUED):
        m = rule.fullmatch(raw)
        if m and len(_DIGITS.sub("", m.group(1))) >= 3:
            return _DIGITS.sub("", m.group(1))
    return _DIGITS.sub("", raw)


def empty_record():
    """All 20 fields, in order, unset."""
    return OrderedDict((f, None) for f in ALL_FIELDS)


# ── Parsers ─────────────────────────────────────────────────────────────────


def parse_money(text):
    """'Rp 4.000.000,-' -> 4000000.  Returns None when there is no number.

    Handles both conventions this corpus contains, and the mixtures OCR produces:
    Indonesian '4.000.000', US '2,715,492.00', and mangled '2.036,619.00'.
    """
    if text is None:
        return None
    s = str(text)
    s = re.sub(r"(?i)\b(rp|idr)\b\.?", " ", s)
    s = s.replace(" ", " ")  # OCR emits non-breaking spaces inside numbers
    m = _MONEY.search(s)
    if not m:
        return None
    raw = re.sub(r"\s", "", m.group(0)).strip(".,")
    if not raw:
        return None
    digits = digits_without_cents(raw)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def parse_period(text, context=""):
    """Normalise a pay period to YYYY-MM. 'Mei-25' -> '2025-05'.

    Falls back to the raw text when nothing recognisable is found, so a value is never
    silently thrown away.

    `context` is the rest of the page, consulted only when the period line names a month
    but no year — "Periode : November", "GAJI BULAN  JANUARI". That is a common layout:
    the year is printed once in the header or in a transaction date and the period line
    does not repeat it. Without the context those pages returned "????-11", which is not a
    date anyone can use and which no comparison can ever match.
    """
    if not text:
        return None
    s = str(text).strip()
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s
    m = re.search(r"([A-Za-z]{3,9})[\s\-/,.]*(\d{4}|\d{2})\b", s)  # Mei-25, Desember 2023
    if m:
        word = m.group(1).lower()
        month = MONTHS.get(word)
        if not month:
            # OCR glued the label to the month: "PERIODEDESEMBER 2023" captures "EDESEMBER"
            month = next((n for name, n in MONTHS.items() if len(name) >= 4 and word.endswith(name)), None)
        if month:
            return f"{_year(m.group(2))}-{month:02d}"
    m = re.search(r"\b(\d{4})[-/](\d{1,2})\b", s)  # 2025-08, 2025/8
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r"\b(\d{1,2})[-/](\d{4})\b", s)  # 08/2025
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.search(r"\b([A-Za-z]{3,9})\b", s)  # month name alone
    if m and MONTHS.get(m.group(1).lower()):
        return f"{year_from(context) or '????'}-{MONTHS[m.group(1).lower()]:02d}"
    return s


# \b at both ends on purpose: it keeps the four digits inside an 18-digit NIP or a bank
# account number from being read as a year.
_YEAR_TOKEN = re.compile(r"\b((?:19|20)\d{2})\b")

# A year that sits inside a real date — 01/12/2023, 2023-12-01 — is worth far more than a
# loose four-digit number, because a garbled letterhead produces those by accident:
# "UD. GREEN GURU  70 2024  a ri 3" is not a date and 2024 is not that slip's year.
_DATE_YEAR = re.compile(
    r"\b\d{1,2}[/\-.]\d{1,2}[/\-.]((?:19|20)\d{2})\b"  # 01/12/2023
    r"|\b((?:19|20)\d{2})[/\-.]\d{1,2}[/\-.]\d{1,2}\b"
)  # 2023-12-01


def year_from(context):
    """The most likely calendar year of the PERIOD a page covers, or None.

    Years inside a date are considered first and alone; only when the page has none does
    the search widen to every four-digit number on it.

    Two rules, both learned from being wrong:

    Outliers are dropped. A scan turns "2.000.000" and stray glyphs into things like 2000
    and 2058, and one page offered [2024, 2000, 2000, 2000, 2024] — frequency alone then
    picks 2000. A payslip's own years sit within a few years of each other, so anything
    more than five years from the newest candidate is scanner noise, not a date.

    A tie goes to the EARLIEST year, not the latest. This said "latest" first, reasoning
    that the pay date is later than the period — which is true, and exactly backwards: the
    field wanted is the PERIOD, and the period always precedes the date the slip was
    printed. A page carrying both 2025 and 2026 is a 2025 slip printed in 2026.
    """
    if not context:
        return None
    s = str(context)
    dated = [int(g) for m in _DATE_YEAR.finditer(s) for g in m.groups() if g]
    dated = [y for y in dated if 1990 <= y <= 2099]
    years = dated or [int(y) for y in _YEAR_TOKEN.findall(s) if 1990 <= int(y) <= 2099]
    if not years:
        return None
    newest = max(years)
    years = [y for y in years if newest - y <= 5]
    counts = Counter(years)
    return min(counts, key=lambda y: (-counts[y], y))


def _year(token):
    """'25' -> 2025, '2023' -> 2023."""
    return int(token) if len(token) == 4 else 2000 + int(token)


# "ID / Name  2058 / Bobby Septian", "ID/Name  :0831 - I GUSTI NGURAH SAPUTRA". One cell
# holding two values, which is a layout the label matcher cannot see: it matched "Name",
# so it hands back the whole cell including the id in front of it. The id is digits (dots
# and dashes allowed, as staff numbers are often written 08.30.09.24) followed by a
# separator and then something that is not a digit.
_ID_THEN_NAME = re.compile(r"^\s*[:：]?\s*(\d[\d.\-]{0,15})\s*[-/|]\s*(?=\D)")


def split_id_name(text, want):
    """Pull either half out of a shared id/name cell. `want` is 'id' or 'name'.

    Returns None when the text is not that shape, so the caller falls through to its normal
    handling and a plain name is never mistaken for half of a pair.
    """
    if text is None:
        return None
    s = str(text)
    m = _ID_THEN_NAME.match(s)
    if not m:
        return None
    return m.group(1) if want == "id" else s[m.end() :].strip() or None


def clean_identity(text):
    """Tidy an identity value: drop a leading colon, collapse runs of whitespace.

    Runs of spaces collapse to one; single spaces are left exactly where they are. That
    matters most for company names, where "PT. HM Sampoerna" and "PT.HM Sampoerna" are
    different strings and the one on the document is the one to keep — this function must
    not decide between them.

    Rejects values with no alphanumeric content at all (stray '\\', '---'), which show up
    as artefacts in scanned-form text layers.
    """
    if text is None:
        return None
    s = re.sub(r"\s+", " ", str(text)).strip()
    # "NIP/187310092008012007" leaves the slash behind when the label pattern stops at
    # "nomor_induk_karyawan", and a value that opens with punctuation is a separator the label missed.
    s = re.sub(r"^[:：/|,;\-–\s]+", "", s).strip()  # OCR often emits the full-width colon
    s = re.sub(r"[\s:：/|,;.\-–]+$", "", s).strip()
    if not re.search(r"\w", s):
        return None
    return s or None


def plausible_money(value):
    """Filter out numbers too small to be a rupiah payslip amount.

    Zero is kept (a genuine "no deductions"), but a bare 70 or 12 is a row number, a page
    count or a table index that happened to sit next to a label.
    """
    if value is None:
        return None
    return value if value == 0 or value >= 1000 else None


def keep_content_lines(text):
    """Split into stripped lines, dropping blanks and page furniture."""
    return [ln for ln in (raw.strip() for raw in (text or "").splitlines()) if ln and not CHROME.match(ln)]


def looks_like_value(line):
    """True when a line could be a value rather than a label or a section heading."""
    return bool(line) and not NOISE.match(line) and not SECTION_HEAD.match(line) and not is_column_head(line)


# ── Comparing a label against what the extractor returned ───────────────────

_DIGITS = re.compile(r"\D+")
# Everything that is not a letter or a digit. Punctuation and spacing are exactly what a
# scan mangles, and neither changes what a value means.
_LOOSE = re.compile(r"[^0-9a-z]+")


def blank(value) -> bool:
    """True for the several ways a field arrives empty: None, "", whitespace, and the
    dashes a model uses when it means "nothing here"."""
    if value is None:
        return True
    s = str(value).strip()
    return s == "" or s in {"-", "--", "---", "—", "–", "null", "None", "N/A", "n/a"}


def _loose(text: str) -> str:
    """Letters and digits only, lowercased, accents folded.

    This is what makes "TriWulandari" and "Tri Wulandari" the same answer, and
    "PT.HM Sampoerna" the same as "PT HM Sampoerna". Case goes too: a scan that reads
    a name in caps has still read the name.
    """
    return _loose_cached(str(text))


@lru_cache(maxsize=8192)
def _loose_cached(s: str) -> str:
    """Memoised because the callers fold WHOLE PAGES, once per field.

    `value_in_ocr` and `label_in_ocr` each fold the entire page text to answer about one
    field, so a twenty-field page folded the same thousand characters forty times. The
    result depends only on the string, so the second fold onwards is free.
    """
    if s.isascii():
        return _LOOSE.sub("", s.lower())  # NFKD and combining are no-ops here
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return _LOOSE.sub("", s.lower())


def same_value(field: str, mine, theirs) -> bool:
    """Does the extractor's answer mean the same as the label?

    Money compares on digits alone; everything else compares on letters and digits alone.
    Both ignore spacing and punctuation, because those are what OCR damages and neither
    carries meaning on a payslip. What is NOT forgiven is a different character: "Sampoerna"
    and "Sampooma" stay different, which is the misreading worth counting.
    """
    if blank(mine) or blank(theirs):
        return blank(mine) and blank(theirs)
    a, b = str(mine).strip(), str(theirs).strip()
    if field == "periode":
        # Both sides go through the field's own normal form first. The extractor is
        # required to emit YYYY-MM, but a labeller types what the document prints —
        # "Desember 2024" — and comparing those two as strings marked a correct answer
        # wrong. Only the normalised pair is compared; if either side will not normalise,
        # the loose comparison below still applies.
        pa, pb = parse_period(a), parse_period(b)
        if pa and pb and re.fullmatch(r"\d{4}-\d{2}", pa) and re.fullmatch(r"\d{4}-\d{2}", pb):
            return pa == pb
    if field in MONEY_FIELDS:
        da, db = _DIGITS.sub("", a), _DIGITS.sub("", b)
        da, db = da.lstrip("0") or "0", db.lstrip("0") or "0"
        if da == db:
            return True
        # Trailing cents: 4000000 and 4000000.00 are the same salary.
        for x, y in ((da, db), (db, da)):
            if x.endswith("00") and (x[:-2].lstrip("0") or "0") == y:
                return True
        return False
    return _loose(a) == _loose(b)


def as_int(value):
    """The digits of a money field as an int, for the arithmetic checks."""
    if blank(value):
        return None
    digits = digits_without_cents(value)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None
