"""OCR candidate extraction (DESIGN.md §12.3).

Renders a page to a bitmap (pypdfium2) and OCRs it (Tesseract), producing the
same normalized ``Word`` model the native pipeline consumes — so a scanned page
flows through the *same* line-reconstruction as a born-digital one, just with
``extraction_method="ocr"`` and per-word confidence.

The heavy binaries (pypdfium2, Tesseract) are imported lazily and only run inside
the isolated worker. ``ocr_available`` is the readiness smoke test §12.3 requires:
importing the library is not proof the executable + language data work.
"""

from __future__ import annotations

from .config import ExtractionConfig
from .model import Word


def ocr_available() -> bool:
    """True only if the Tesseract executable is actually runnable."""
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def words_from_tsv(
    data: dict, width: float, height: float, *, min_confidence: float
) -> list[Word]:
    """Convert a pytesseract ``image_to_data`` DICT to normalized words (pure)."""
    words: list[Word] = []
    n = len(data.get("text", []))
    for i in range(n):
        if int(data["level"][i]) != 5:  # 5 == word level
            continue
        text = str(data["text"][i]).strip()
        if not text:
            continue
        conf = float(data["conf"][i])
        if conf < min_confidence:
            continue
        left, top = float(data["left"][i]), float(data["top"][i])
        w, h = float(data["width"][i]), float(data["height"][i])
        words.append(
            Word(
                text=text,
                x0=left / width,
                y0=top / height,
                x1=(left + w) / width,
                y1=(top + h) / height,
                confidence=conf,
                block_num=int(data["block_num"][i]) if "block_num" in data else None,
                paragraph_num=int(data["par_num"][i]) if "par_num" in data else None,
                line_num=int(data["line_num"][i]) if "line_num" in data else None,
            )
        )
    return words


def render_page(pdf_bytes: bytes, page_index: int, dpi: int):
    """Render one page to a PIL image at ``dpi`` (pypdfium2)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page = doc[page_index]
        bitmap = page.render(scale=dpi / 72.0)
        return bitmap.to_pil()
    finally:
        doc.close()


def ocr_page_words(
    pdf_bytes: bytes, page_index: int, config: ExtractionConfig, *, psm: int | None = None
) -> list[Word]:
    """OCR a page. ``psm`` overrides the page-segmentation mode — use the sparse
    mode (§12.7) for drawing pages, where numeral labels are isolated, not prose."""
    import pytesseract
    from pytesseract import Output

    image = render_page(pdf_bytes, page_index, config.ocr_dpi)
    tess_config = f"--psm {psm}" if psm is not None else ""
    data = pytesseract.image_to_data(
        image,
        lang="+".join(config.ocr_languages),
        config=tess_config,
        output_type=Output.DICT,
    )
    return words_from_tsv(
        data, image.width, image.height, min_confidence=config.ocr_min_confidence
    )
