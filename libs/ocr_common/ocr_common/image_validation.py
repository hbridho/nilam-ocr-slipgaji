"""The checks every uploaded document goes through before a model sees it."""

from ocr_common.config import BaseServiceSettings
from ocr_common.errors import EMPTY_FILE, FILE_TOO_LARGE, UNSUPPORTED_FILE_TYPE, BadRequest, PayloadTooLarge

# Asked for by the ML team: a payslip is typically 1-2 MB, a few reach 2.1 MB, so anything larger
# than the limit is refused before any model runs, with a message the client can show as is.
PAYLOAD_TOO_LARGE_MESSAGE = "Ukuran dokumen melebihi batas {limit}, pastikan hanya mengunggah dokumen slip gaji"

# Content types that mean "some file" and nothing more. curl sends the first by default, and Swagger
# UI sends one of these whenever the browser cannot name the type — so a perfectly good PDF arrives
# labelled as bytes. Refusing on the label alone would reject it, which is why these fall through to
# the sniff below instead of being matched against the allow-list.
GENERIC_CONTENT_TYPES = {"", "application/octet-stream", "binary/octet-stream", "application/binary"}

# First bytes that name the format for real. The client's label is a claim; this is evidence.
MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def upload_limit_label(max_upload_bytes: int) -> str:
    """`MAX_UPLOAD_BYTES` as the caller reads it: `2,5 MB`, `5 MB`."""
    megabytes = max_upload_bytes / (1024 * 1024)
    text = f"{megabytes:.1f}".rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')} MB"


def sniff_content_type(content: bytes) -> str | None:
    """The type the bytes themselves declare, or None when they match nothing known."""
    for prefix, content_type in MAGIC:
        if content.startswith(prefix):
            return content_type
    return None


def validate_image(content_type: str | None, content: bytes, settings: BaseServiceSettings) -> None:
    """Raises `BadRequest` when the content type is not allowed or the upload is empty, and
    `PayloadTooLarge` (413) when it exceeds `MAX_UPLOAD_BYTES`.

    A generic label (`application/octet-stream`, or none at all) is decided by the file's first
    bytes rather than refused: it carries no information, and refusing on it would turn every
    `curl -F` and most Swagger uploads into a 400 on a file that is perfectly valid. A label that
    names a type we do not accept is still refused on the label — that is a real mismatch, not a
    missing one.
    """
    content_type = (content_type or "").lower().split(";")[0].strip()

    if not content:
        raise BadRequest("Uploaded file is empty", EMPTY_FILE)

    if content_type in GENERIC_CONTENT_TYPES:
        sniffed = sniff_content_type(content)
        if sniffed is None or sniffed not in settings.allowed_content_types:
            raise BadRequest(
                f"Unsupported content type: {content_type or 'unknown'} "
                f"(isi berkasnya juga tidak dikenali sebagai PDF, JPEG atau PNG)",
                UNSUPPORTED_FILE_TYPE,
            )
    elif content_type not in settings.allowed_content_types:
        raise BadRequest(f"Unsupported content type: {content_type}", UNSUPPORTED_FILE_TYPE)

    if len(content) > settings.max_upload_bytes:
        raise PayloadTooLarge(
            PAYLOAD_TOO_LARGE_MESSAGE.format(limit=upload_limit_label(settings.max_upload_bytes)), FILE_TOO_LARGE
        )
