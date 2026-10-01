"""Fitur guardrail — dipakai BERSAMA oleh guard_fit.py (latih) dan guard_score.py (saji).

Tidak mengimpor sklearn. Featuriser yang sama persis di kedua sisi adalah satu-satunya
cara menjamin skor saat serving sama dengan skor saat validasi; model skor keyakinan yang
lama pernah punya dua salinan featuriser, dan keduanya diam-diam bergeser.

OpenCV, pypdfium2 dan Pillow diimpor DI DALAM fungsi piksel, bukan di atas berkas. Guardrail yang
berjalan di produksi adalah yang berbasis teks, dan jalur piksel mati secara bawaan
(GUARDRAILS_PIXEL_BACKEND=off) — mengimpornya di atas berarti setiap image guardrails memikul
OpenCV untuk kode yang tidak pernah dipanggil, dan gagal start kalau paketnya tidak ada.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

# ── teks ────────────────────────────────────────────────────────────────────

# Pembungkus BRISPOT mencetak baris-baris ini di SETIAP berkas — termasuk yang bukan slip
# gaji, karena judulnya ditentukan saat unggah, bukan oleh isinya. Membiarkannya sama saja
# memberi model petunjuk yang tidak ada hubungannya dengan dokumen.
WRAPPER = re.compile(
    r"^\s*(foto\s+slip\s+gaji|brispot|halaman\s+\d+\s+dari\s+\d+" r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\s*$", re.I
)

# Token bawaan TfidfVectorizer (\b\w\w+\b) DITAMBAH satu syarat: harus memuat minimal satu huruf.
#
# normalise_text mengubah setiap digit menjadi 0, sehingga tanpa syarat ini kosakata terisi token
# seperti "0000 0000", "000000000000000000" dan "00 rp" — yang sebenarnya mengukur PANJANG nomor
# yang kebetulan ada di korpus ini (nomor surat, NIP, nomor rekening). Diukur: membuangnya
# menaikkan AUC 0,979 -> 0,985 pada kosakata 200 kolom, dan menaikkan band murni 88 -> 97.
# Keberadaan nominal tetap terwakili oleh 8 ciri layout, yang mengukurnya secara langsung.
#
# Dipakai DUA sisi: guard_fit.Union memberikannya ke TfidfVectorizer sebagai token_pattern, dan
# text_grams() memakainya saat menyajikan. Mengubahnya di sini saja sudah menjaga paritas; mengubah
# salah satu sisi sendirian akan membuat skor produksi berbeda dari skor validasi tanpa galat apa pun.
TOKEN = re.compile(r"(?u)\b(?=\w*[a-z])\w\w+\b")


def strip_wrapper(text: str) -> str:
    return "\n".join(ln for ln in (text or "").splitlines() if not WRAPPER.match(ln))


def normalise_text(t: str) -> str:
    # angka diseragamkan: model harus belajar "ada nominal di sini", bukan menghafal gaji
    return re.sub(r"\d", "0", (t or "").lower())


def text_grams(t: str, ngram_max: int = 2):
    """Unigram + bigram, persis seperti analyzer TfidfVectorizer(ngram_range=(1, 2))."""
    toks = TOKEN.findall(normalise_text(t))
    out = list(toks)
    for n in range(2, ngram_max + 1):
        out += [" ".join(toks[i : i + n]) for i in range(len(toks) - n + 1)]
    return out


# ── piksel ──────────────────────────────────────────────────────────────────

RENDER_DPI = 72  # garis tabel dan tata letak sudah jelas di 72 DPI
MAX_PAGES = 3  # slip gaji tiga bulan = tiga halaman; halaman ke-4 dst. tidak menambah
WIDTH = 800  # semua halaman diseragamkan lebarnya sebelum diukur

PIX_NAMES = (
    [
        "aspect",
        "gray_mean",
        "gray_std",
        "ink",
        "colorful",
        "saturation",
        "edges",
        "hline_frac",
        "hline_n",
        "vline_frac",
        "vline_n",
        "text_rows",
        "row_runs",
        "cc_density",
        "cc_area",
        "sharpness",
        "margin_top",
        "margin_bottom",
    ]
    + [f"grid_{r}{c}" for r in range(4) for c in range(4)]
    + ["n_pages"]
)


def render_pages(path: Path):
    import pypdfium2 as pdfium
    from PIL import Image

    path = Path(path)
    if path.suffix.lower() == ".pdf":
        doc = pdfium.PdfDocument(str(path))
        n = len(doc)
        imgs = [doc[i].render(scale=RENDER_DPI / 72).to_pil().convert("RGB") for i in range(min(n, MAX_PAGES))]
        doc.close()
        return imgs, n
    return [Image.open(path).convert("RGB")], 1


def page_features(img):
    import cv2

    rgb = np.asarray(img)
    h, w = rgb.shape[:2]
    aspect = w / h
    rgb = cv2.resize(rgb, (WIDTH, max(1, int(h * WIDTH / w))), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    H, W = gray.shape

    _, ink_mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = ink_mask > 0

    r, g, b = (rgb[..., i].astype(np.float64) for i in range(3))
    rg, yb = r - g, 0.5 * (r + g) - b
    colorful = np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    sat = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[..., 1].mean() / 255.0
    edges = cv2.Canny(gray, 60, 160).mean() / 255.0

    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, W // 12), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, H // 12)))
    hl = cv2.morphologyEx(ink_mask, cv2.MORPH_OPEN, hk)
    vl = cv2.morphologyEx(ink_mask, cv2.MORPH_OPEN, vk)
    hline_n = cv2.connectedComponents(hl)[0] - 1
    vline_n = cv2.connectedComponents(vl)[0] - 1

    prof = ink.mean(axis=1)
    rows_on = prof > 0.01
    text_rows = rows_on.mean()
    row_runs = int(np.count_nonzero(np.diff(rows_on.astype(np.int8)) == 1))

    n_cc, _, stats, _ = cv2.connectedComponentsWithStats(ink_mask, connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA] if n_cc > 1 else np.array([0])
    cc_density = (n_cc - 1) / (H * W) * 1e4
    cc_area = float(np.median(areas)) if len(areas) else 0.0
    sharpness = np.log1p(cv2.Laplacian(gray, cv2.CV_64F).var())

    ys = np.where(rows_on)[0]
    margin_top = ys[0] / H if len(ys) else 1.0
    margin_bottom = (H - ys[-1]) / H if len(ys) else 1.0
    grid = [
        ink[int(H * rr / 4) : int(H * (rr + 1) / 4), int(W * cc / 4) : int(W * (cc + 1) / 4)].mean()
        for rr in range(4)
        for cc in range(4)
    ]

    return [
        aspect,
        gray.mean() / 255.0,
        gray.std() / 255.0,
        ink.mean(),
        colorful / 100.0,
        sat,
        edges,
        hl.mean() / 255.0,
        hline_n,
        vl.mean() / 255.0,
        vline_n,
        text_rows,
        row_runs,
        cc_density,
        np.log1p(cc_area),
        sharpness,
        margin_top,
        margin_bottom,
    ] + grid


LAYOUT_NAMES = [
    "n_blocks",
    "block_rows",
    "n_columns",
    "right_aligned",
    "left_aligned",
    "table_cells",
    "block_h_median",
    "block_w_cv",
    "wide_blocks",
    "fill_ratio",
]


def page_layout(img):
    """Tata letak blok teks dari piksel — tanpa OCR.

    Tinta di-dilate mendatar supaya kata sebaris menyatu jadi satu blok. Slip gaji punya
    ciri yang tidak dimiliki surat: dua-tiga kolom, dan kolom nominal yang RATA KANAN
    (banyak blok berbagi tepi kanan yang sama); surat berisi blok selebar halaman.
    """
    import cv2

    rgb = np.asarray(img)
    h, w = rgb.shape[:2]
    rgb = cv2.resize(rgb, (WIDTH, max(1, int(h * WIDTH / w))), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    H, W = gray.shape
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # buang garis tabel dulu, supaya blok teks tidak tersambung lewat garis
    hl = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (W // 12, 1)))
    vl = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, H // 12)))
    text_ink = cv2.subtract(ink, cv2.bitwise_or(hl, vl))
    blob = cv2.dilate(text_ink, cv2.getStructuringElement(cv2.MORPH_RECT, (14, 3)))
    n, _, st, _ = cv2.connectedComponentsWithStats(blob, connectivity=8)
    boxes = [
        s for s in st[1:] if s[cv2.CC_STAT_AREA] > 60 and s[cv2.CC_STAT_HEIGHT] >= 5 and s[cv2.CC_STAT_HEIGHT] < H * 0.2
    ]
    if not boxes:
        return [0.0] * len(LAYOUT_NAMES)
    x0 = np.array([b[0] for b in boxes])
    ww = np.array([b[2] for b in boxes])
    y0 = np.array([b[1] for b in boxes])
    hh = np.array([b[3] for b in boxes])
    x1 = x0 + ww
    rows = len(np.unique((y0 + hh / 2) // max(4, np.median(hh))))

    def aligned(edges, tol=6):
        # porsi blok yang tepinya berbagi posisi dengan >= 2 blok lain
        e = np.sort(edges)
        return float(np.mean([np.sum(np.abs(e - v) <= tol) >= 3 for v in edges]))

    # kolom = celah vertikal kosong pada proyeksi blok, dihitung di bagian tengah halaman
    occ = np.zeros(W, bool)
    for a, b in zip(x0, x1, strict=False):
        occ[a:b] = True
    runs = int(np.count_nonzero(np.diff(occ.astype(np.int8)) == 1)) + int(occ[0])
    cells = cv2.connectedComponents(cv2.bitwise_not(cv2.bitwise_or(hl, vl)))[0] - 1 if (hl.any() and vl.any()) else 0
    return [
        math_log1p(len(boxes)),
        math_log1p(rows),
        float(runs),
        aligned(x1),
        aligned(x0),
        math_log1p(max(0, cells)),
        float(np.median(hh)) / 20.0,
        float(ww.std() / max(1, ww.mean())),
        float(np.mean(ww > W * 0.6)),
        float((ww * hh).sum()) / (H * W),
    ]


def math_log1p(v):
    return float(np.log1p(v))


def layout_features(path: Path):
    imgs, _ = render_pages(path)
    return np.array([page_layout(im) for im in imgs], dtype=np.float64).mean(axis=0).tolist()


def pixel_features(path: Path):
    imgs, n_pages = render_pages(path)
    per = np.array([page_features(im) for im in imgs], dtype=np.float64)
    return per.mean(axis=0).tolist() + [float(np.log1p(n_pages))]
