"""Step 2 of the pipeline: per-page processing (split, digital check, preprocess, OCR, barcodes).

Plain functions, no FastAPI/DB involved - these operate on files and images only.
"""

import os
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
import pymupdf
import pytesseract
import zxingcpp
from dotenv import load_dotenv

# Loaded directly (not via backend.config) so this module works standalone, without DATABASE_URL.
load_dotenv()

# Windows ships no tesseract on PATH by default; fall back to the common install location.
_tesseract_cmd = os.environ.get("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
if os.path.exists(_tesseract_cmd):
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd

# TESSDATA_PREFIX (if set, in .env or the shell) is read by the tesseract subprocess itself -
# only needed when heb/eng/osd traineddata aren't in tesseract's own tessdata dir, e.g. no
# admin rights to write into Program Files.

DPI = 300
BLANK_INK_RATIO = 0.001  # fraction of non-white pixels below which a page counts as blank
DIGITAL_IMAGE_COVERAGE = 0.8  # a single image covering more than this fraction means "scanned"
CENTRAL_PROXIMITY_PX = 400  # how close (px, at DPI) an OCR word must be to a barcode to confirm it


@dataclass
class Page:
    source: str
    page_number: int  # 1-based, within source file
    image: np.ndarray  # BGR, rasterized at DPI
    is_digital: bool
    pdf_text: str = ""
    render_seconds: float = 0.0  # time spent rasterizing this page in split_files()


@dataclass
class Word:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int
    rotation: int = 0  # 0 for the main OCR pass, 90/270 for a rotated crop around a barcode


@dataclass
class Barcode:
    value: str
    format: str
    rect: tuple  # (x, y, w, h), axis-aligned bounding box in image coordinates
    is_central: bool = False


def _page_image(page: pymupdf.Page, dpi: int = DPI) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def _is_blank(image: np.ndarray) -> bool:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    ink_ratio = float((gray < 250).sum()) / gray.size
    return ink_ratio < BLANK_INK_RATIO


def is_digital_page(page: pymupdf.Page) -> bool:
    """A page is digital if it has a text layer and no single image covers most of it."""
    if not page.get_text().strip():
        return False
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return True
    for xref, *_ in page.get_images(full=True):
        for rect in page.get_image_rects(xref):
            if (rect.width * rect.height) / page_area > DIGITAL_IMAGE_COVERAGE:
                return False
    return True


def split_files(paths: list[str]) -> tuple[list[Page], list[dict]]:
    """Open each file (PDF or image) in order; return (non-blank pages, skipped blank pages).

    Blank pages are reported, not dropped silently - skipped is a list of
    {"source": ..., "page_number": ...} for each one, in the same order they occur.
    """
    pages: list[Page] = []
    skipped: list[dict] = []
    for path in paths:
        doc = pymupdf.open(path)
        for i in range(doc.page_count):
            fitz_page = doc[i]
            t0 = time.monotonic()
            image = _page_image(fitz_page)
            render_seconds = time.monotonic() - t0
            if _is_blank(image):
                skipped.append({"source": path, "page_number": i + 1})
                continue
            pages.append(
                Page(
                    source=path,
                    page_number=i + 1,
                    image=image,
                    is_digital=is_digital_page(fitz_page),
                    pdf_text=fitz_page.get_text() if is_digital_page(fitz_page) else "",
                    render_seconds=render_seconds,
                )
            )
        # keep `doc` alive for as long as pages reference data copied out of it - not needed here
        # since _page_image() and get_text() already copied everything into plain Python objects.
    return pages, skipped


def _rotate_image(image: np.ndarray, angle: int) -> np.ndarray:
    if angle == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image


# a hung tesseract.exe is killed after this and pytesseract raises RuntimeError
TESSERACT_TIMEOUT_S = 60


def _osd_rotation(gray: np.ndarray) -> int:
    try:
        osd = pytesseract.image_to_osd(gray, output_type=pytesseract.Output.DICT, timeout=TESSERACT_TIMEOUT_S)
        return int(osd.get("rotate", 0)) % 360
    except pytesseract.TesseractError:
        return 0


def _deskew_angle(gray: np.ndarray) -> float:
    ink = np.column_stack(np.where(gray < 250))
    if len(ink) < 100:
        return 0.0
    angle = cv2.minAreaRect(ink.astype(np.float32))[-1]
    if angle < -45:
        angle += 90
    return angle


def _rotate_bound(image: np.ndarray, angle: float) -> np.ndarray:
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _perspective_correct(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
    edges = cv2.dilate(edges, None, iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image
    page_area = image.shape[0] * image.shape[1]
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) / page_area < 0.5:
        return image  # ponytail: no plausible page quad found, skip rather than guess
    approx = cv2.approxPolyDP(largest, 0.02 * cv2.arcLength(largest, True), True)
    if len(approx) != 4:
        return image
    pts = approx.reshape(4, 2).astype(np.float32)
    ordered = pts[np.argsort(pts[:, 1])]
    top = ordered[:2][np.argsort(ordered[:2, 0])]
    bottom = ordered[2:][np.argsort(ordered[2:, 0])]
    src = np.array([top[0], top[1], bottom[1], bottom[0]], dtype=np.float32)
    w = image.shape[1]
    h = image.shape[0]
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image, matrix, (w, h))


def apply_orientation(image: np.ndarray) -> tuple[int, np.ndarray]:
    """OSD-detected rotation (0/90/180/270) applied to image. Split out of preprocess()
    only so callers can time/report this stage separately - behavior is unchanged."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    angle = _osd_rotation(gray)
    color = _rotate_image(image, angle) if angle else image
    return angle, color


def _gray_luminance(color: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)


def _gray_red_channel(color: np.ndarray) -> np.ndarray:
    """Yellow highlighter is high-R/high-G/low-B, so it reads almost as white on the red
    channel alone - this erases the highlighter while leaving black text's contrast intact."""
    return color[:, :, 2]  # BGR -> red is index 2


def _gray_max_channel(color: np.ndarray) -> np.ndarray:
    return np.max(color, axis=2)


def _binarize_otsu(gray: np.ndarray) -> np.ndarray:
    _, out = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return out


def _binarize_adaptive(gray: np.ndarray, block_size: int = 25, c: int = 10) -> np.ndarray:
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block_size, c
    )


def apply_preprocessing(color: np.ndarray, to_gray=_gray_luminance, binarize=None) -> tuple[np.ndarray, np.ndarray]:
    """Deskew, perspective-correct, CLAHE. Split out of preprocess() for per-stage timing.

    to_gray/binarize default to exactly the original behavior (luminance grayscale, no
    binarization) - they only exist so backend.tools.ocr_experiment can compare alternatives
    without duplicating the deskew/perspective geometry. color is never touched by either -
    it stays the full-color image sent to the AI and stored for the review screen.
    """
    gray = to_gray(color)
    skew = _deskew_angle(gray)
    if abs(skew) > 0.1:
        color = _rotate_bound(color, skew)

    color = _perspective_correct(color)

    gray = to_gray(color)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    if binarize is not None:
        gray = binarize(gray)
    return gray, color


def preprocess(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (grayscale image for OCR, color image for the AI call)."""
    _, color = apply_orientation(image)
    return apply_preprocessing(color)


def run_ocr(gray_image: np.ndarray, lang: str = "heb+eng", config: str = "", scale: float = 1) -> list[Word]:
    """scale: upscale (e.g. 2, INTER_CUBIC) before OCR, for small/dense text - transient,
    only for this call. Returned bboxes are always rescaled back to gray_image's own
    coordinates, so callers never need to know OCR ran on a different resolution."""
    image = gray_image
    if scale != 1:
        image = cv2.resize(gray_image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    data = pytesseract.image_to_data(
        image, lang=lang, config=config, output_type=pytesseract.Output.DICT, timeout=TESSERACT_TIMEOUT_S
    )
    words = []
    for i, text in enumerate(data["text"]):
        if not text.strip():
            continue
        words.append(
            Word(
                text=text,
                conf=float(data["conf"][i]),
                left=round(data["left"][i] / scale),
                top=round(data["top"][i] / scale),
                width=round(data["width"][i] / scale),
                height=round(data["height"][i] / scale),
            )
        )
    return words


def _iou(a: Word, b: Word) -> float:
    ax0, ay0, ax1, ay1 = a.left, a.top, a.left + a.width, a.top + a.height
    bx0, by0, bx1, by1 = b.left, b.top, b.left + b.width, b.top + b.height
    iw = max(0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union > 0 else 0.0


def merge_ocr_passes(primary: list[Word], secondary: list[Word], iou_threshold: float = 0.3) -> list[Word]:
    """Union of two OCR passes over the same page (e.g. two different grayscale
    conversions) - where their word boxes overlap (IoU >= iou_threshold), keep only the
    higher-confidence one; non-overlapping words from both passes are kept as-is."""
    merged = list(primary)
    used_secondary: set[int] = set()
    for i, w in enumerate(merged):
        for j, w2 in enumerate(secondary):
            if j in used_secondary:
                continue
            if _iou(w, w2) >= iou_threshold:
                used_secondary.add(j)
                if w2.conf > w.conf:
                    merged[i] = w2
                break
    merged.extend(w2 for j, w2 in enumerate(secondary) if j not in used_secondary)
    return merged


def _decode(image: np.ndarray):
    return zxingcpp.read_barcodes(image)


def _bbox(result) -> tuple:
    pos = result.position
    xs = [pos.top_left.x, pos.top_right.x, pos.bottom_right.x, pos.bottom_left.x]
    ys = [pos.top_left.y, pos.top_right.y, pos.bottom_right.y, pos.bottom_left.y]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _unrotate_point(x: float, y: float, angle: int, orig_w: int, orig_h: int) -> tuple:
    if angle == 90:
        return y, orig_h - 1 - x
    if angle == 180:
        return orig_w - 1 - x, orig_h - 1 - y
    if angle == 270:
        return orig_w - 1 - y, x
    return x, y


def _word_near(word: Word, rect: tuple, radius: int) -> bool:
    bx, by, bw, bh = rect
    bcx, bcy = bx + bw / 2, by + bh / 2
    wcx, wcy = word.left + word.width / 2, word.top + word.height / 2
    return ((bcx - wcx) ** 2 + (bcy - wcy) ** 2) ** 0.5 <= radius


def barcode_crop_words(gray: np.ndarray, rect: tuple, margin: int = CENTRAL_PROXIMITY_PX) -> list[Word]:
    """OCR a 90deg/270deg-rotated crop around a barcode, to catch sideways labels like 'מרכזי'
    printed beside the sticker. Returns words mapped back into the full page's coordinates."""
    h, w = gray.shape[:2]
    bx, by, bw, bh = rect
    x0, y0 = max(0, int(bx - margin)), max(0, int(by - margin))
    x1, y1 = min(w, int(bx + bw + margin)), min(h, int(by + bh + margin))
    crop = gray[y0:y1, x0:x1]
    crop_h, crop_w = crop.shape[:2]

    words = []
    for angle in (90, 270):
        rotated = _rotate_image(crop, angle)
        for word in run_ocr(rotated):
            lx0, ly0 = _unrotate_point(word.left, word.top, angle, crop_w, crop_h)
            lx1, ly1 = _unrotate_point(word.left + word.width, word.top + word.height, angle, crop_w, crop_h)
            words.append(
                Word(
                    text=word.text,
                    conf=word.conf,
                    left=x0 + round(min(lx0, lx1)),
                    top=y0 + round(min(ly0, ly1)),
                    width=round(abs(lx1 - lx0)),
                    height=round(abs(ly1 - ly0)),
                    rotation=angle,
                )
            )
    return words


def _is_central(value: str, rect: tuple, ocr_words: list[Word], gray: np.ndarray) -> bool:
    if len(value) != 6 or not value.isdigit():
        return False
    nearby = [w for w in ocr_words if _word_near(w, rect, CENTRAL_PROXIMITY_PX)]
    nearby += barcode_crop_words(gray, rect)  # the label is often printed sideways
    return any(w.text.strip() == "מרכזי" or w.text.strip() == value for w in nearby)


def read_barcodes(image: np.ndarray, gray: np.ndarray, ocr_words: list[Word]) -> list[Barcode]:
    h, w = image.shape[:2]

    results = _decode(image)
    rects = [_bbox(r) for r in results] if results else []

    if not results:
        for scale in (1.5, 2.0):
            scaled = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            found = _decode(scaled)
            if found:
                results = found
                rects = [tuple(c / scale for c in _bbox(r)) for r in found]
                break

    if not results:
        for angle in (90, 180, 270):
            rotated = _rotate_image(image, angle)
            found = _decode(rotated)
            if found:
                results = found
                rects = []
                for r in found:
                    x, y, bw, bh = _bbox(r)
                    x0, y0 = _unrotate_point(x, y, angle, w, h)
                    x1, y1 = _unrotate_point(x + bw, y + bh, angle, w, h)
                    rects.append(
                        (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
                    )
                break

    barcodes = []
    for result, rect in zip(results, rects):
        barcodes.append(
            Barcode(
                value=result.text,
                format=str(result.format),
                rect=rect,
                is_central=_is_central(result.text, rect, ocr_words, gray),
            )
        )
    return barcodes


def _self_test() -> None:
    # _unrotate_point must invert _rotate_image for every corner, at every angle.
    w, h = 50, 80
    img = np.zeros((h, w, 3), dtype=np.uint8)
    corners = [(0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)]
    for angle in (90, 180, 270):
        rotated = _rotate_image(img, angle)
        rh, rw = rotated.shape[:2]
        for x, y in corners:
            rx, ry = {
                90: (h - 1 - y, x),
                180: (w - 1 - x, h - 1 - y),
                270: (y, w - 1 - x),
            }[angle]
            assert 0 <= rx < rw and 0 <= ry < rh
            ox, oy = _unrotate_point(rx, ry, angle, w, h)
            assert (round(ox), round(oy)) == (x, y), (angle, (x, y), (ox, oy))
    print("pipeline self-test OK")


if __name__ == "__main__":
    _self_test()
