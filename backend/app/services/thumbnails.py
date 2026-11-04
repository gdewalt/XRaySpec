"""Small, cached previews for private source PDFs."""

from __future__ import annotations

import io

from ..db.models import SourceDocument

THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024


def thumbnail_object_key(source: SourceDocument) -> str:
    """Return a private, stable key tied to an immutable source row."""
    return f"thumbnails/{source.owner_id}/{source.id}/first-page.png"


def render_pdf_thumbnail(pdf_bytes: bytes, *, width: int = 180) -> bytes:
    """Render the first PDF page to a compact PNG without exposing the source."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        if len(pdf) == 0:
            raise ValueError("PDF has no pages")
        page = pdf[0]
        try:
            page_width, _ = page.get_size()
            scale = width / max(float(page_width), 1.0)
            bitmap = page.render(scale=scale)
            try:
                image = bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        pdf.close()

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
