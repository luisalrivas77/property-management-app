"""Turning a stored original into the document block a request carries (phase-2.md §5.2).

No OCR engine: the API reads the PDF itself (text layer and page images) or the image. pypdf
is used for counting and splitting pages only; the original stays untouched (principle 6), a
chunk is a new in-memory PDF of some of its pages.
"""

import base64
import io

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

PDF = "application/pdf"
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
# Beyond this many pages in total a document is refused rather than split into ever more
# requests: the API's own per-request ceiling (settings.MAX_AI_PAGES), as a cost guard.
MAX_TOTAL_PAGES = 600

UNSUPPORTED_MEDIA_TYPE = "ai.unsupported_media_type"
TOO_MANY_PAGES = "ai.too_many_pages"
UNREADABLE = "ai.unreadable"  # a PDF pypdf cannot open (damaged, or encrypted with a password)


class DocumentInputError(Exception):
    """The original cannot be sent; `code` is the proposal's error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def check_media_type(media_type: str) -> None:
    if media_type != PDF and media_type not in IMAGE_TYPES:
        raise DocumentInputError(UNSUPPORTED_MEDIA_TYPE)


def page_count(data: bytes) -> int:
    reader = _reader(data)
    try:
        count = len(reader.pages)
    except (PdfReadError, ValueError, KeyError, TypeError):
        raise DocumentInputError(UNREADABLE) from None
    if count < 1:
        raise DocumentInputError(UNREADABLE)
    if count > MAX_TOTAL_PAGES:
        raise DocumentInputError(TOO_MANY_PAGES)
    return count


def chunks(pages: int, max_pages: int) -> list[tuple[int, int]]:
    """Page ranges (0-based start, exclusive stop) of at most max_pages each."""
    return [(start, min(start + max_pages, pages)) for start in range(0, pages, max_pages)]


def pdf_pages(data: bytes, start: int, stop: int) -> bytes:
    """A new PDF of pages [start, stop) of `data`; the original bytes when that is all of
    it, so a short document is sent exactly as stored."""
    reader = _reader(data)
    if start == 0 and stop >= len(reader.pages):
        return data
    writer = PdfWriter()
    try:
        for index in range(start, stop):
            writer.add_page(reader.pages[index])
        out = io.BytesIO()
        writer.write(out)
    except (PdfReadError, ValueError, KeyError, TypeError):
        raise DocumentInputError(UNREADABLE) from None
    return out.getvalue()


def document_block(data: bytes, media_type: str, *, citations: bool) -> dict:
    """The content block for a PDF (a `document`, citations on or off) or an image (an
    `image`; the API cites documents only, so values read from a photo carry no source).

    Citations cite the PDF's text layer, so a PDF with none (photos combined into a PDF by
    the capture page, D30, or a scan) is sent with citations off: its values carry no source,
    exactly as a photo's do, instead of risking a request the API may refuse."""
    encoded = base64.standard_b64encode(data).decode("ascii")
    source = {"type": "base64", "media_type": media_type, "data": encoded}
    if media_type == PDF:
        block: dict = {"type": "document", "source": source}
        if citations and has_text_layer(data):
            block["citations"] = {"enabled": True}
        return block
    return {"type": "image", "source": source}


def has_text_layer(data: bytes) -> bool:
    """Whether any page of the PDF has extractable text (False for a PDF pypdf cannot read:
    the caller's own checks refuse that one)."""
    try:
        return any(
            (page.extract_text() or "").strip() for page in PdfReader(io.BytesIO(data)).pages
        )
    except Exception:  # pypdf raises a wide range of errors on damaged files
        return False


def _reader(data: bytes) -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentInputError(UNREADABLE)
    except DocumentInputError:
        raise
    except Exception:  # pypdf raises a wide range of errors on damaged files
        raise DocumentInputError(UNREADABLE) from None
    return reader
