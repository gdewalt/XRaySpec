"""OCR rendering, cleanup, deskewing, and layout-specific recognition.

Specification scans use a cleaned full-page pass for structural tokens followed
by independent left/right column passes. Drawing sheets use a higher-resolution
sparse-text pass at four orientations; every box is transformed back to the
original PDF coordinate system before downstream figure/callout detection.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

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
    """Render a PDF page to a PIL image at ``dpi``."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page = doc[page_index]
        bitmap = page.render(scale=dpi / 72.0)
        return bitmap.to_pil()
    finally:
        doc.close()


def _otsu_threshold(image) -> int:
    """Return an Otsu threshold for an 8-bit grayscale PIL image."""
    histogram = image.histogram()[:256]
    total = sum(histogram)
    if not total:
        return 128
    weighted_total = sum(index * count for index, count in enumerate(histogram))
    background_weight = 0
    background_sum = 0
    best_variance = -1.0
    best_threshold = 128
    for threshold, count in enumerate(histogram):
        background_weight += count
        if background_weight == 0:
            continue
        foreground_weight = total - background_weight
        if foreground_weight == 0:
            break
        background_sum += threshold * count
        background_mean = background_sum / background_weight
        foreground_mean = (weighted_total - background_sum) / foreground_weight
        variance = background_weight * foreground_weight * (background_mean - foreground_mean) ** 2
        if variance > best_variance:
            best_variance = variance
            best_threshold = threshold
    return best_threshold


def _projection_score(image) -> float:
    """Horizontal-projection sharpness used to select a small deskew angle."""
    from PIL import ImageOps

    gray = ImageOps.grayscale(image)
    threshold = _otsu_threshold(gray)
    binary = gray.point(lambda value: 1 if value < threshold else 0)
    width, height = binary.size
    pixels = list(binary.getdata())
    margin_x = max(1, width // 25)
    margin_y = max(1, height // 25)
    rows = [
        sum(pixels[y * width + margin_x : y * width + width - margin_x])
        for y in range(margin_y, height - margin_y)
    ]
    return float(
        sum(
            (current - previous) ** 2
            for previous, current in zip(rows, rows[1:], strict=False)
        )
    )


def estimate_skew_angle(image, config: ExtractionConfig) -> float:
    """Estimate a bounded small-angle correction from horizontal text projections."""
    from PIL import ImageOps

    sample = ImageOps.grayscale(image)
    if sample.width > 600:
        height = max(1, round(sample.height * 600 / sample.width))
        sample = sample.resize((600, height))
    maximum = max(0.0, config.ocr_deskew_max_degrees)
    step = max(0.1, config.ocr_deskew_step_degrees)
    candidates: list[float] = []
    angle = -maximum
    while angle <= maximum + 1e-6:
        candidates.append(round(angle, 3))
        angle += step
    scores = {
        candidate: _projection_score(
            sample.rotate(candidate, resample=0, expand=False, fillcolor=255)
        )
        for candidate in candidates
    }
    baseline = scores.get(0.0, _projection_score(sample))
    best = max(candidates, key=lambda candidate: scores[candidate])
    # Avoid needless resampling when the projection improvement is marginal.
    return best if abs(best) >= step / 2 and scores[best] > baseline * 1.03 else 0.0


def preprocess_image(image, config: ExtractionConfig):
    """Normalize background/contrast, remove scanner edges, and deskew in place."""
    from PIL import ImageDraw, ImageOps

    clean = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=(1, 1))
    angle = estimate_skew_angle(clean, config)
    if angle:
        clean = clean.rotate(angle, resample=3, expand=False, fillcolor=255)
        clean = ImageOps.autocontrast(clean, cutoff=(1, 1))
    # Scanner shadows and black page edges generate false punctuation. Whitening
    # the outer 0.8% preserves the PDF coordinate frame while removing that noise.
    edge = max(4, round(min(clean.size) * 0.008))
    draw = ImageDraw.Draw(clean)
    draw.rectangle((0, 0, clean.width, edge), fill=255)
    draw.rectangle((0, clean.height - edge, clean.width, clean.height), fill=255)
    draw.rectangle((0, 0, edge, clean.height), fill=255)
    draw.rectangle((clean.width - edge, 0, clean.width, clean.height), fill=255)
    return clean, angle


def _tesseract_words(image, config: ExtractionConfig, *, psm: int) -> list[Word]:
    import pytesseract
    from pytesseract import Output

    tess_config = (
        f"--oem 1 --psm {psm} "
        "-c thresholding_method=2 -c preserve_interword_spaces=1"
    )
    data = pytesseract.image_to_data(
        image,
        lang="+".join(config.ocr_languages),
        config=tess_config,
        output_type=Output.DICT,
    )
    return words_from_tsv(
        data, image.width, image.height, min_confidence=config.ocr_min_confidence
    )


def _word_with_box(word: Word, box: tuple[float, float, float, float]) -> Word:
    return Word(
        text=word.text,
        x0=max(0.0, min(1.0, box[0])),
        y0=max(0.0, min(1.0, box[1])),
        x1=max(0.0, min(1.0, box[2])),
        y1=max(0.0, min(1.0, box[3])),
        confidence=word.confidence,
        block_num=word.block_num,
        paragraph_num=word.paragraph_num,
        line_num=word.line_num,
    )


def _transform_box(word: Word, transform) -> tuple[float, float, float, float]:
    points = [
        transform(word.x0, word.y0),
        transform(word.x1, word.y0),
        transform(word.x0, word.y1),
        transform(word.x1, word.y1),
    ]
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _map_crop_words(
    words: Iterable[Word], crop: tuple[float, float, float, float]
) -> list[Word]:
    x0, y0, x1, y1 = crop
    width, height = x1 - x0, y1 - y0
    return [
        _word_with_box(
            word,
            _transform_box(word, lambda x, y: (x0 + x * width, y0 + y * height)),
        )
        for word in words
    ]


def _unrotate_words(
    words: Iterable[Word], angle: float, size: tuple[int, int]
) -> list[Word]:
    """Map boxes from a PIL-rotated, same-size image back to the source image."""
    if not angle:
        return list(words)
    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    width, height = size

    def inverse(x: float, y: float) -> tuple[float, float]:
        dx, dy = (x - 0.5) * width, (y - 0.5) * height
        return (
            0.5 + (cosine * dx - sine * dy) / width,
            0.5 + (sine * dx + cosine * dy) / height,
        )

    return [_word_with_box(word, _transform_box(word, inverse)) for word in words]


def _unrotate_right_angle_words(words: Iterable[Word], angle: int) -> list[Word]:
    """Map boxes from an expanded right-angle rotation back to the input image."""
    transforms = {
        0: lambda x, y: (x, y),
        90: lambda x, y: (1 - y, x),
        180: lambda x, y: (1 - x, 1 - y),
        270: lambda x, y: (y, 1 - x),
    }
    transform = transforms[angle % 360]
    return [_word_with_box(word, _transform_box(word, transform)) for word in words]


def _box_iou(left: Word, right: Word) -> float:
    ix0, iy0 = max(left.x0, right.x0), max(left.y0, right.y0)
    ix1, iy1 = min(left.x1, right.x1), min(left.y1, right.y1)
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    left_area = max(0.0, left.x1 - left.x0) * max(0.0, left.y1 - left.y0)
    right_area = max(0.0, right.x1 - right.x0) * max(0.0, right.y1 - right.y0)
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def dedupe_words(words: Iterable[Word]) -> list[Word]:
    """Merge equivalent tokens emitted by overlapping/orientation OCR passes."""
    kept: list[Word] = []
    for word in sorted(words, key=lambda item: (-(item.confidence or 0.0), item.cy, item.x0)):
        token = word.text.strip().casefold()
        duplicate = next(
            (
                existing
                for existing in kept
                if existing.text.strip().casefold() == token
                and (
                    _box_iou(existing, word) >= 0.25
                    or math.hypot(existing.cx - word.cx, existing.cy - word.cy) <= 0.008
                )
            ),
            None,
        )
        if duplicate is None:
            kept.append(word)
    return sorted(kept, key=lambda item: (item.cy, item.x0))


def ocr_page_words(
    pdf_bytes: bytes, page_index: int, config: ExtractionConfig, *, psm: int | None = None
) -> list[Word]:
    """Clean, deskew, and OCR a full page while preserving original coordinates."""
    image = render_page(pdf_bytes, page_index, config.ocr_dpi)
    clean, angle = preprocess_image(image, config)
    words = _tesseract_words(clean, config, psm=psm or 3)
    return _unrotate_words(words, angle, clean.size)


def ocr_specification_words(
    pdf_bytes: bytes,
    page_index: int,
    config: ExtractionConfig,
    base_words: list[Word],
) -> list[Word]:
    """OCR left and right specification columns independently.

    The full-page pass remains authoritative for the patent/column header and
    centre gutter. Column passes replace prose only when they recover a credible
    amount of text, preventing a bad crop from degrading a usable full-page pass.
    """
    from .native import _detect_columns, column_boundary

    if _detect_columns(base_words) is None and column_boundary(base_words) is None:
        return base_words
    image = render_page(pdf_bytes, page_index, config.ocr_dpi)
    clean, angle = preprocess_image(image, config)
    crops = (
        (0.055, 0.075, 0.485, 0.955),
        (0.515, 0.075, 0.945, 0.955),
    )
    column_words: list[Word] = []
    for crop in crops:
        pixels = (
            round(crop[0] * clean.width),
            round(crop[1] * clean.height),
            round(crop[2] * clean.width),
            round(crop[3] * clean.height),
        )
        recognized = _tesseract_words(clean.crop(pixels), config, psm=config.ocr_text_psm)
        column_words.extend(_map_crop_words(recognized, crop))
    column_words = _unrotate_words(column_words, angle, clean.size)
    base_body = [
        word
        for word in base_words
        if word.cy > 0.075 and (word.cx < 0.455 or word.cx > 0.545)
    ]
    if len(column_words) < max(10, round(len(base_body) * 0.60)):
        return base_words
    structural = [
        word for word in base_words if word.cy <= 0.09 or 0.455 <= word.cx <= 0.545
    ]
    return dedupe_words([*structural, *column_words])


def ocr_drawing_words(
    pdf_bytes: bytes, page_index: int, config: ExtractionConfig
) -> list[Word]:
    """OCR a drawing sheet at high resolution and all configured orientations."""
    image = render_page(pdf_bytes, page_index, config.ocr_drawing_dpi)
    clean, deskew_angle = preprocess_image(image, config)
    recognized: list[Word] = []
    for rotation in config.ocr_drawing_rotations:
        rotated = clean.rotate(rotation, expand=True, fillcolor=255)
        oriented = _tesseract_words(rotated, config, psm=config.ocr_sparse_psm)
        recognized.extend(_unrotate_right_angle_words(oriented, rotation))
    return _unrotate_words(dedupe_words(recognized), deskew_angle, clean.size)
