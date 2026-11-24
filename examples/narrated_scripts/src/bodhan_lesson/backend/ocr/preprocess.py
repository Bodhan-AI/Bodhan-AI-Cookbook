"""Conservative page normalization and inverse coordinate mapping."""

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from math import ceil, cos, floor, isfinite, radians, sin
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from ..models import Affine, Box, ImageOperation, Page, PageTransform, PreprocessedPage, Size
from .inputs import png_bytes, render_page, rgb_pixels

IDENTITY: Affine = ((1, 0, 0), (0, 1, 0))
THUMBNAIL_SIZE = 640
INK_MASK = [255 if value < 175 else 0 for value in range(256)]
DESKEW_LIMIT = 4
DESKEW_GAIN = 1.06
EXIF_ORIENTATION = 274
QUARTER_TURNS = {90: Image.Transpose.ROTATE_90, -90: Image.Transpose.ROTATE_270}


@dataclass(frozen=True)
class PreprocessSettings:
    source_lang: str | None = None


def _exif_inverse(orientation: int, size: Size) -> Affine:
    width, height = size
    return {
        2: ((-1, 0, width), (0, 1, 0)),
        3: ((-1, 0, width), (0, -1, height)),
        4: ((1, 0, 0), (0, -1, height)),
        5: ((0, 1, 0), (1, 0, 0)),
        6: ((0, 1, 0), (-1, 0, height)),
        7: ((0, -1, width), (-1, 0, height)),
        8: ((0, -1, width), (1, 0, 0)),
    }.get(orientation, IDENTITY)


def _rotation_inverse(before: Size, after: Size, degrees: float) -> Affine:
    """Match Pillow's center-based, expanded rotation in image-edge coordinates."""
    x, y = (size / 2 for size in before)
    u, v = (size / 2 for size in after)
    cosine = round(cos(radians(degrees)), 15)
    sine = round(sin(radians(degrees)), 15)
    return (
        (cosine, -sine, x - cosine * u + sine * v),
        (sine, cosine, y - sine * u - cosine * v),
    )


def _compose(outer: Affine, inner: Affine) -> Affine:
    """Apply inner, then outer."""
    (a, b, c), (d, e, f) = outer
    (g, h, i), (j, k, l) = inner
    return (
        (a * g + b * j, a * h + b * k, a * i + b * l + c),
        (d * g + e * j, d * h + e * k, d * i + e * l + f),
    )


def _ink_mask(image: Image.Image) -> Image.Image:
    with image.convert("L") as gray:
        gray.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE), Image.Resampling.LANCZOS)
        return gray.point(INK_MASK)


def _row_counts(mask: Image.Image) -> list[int]:
    pixels = mask.tobytes()
    return [
        pixels.count(b"\xff", start, start + mask.width)
        for start in range(0, len(pixels), mask.width)
    ]


def _inner_halves(mask: Image.Image) -> tuple[Image.Image, Image.Image]:
    width, height = mask.size
    top, bottom = height // 10, height - height // 10
    left, middle = width // 10, width // 2
    return mask.crop((left, top, middle, bottom)), mask.crop((middle, top, width - left, bottom))


def _baseline_direction(mask: Image.Image) -> int:
    """Vote on Latin prose using the sharper edge between the baseline and ascenders."""
    counts = _row_counts(mask)
    threshold = max(2, mask.width // 65)
    start = 0
    upright = inverted = eligible = 0
    for end, count in enumerate([*counts, 0]):
        if count >= threshold:
            continue
        if 4 <= end - start <= mask.height * 0.065:
            band = [0, *counts[start:end], 0]
            middle = len(band) // 2
            rise = max(band[i + 1] - band[i] for i in range(middle))
            fall = max(band[-i - 2] - band[-i - 1] for i in range(middle))
            eligible += 1
            upright += fall >= max(1.4 * rise, rise + 2)
            inverted += rise >= max(1.4 * fall, fall + 2)
        start = end + 1
    if eligible >= 6:
        if upright / eligible >= 0.8:
            return 1
        if inverted / eligible >= 0.8:
            return -1
    return 0


def _right_angle(mask: Image.Image, source_lang: str | None) -> tuple[int, str]:
    # A landscape page may be intentional. Require repeated Latin baseline
    # evidence in both halves after the proposed turn to distinguish the two directions.
    if source_lang != "en" or mask.width <= mask.height:
        return 0, "Right-angle inference is limited to landscape English pages."
    with mask.transpose(Image.Transpose.ROTATE_90) as sideways:
        left, right = _inner_halves(sideways)
        with left, right:
            left_vote = _baseline_direction(left)
            right_vote = _baseline_direction(right)
    if left_vote and left_vote == right_vote:
        return 90 * left_vote, "Both halves support the same English text baseline direction."
    return 0, "No consistent evidence distinguishes a quarter turn from the current orientation."


def _projection_score(mask: Image.Image, angle: float) -> float:
    with mask.rotate(angle, expand=True, fillcolor=0) as rotated:
        # Limit the influence of solid lines and page boundaries on the projection.
        counts = [min(count, mask.width // 3) for count in _row_counts(rotated)]
    total = sum(counts)
    return sum(count * count for count in counts) / (total * total) if total else 0.0


def _deskew_estimate(mask: Image.Image) -> float | None:
    if min(mask.size) < 40 or mask.histogram()[255] < 100:
        return None
    baseline = _projection_score(mask, 0)
    scores = {
        step / 2: _projection_score(mask, step / 2)
        for step in range(-2 * DESKEW_LIMIT, 2 * DESKEW_LIMIT + 1)
        if step
    }
    best = max(scores, key=scores.__getitem__)
    if abs(best) == DESKEW_LIMIT or scores[best] <= baseline * DESKEW_GAIN:
        return None
    other_peak = max(score for angle, score in scores.items() if abs(angle - best) >= 1)
    if scores[best] <= other_peak * 1.02:
        return None
    return best


def _deskew_angle(mask: Image.Image) -> float:
    left, right = _inner_halves(mask)
    with left, right:
        left_angle = _deskew_estimate(left)
        right_angle = _deskew_estimate(right)
    if left_angle is None or right_angle is None or left_angle * right_angle <= 0:
        return 0
    if abs(left_angle - right_angle) > 0.5:
        return 0
    return (left_angle + right_angle) / 2


def normalize_page(
    image: Image.Image, settings: PreprocessSettings,
) -> tuple[Image.Image, PageTransform]:
    """Apply conservative geometric corrections and return an inverse affine."""
    orientation = image.getexif().get(EXIF_ORIENTATION)
    with ImageOps.exif_transpose(image) as oriented:
        normalized = rgb_pixels(oriented)
    exif_applied = orientation in range(2, 9)
    inverse = _exif_inverse(orientation, image.size) if exif_applied else IDENTITY
    operations = [ImageOperation(
        "exif", exif_applied,
        "Applied EXIF orientation." if exif_applied else "EXIF specifies no supported correction.",
        exif_orientation=orientation if isinstance(orientation, int) else None,
    )]

    with _ink_mask(normalized) as mask:
        right_angle, rotation_reason = _right_angle(mask, settings.source_lang)
        if right_angle:
            with mask.transpose(QUARTER_TURNS[right_angle]) as turned_mask:
                deskew = _deskew_angle(turned_mask)
        else:
            deskew = _deskew_angle(mask)

    operations.append(ImageOperation("right_angle", bool(right_angle), rotation_reason, right_angle))
    if right_angle:
        with normalized as before:
            normalized = before.transpose(QUARTER_TURNS[right_angle])
            inverse = _compose(inverse, _rotation_inverse(before.size, normalized.size, right_angle))

    operations.append(ImageOperation(
        "deskew", bool(deskew),
        "Both page halves support a small angle." if deskew else "No consistent small skew detected.",
        deskew,
    ))
    if deskew:
        with normalized as before:
            normalized = before.rotate(
                deskew, Image.Resampling.BICUBIC, expand=True, fillcolor="white"
            )
            inverse = _compose(inverse, _rotation_inverse(before.size, normalized.size, deskew))

    operations.append(ImageOperation("crop", False, "Preserved complete page margins."))
    return normalized, PageTransform(image.size, normalized.size, inverse, tuple(operations))


def map_box_to_original(box: Box, transform: PageTransform) -> tuple[int, int, int, int]:
    """Map all four corners, round outwards, and clip to the original raster extent."""
    x0, y0, x1, y1 = box
    if not all(map(isfinite, box)) or x0 > x1 or y0 > y1:
        raise ValueError("Expected a finite box in (left, top, right, bottom) order.")
    (a, b, c), (d, e, f) = transform.normalized_to_original
    corners = [(a * x + b * y + c, d * x + e * y + f) for x in (x0, x1) for y in (y0, y1)]
    xs, ys = zip(*corners)
    width, height = transform.original_size
    return (
        min(width, max(0, floor(min(xs)))), min(height, max(0, floor(min(ys)))),
        min(width, max(0, ceil(max(xs)))), min(height, max(0, ceil(max(ys)))),
    )


def _preprocess_page(page: Page, out: Path, settings: PreprocessSettings) -> PreprocessedPage:
    original_path = Path("work/originals") / f"{page.page_id}.png"
    image_path = Path("work/images") / f"{page.page_id}.png"
    try:
        with render_page(page) as image:
            original_bytes = png_bytes(image)
            normalized, transform = normalize_page(image, settings)
            with normalized:
                normalized_bytes = png_bytes(normalized)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, pdfium.PdfiumError) as error:
        (out / original_path).unlink(missing_ok=True)
        (out / image_path).unlink(missing_ok=True)
        reason = f"Could not decode or normalize the page ({type(error).__name__})."
        return PreprocessedPage(page.page_id, "failed", reason=reason)

    (out / original_path).write_bytes(original_bytes)
    (out / image_path).write_bytes(normalized_bytes)
    return PreprocessedPage(
        page_id=page.page_id, status="ok", original_path=original_path.as_posix(),
        normalized_path=image_path.as_posix(), input_sha256=sha256(original_bytes).hexdigest(),
        output_sha256=sha256(normalized_bytes).hexdigest(), transform=transform,
    )


def preprocess_pages(
    pages: tuple[Page, ...], out: Path, settings: PreprocessSettings,
) -> tuple[PreprocessedPage, ...]:
    """Write one transform record for every input page, including decode failures."""
    images = out / "work/images"
    images.mkdir(parents=True, exist_ok=True)
    (out / "work/originals").mkdir(parents=True, exist_ok=True)
    results = []
    for page in pages:
        record = _preprocess_page(page, out, settings)
        artifact = images / f"{page.page_id}.transform.json"
        artifact.write_text(json.dumps(asdict(record), indent=2) + "\n", encoding="utf-8")
        results.append(record)
    return tuple(results)
