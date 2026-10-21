"""OCR rendering, cleanup, deskewing, and layout-specific recognition.

Specification scans use a cleaned full-page pass for structural tokens followed
by independent left/right column passes. Drawing sheets use higher-resolution
sparse-text passes at the configured orientations; every box is transformed
back to the original PDF coordinate system before downstream figure detection.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

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


def words_from_tsv(data: dict, width: float, height: float, *, min_confidence: float) -> list[Word]:
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
        sum((current - previous) ** 2 for previous, current in zip(rows, rows[1:], strict=False))
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


def _tesseract_words(
    image,
    config: ExtractionConfig,
    *,
    psm: int,
    thresholding_method: int = 2,
    char_whitelist: str | None = None,
) -> list[Word]:
    import pytesseract
    from pytesseract import Output

    tess_config = (
        f"--oem 1 --psm {psm} "
        f"-c thresholding_method={thresholding_method} "
        "-c preserve_interword_spaces=1"
    )
    if char_whitelist:
        tess_config += f" -c tessedit_char_whitelist={char_whitelist}"
    data = pytesseract.image_to_data(
        image,
        lang="+".join(config.ocr_languages),
        config=tess_config,
        output_type=Output.DICT,
    )
    return words_from_tsv(data, image.width, image.height, min_confidence=config.ocr_min_confidence)


@dataclass(frozen=True, slots=True)
class OcrQuality:
    """Small, deterministic signal used to decide whether another pass is worthwhile."""

    word_count: int
    mean_confidence: float
    low_confidence_fraction: float
    useful_characters: int


@dataclass(frozen=True, slots=True)
class PreparedOcrPage:
    """One rendered and cleaned page shared by every OCR profile for that page."""

    text_image: Any
    drawing_image: Any
    deskew_angle: float


def prepare_ocr_page(
    pdf_bytes: bytes,
    page_index: int,
    config: ExtractionConfig,
    *,
    master_dpi: int | None = None,
) -> PreparedOcrPage:
    """Render and preprocess once, retaining text and drawing resolutions.

    The worker prepares OCR-routed pages at the larger configured resolution and
    derives the specification image by downsampling. Standalone OCR helpers can
    request their own resolution so their existing behavior remains unchanged.
    """
    from PIL import Image

    dpi = master_dpi or max(config.ocr_dpi, config.ocr_drawing_dpi)
    rendered = render_page(pdf_bytes, page_index, dpi)
    clean, angle = preprocess_image(rendered, config)
    if dpi == config.ocr_dpi:
        text_image = clean
    else:
        ratio = config.ocr_dpi / dpi
        text_image = clean.resize(
            (max(1, round(clean.width * ratio)), max(1, round(clean.height * ratio))),
            Image.Resampling.LANCZOS,
        )
    return PreparedOcrPage(
        text_image=text_image,
        drawing_image=clean,
        deskew_angle=angle,
    )


def ocr_quality(words: Iterable[Word], *, low_confidence_cutoff: float = 50.0) -> OcrQuality:
    """Summarize an OCR candidate without interpreting its document layout."""
    materialized = list(words)
    confidences = [
        max(0.0, min(100.0, float(word.confidence)))
        for word in materialized
        if word.confidence is not None
    ]
    mean = sum(confidences) / len(confidences) if confidences else 0.0
    low = (
        sum(confidence < low_confidence_cutoff for confidence in confidences) / len(confidences)
        if confidences
        else 1.0
    )
    return OcrQuality(
        word_count=len(materialized),
        mean_confidence=mean,
        low_confidence_fraction=low,
        useful_characters=sum(
            sum(character.isalnum() for character in word.text) for word in materialized
        ),
    )


def _candidate_score(words: list[Word], config: ExtractionConfig) -> float:
    quality = ocr_quality(words, low_confidence_cutoff=config.ocr_retry_low_confidence_cutoff)
    # Confidence dominates, while a logarithmic coverage bonus prevents a tiny,
    # pristine fragment from defeating a complete and nearly-as-clean page.
    coverage_bonus = 4.0 * math.log1p(quality.useful_characters)
    noise_penalty = 25.0 * quality.low_confidence_fraction
    return quality.mean_confidence + coverage_bonus - noise_penalty


def _needs_retry(words: list[Word], config: ExtractionConfig, *, minimum_words: int) -> bool:
    quality = ocr_quality(words, low_confidence_cutoff=config.ocr_retry_low_confidence_cutoff)
    return (
        quality.word_count < minimum_words
        or quality.mean_confidence < config.ocr_retry_mean_confidence
        or quality.low_confidence_fraction > config.ocr_retry_low_confidence_fraction
    )


def _binarize_image(image):
    """Create a global-Otsu fallback for uneven results from Tesseract thresholding."""
    from PIL import ImageOps

    gray = ImageOps.grayscale(image)
    threshold = _otsu_threshold(gray)
    return gray.point(lambda value: 255 if value > threshold else 0)


def _adaptive_tesseract_words(
    image,
    config: ExtractionConfig,
    *,
    primary_psm: int,
    retry_psm: int,
    minimum_words: int,
) -> list[Word]:
    """Run extra OCR profiles only when the current best result is weak."""
    candidates = [_tesseract_words(image, config, psm=primary_psm, thresholding_method=2)]
    if not _needs_retry(candidates[0], config, minimum_words=minimum_words):
        return candidates[0]

    candidates.append(_tesseract_words(image, config, psm=retry_psm, thresholding_method=1))
    best = max(candidates, key=lambda words: _candidate_score(words, config))
    if _needs_retry(best, config, minimum_words=minimum_words):
        candidates.append(
            _tesseract_words(
                _binarize_image(image),
                config,
                psm=retry_psm,
                thresholding_method=0,
            )
        )
    return max(candidates, key=lambda words: _candidate_score(words, config))


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
        is_bold=word.is_bold,
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


def _map_crop_words(words: Iterable[Word], crop: tuple[float, float, float, float]) -> list[Word]:
    x0, y0, x1, y1 = crop
    width, height = x1 - x0, y1 - y0
    return [
        _word_with_box(
            word,
            _transform_box(word, lambda x, y: (x0 + x * width, y0 + y * height)),
        )
        for word in words
    ]


def _unrotate_words(words: Iterable[Word], angle: float, size: tuple[int, int]) -> list[Word]:
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


def _box_containment(left: Word, right: Word) -> float:
    """Return how much of the smaller word box is covered by the larger one."""
    ix0, iy0 = max(left.x0, right.x0), max(left.y0, right.y0)
    ix1, iy1 = min(left.x1, right.x1), min(left.y1, right.y1)
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    left_area = max(0.0, left.x1 - left.x0) * max(0.0, left.y1 - left.y0)
    right_area = max(0.0, right.x1 - right.x0) * max(0.0, right.y1 - right.y0)
    smaller_area = min(left_area, right_area)
    return intersection / smaller_area if smaller_area else 0.0


def _word_completeness(word: Word) -> tuple[int, float, float]:
    """Rank competing OCR tokens by content, coverage, then confidence."""
    useful_characters = sum(character.isalnum() for character in word.text)
    return (
        useful_characters,
        max(0.0, word.x1 - word.x0),
        word.confidence or 0.0,
    )


def dedupe_words(words: Iterable[Word]) -> list[Word]:
    """Merge equivalent or nested tokens from overlapping OCR passes.

    Separate full-page and column passes occasionally recognize the same word as
    both a complete token and a suffix (for example ``interface``/``terface``).
    Their boxes substantially overlap and share a right edge, so keep the more
    complete reading instead of emitting both fragments into the line.
    """
    kept: list[Word] = []
    for word in sorted(words, key=lambda item: (-(item.confidence or 0.0), item.cy, item.x0)):
        token = word.text.strip().casefold()
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(kept)
                if (
                    existing.text.strip().casefold() == token
                    and (
                        _box_iou(existing, word) >= 0.25
                        or math.hypot(existing.cx - word.cx, existing.cy - word.cy)
                        <= 0.008
                    )
                )
                or (
                    abs(existing.cy - word.cy) <= 0.008
                    and _box_containment(existing, word) >= 0.82
                    and abs(existing.x1 - word.x1) <= 0.008
                )
            ),
            None,
        )
        if duplicate_index is None:
            kept.append(word)
        elif _word_completeness(word) > _word_completeness(kept[duplicate_index]):
            kept[duplicate_index] = word
    return sorted(kept, key=lambda item: (item.cy, item.x0))


def fuse_drawing_words(words: Iterable[Word]) -> list[Word]:
    """Fuse overlapping drawing OCR results and reward cross-pass agreement.

    Figure and callout profiles intentionally overlap. Repeated recognition at
    the same coordinates is useful evidence, so the retained word receives a
    small confidence boost instead of merely discarding its duplicates.
    """
    groups: list[list[Word]] = []
    for word in words:
        token = word.text.strip().casefold()
        group = next(
            (
                candidate
                for candidate in groups
                if candidate[0].text.strip().casefold() == token
                and (
                    _box_iou(candidate[0], word) >= 0.25
                    or math.hypot(candidate[0].cx - word.cx, candidate[0].cy - word.cy) <= 0.008
                )
            ),
            None,
        )
        if group is None:
            groups.append([word])
        else:
            group.append(word)

    fused: list[Word] = []
    for group in groups:
        best = max(group, key=lambda item: item.confidence or 0.0)
        if best.confidence is not None and len(group) > 1:
            boost = min(8.0, 2.0 * (len(group) - 1))
            best = replace(best, confidence=min(100.0, best.confidence + boost))
        fused.append(best)
    return sorted(fused, key=lambda item: (item.cy, item.x0))


def ocr_page_words(
    pdf_bytes: bytes,
    page_index: int,
    config: ExtractionConfig,
    *,
    psm: int | None = None,
    prepared: PreparedOcrPage | None = None,
) -> list[Word]:
    """Clean, deskew, and OCR a full page while preserving original coordinates."""
    prepared = prepared or prepare_ocr_page(
        pdf_bytes, page_index, config, master_dpi=config.ocr_dpi
    )
    clean = prepared.text_image
    primary_psm = psm or 3
    retry_psm = psm or config.ocr_text_retry_psm
    words = _adaptive_tesseract_words(
        clean,
        config,
        primary_psm=primary_psm,
        retry_psm=retry_psm,
        minimum_words=12,
    )
    return _unrotate_words(words, prepared.deskew_angle, clean.size)


def ocr_specification_words(
    pdf_bytes: bytes,
    page_index: int,
    config: ExtractionConfig,
    base_words: list[Word],
    *,
    prepared: PreparedOcrPage | None = None,
) -> list[Word]:
    """OCR left and right specification columns independently.

    The full-page pass remains authoritative for the patent/column header and
    centre gutter. Column passes replace prose only when they recover a credible
    amount of text, preventing a bad crop from degrading a usable full-page pass.
    """
    from .native import _detect_columns, column_boundary

    if _detect_columns(base_words) is None and column_boundary(base_words) is None:
        return base_words
    prepared = prepared or prepare_ocr_page(
        pdf_bytes, page_index, config, master_dpi=config.ocr_dpi
    )
    clean = prepared.text_image
    crops = (
        (0.055, 0.102, 0.500, 0.955),
        # The right column on scanned grants commonly begins around x=.508.
        # Starting at .515 clipped the first character, producing suffixes such
        # as ``onfigurations`` and overlapping ``interface``/``terface`` reads.
        # Include a narrow gutter margin; reconstruction already identifies and
        # removes printed centre line numbers geometrically.
        (0.500, 0.102, 0.945, 0.955),
    )
    column_words: list[Word] = []
    for crop in crops:
        pixels = (
            round(crop[0] * clean.width),
            round(crop[1] * clean.height),
            round(crop[2] * clean.width),
            round(crop[3] * clean.height),
        )
        recognized = _adaptive_tesseract_words(
            clean.crop(pixels),
            config,
            primary_psm=config.ocr_text_psm,
            retry_psm=config.ocr_text_retry_psm,
            minimum_words=8,
        )
        column_words.extend(_map_crop_words(recognized, crop))
    column_words = _unrotate_words(column_words, prepared.deskew_angle, clean.size)
    base_body = [
        word for word in base_words if word.cy > 0.075 and (word.cx < 0.455 or word.cx > 0.545)
    ]
    if len(column_words) < max(10, round(len(base_body) * 0.60)):
        return base_words
    structural = [word for word in base_words if word.cy <= 0.10 or 0.455 <= word.cx <= 0.545]
    return dedupe_words([*structural, *column_words])


def ocr_drawing_words(
    pdf_bytes: bytes,
    page_index: int,
    config: ExtractionConfig,
    *,
    prepared: PreparedOcrPage | None = None,
) -> list[Word]:
    """OCR drawing labels with independent figure and callout profiles.

    General prose OCR is a poor fit for sparse patent drawings. Each configured
    orientation therefore gets two constrained passes: an alphanumeric figure
    pass (``FIG. 14B``) and a numeric reference-callout pass (``104A``). Their
    source-coordinate boxes are fused only after rotation is undone.
    """
    prepared = prepared or prepare_ocr_page(
        pdf_bytes, page_index, config, master_dpi=config.ocr_drawing_dpi
    )
    clean = prepared.drawing_image

    figure_chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz."
    callout_chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def label_image(image, dpi: int):
        """Downsample bilevel drawings before caption recognition.

        High-resolution rendering preserves fine drawing geometry, but the
        source TIFF's dither becomes artificial strokes at 400 dpi.  Caption
        OCR is markedly more reliable after a high-quality reduction while
        normalized word coordinates remain unchanged.
        """
        from PIL import Image

        ratio = min(1.0, dpi / max(1, config.ocr_drawing_dpi))
        if ratio >= 0.999:
            return image
        return image.resize(
            (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
            Image.Resampling.LANCZOS,
        )

    def canonical_label_words(words: list[Word]) -> list[Word]:
        """Turn independently parsed captions into conflict-free OCR tokens."""
        from .callouts import detect_figure_occurrences

        return [
            Word(
                text=f"FIG.{occurrence.figure_id}",
                x0=occurrence.box[0],
                y0=occurrence.box[1],
                x1=occurrence.box[2],
                y1=occurrence.box[3],
                confidence=occurrence.confidence,
            )
            for occurrence in detect_figure_occurrences(words, page_index)
        ]

    def credible_figure(words: list[Word]) -> bool:
        from .callouts import detect_figure_occurrences

        return any(
            occurrence.confidence is None or occurrence.confidence >= 45.0
            for occurrence in detect_figure_occurrences(words, page_index)
        )

    def tiled_figure_pass(rotation: int) -> list[Word]:
        """Recover horizontal captions from noisy, diagram-heavy sheets.

        Full-page sparse segmentation can lose a caption amid connector lines and
        halftoning. Four overlapping, binarized horizontal bands isolate titles
        such as ``Figure 14b`` while retaining enough width for long captions.
        """
        rotated = clean.rotate(rotation, expand=True, fillcolor=255)
        # Tiles exist to simplify segmentation, not to preserve tiny callout
        # geometry. Run them at the smallest caption scale to avoid feeding
        # millions of dither pixels through each fallback invocation.
        tile_dpi = min(config.ocr_drawing_label_dpis or (config.ocr_drawing_dpi,))
        rotated = label_image(rotated, tile_dpi)
        tiled: list[Word] = []
        for y0 in (0.08, 0.30, 0.52, 0.70):
            crop = (0.04, y0, 0.96, min(0.97, y0 + 0.28))
            pixels = (
                round(crop[0] * rotated.width),
                round(crop[1] * rotated.height),
                round(crop[2] * rotated.width),
                round(crop[3] * rotated.height),
            )
            recognized = _tesseract_words(
                _binarize_image(rotated.crop(pixels)),
                config,
                psm=config.ocr_drawing_figure_psm,
                thresholding_method=0,
                char_whitelist=figure_chars,
            )
            tiled.extend(_map_crop_words(recognized, crop))
        return _unrotate_right_angle_words(tiled, rotation)

    def orientation_pass(rotation: int) -> list[Word]:
        rotated = clean.rotate(rotation, expand=True, fillcolor=255)
        figure_labels: list[Word] = []
        raw_figure_candidates: list[list[Word]] = []
        for dpi in config.ocr_drawing_label_dpis or (config.ocr_drawing_dpi,):
            recognized = _tesseract_words(
                label_image(rotated, dpi),
                config,
                psm=config.ocr_drawing_figure_psm,
                thresholding_method=2,
                char_whitelist=figure_chars,
            )
            restored = _unrotate_right_angle_words(recognized, rotation)
            raw_figure_candidates.append(restored)
            figure_labels.extend(canonical_label_words(restored))
        figures = (
            fuse_drawing_words(figure_labels)
            if figure_labels
            else max(
                raw_figure_candidates,
                key=lambda words: _candidate_score(words, config),
                default=[],
            )
        )
        callouts = (
            _tesseract_words(
                rotated,
                config,
                psm=config.ocr_drawing_callout_psm,
                thresholding_method=2,
                char_whitelist=callout_chars,
            )
            if config.callout_detection_enabled
            else []
        )
        restored_callouts = _unrotate_right_angle_words(callouts, rotation)
        return fuse_drawing_words([*figures, *restored_callouts])

    rotations = config.ocr_drawing_rotations or (0,)
    normal_candidates: list[list[Word]] = []
    for rotation in rotations:
        candidate = orientation_pass(rotation)
        normal_candidates.append(candidate)
        if credible_figure(candidate):
            return _unrotate_words(candidate, prepared.deskew_angle, clean.size)

    # Local tiles require several extra Tesseract calls per orientation, so use
    # them only after both full-page orientations fail.
    fallback_candidates: list[list[Word]] = []
    for index, rotation in enumerate(rotations):
        tiled = fuse_drawing_words(tiled_figure_pass(rotation))
        combined = fuse_drawing_words([*normal_candidates[index], *tiled])
        fallback_candidates.append(combined)
        if credible_figure(combined):
            return _unrotate_words(combined, prepared.deskew_angle, clean.size)

    candidates = [*normal_candidates, *fallback_candidates]

    def figure_candidate_score(words: list[Word]) -> tuple[int, float, float]:
        from .callouts import detect_figure_occurrences

        figures = detect_figure_occurrences(words, page_index)
        figure_confidence = max(
            (figure.confidence or 0.0 for figure in figures), default=0.0
        )
        # A structurally valid FIG/Figure label is more relevant than a large
        # amount of high-confidence diagram noise. Specification evidence later
        # decides whether a low-confidence identifier should survive.
        return (bool(figures), figure_confidence, _candidate_score(words, config))

    recognized = max(candidates, key=figure_candidate_score, default=[])
    return _unrotate_words(recognized, prepared.deskew_angle, clean.size)
