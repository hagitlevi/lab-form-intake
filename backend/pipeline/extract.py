"""Step 4 of the pipeline: AI field extraction.

One multimodal Anthropic call per form group (all page images, no OCR text sent).
Returns a list of ExtractedForm - may be >1 if the AI finds several forms in the group.
Does NOT write to DB and does NOT run confidence scoring (that is step 5).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Optional

import anthropic
import cv2
import numpy as np
from dotenv import load_dotenv
from pydantic import ValidationError

from backend.pipeline.models import ExtractedForm, ExtractionResponse

load_dotenv()

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
AI_MAX_IMAGE_PX = int(os.environ.get("AI_MAX_IMAGE_PX", "2048"))
PROMPT_VERSION = "v6"
AI_BLUE_CROPS = os.environ.get("AI_BLUE_CROPS", "0").lower() in ("1", "true", "yes")
TOOL_NAME = "extract_forms"
CACHE_DIR = Path("samples/debug/ai_cache")
AI_CACHE = os.environ.get("AI_CACHE", "").lower() in ("1", "true", "yes")  # dev only: cache holds patient fields

log = logging.getLogger(__name__)

_prompt_cache: Optional[str] = None


def _load_prompt() -> str:
    global _prompt_cache
    if _prompt_cache is None:
        _prompt_cache = (Path(__file__).parent / "prompts" / "extract_v2.txt").read_text(
            encoding="utf-8"
        )
    return _prompt_cache


def _resize_to_jpeg(image: "np.ndarray | bytes", max_px: int = AI_MAX_IMAGE_PX) -> bytes:
    if isinstance(image, bytes):  # stored page JPEG: decoded one page at a time to keep memory low
        image = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    h, w = image.shape[:2]
    if max(h, w) > max_px:
        scale = max_px / max(h, w)
        image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


def blue_ink_crops(image: "np.ndarray | bytes", max_crops: int = 3, target_h: int = 360) -> list[bytes]:
    """Enlarged crops (JPEG) of the blue handwriting on a page, cut from the full-resolution
    colour image. The patient name/ID is small, faint blue ink that vanishes when the whole
    page is shrunk to AI_MAX_IMAGE_PX; black print and yellow highlighter are not blue."""
    if isinstance(image, bytes):  # stored page JPEG
        image = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    b, r = image[..., 0].astype(np.int16), image[..., 2].astype(np.int16)
    mask = ((b - r > 40) & (r < 180)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    h, w = mask.shape
    k = max(15, w // 40)  # merge letters and words of one handwritten block
    merged = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (k * 3, k)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(merged)
    blocks = []
    for i in range(1, n):
        x, y, bw, bh, _ = stats[i]
        ink = int(mask[y:y + bh, x:x + bw].sum())
        if ink >= 150:
            blocks.append((ink, x, y, bw, bh))
    out = []
    for _, x, y, bw, bh in sorted(blocks, reverse=True)[:max_crops]:
        pad = max(20, bh // 2)
        crop = image[max(0, y - pad):min(h, y + bh + pad), max(0, x - pad):min(w, x + bw + pad)]
        scale = min(4.0, max(1.0, target_h / crop.shape[0]))
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            out.append(buf.tobytes())
    return out


def _cache_key(images_bytes: list[bytes], model: str) -> str:
    h = hashlib.sha256()
    for b in images_bytes:
        h.update(b)
    h.update(PROMPT_VERSION.encode())
    h.update(model.encode())
    return h.hexdigest()


def _read_cache(key: str) -> Optional[dict]:
    if not AI_CACHE:
        return None
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _write_cache(key: str, data: dict) -> None:
    if not AI_CACHE:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _tool_def() -> dict:
    schema = ExtractionResponse.model_json_schema()
    return {
        "name": TOOL_NAME,
        "description": "Return all commitment forms found in the provided page images.",
        "input_schema": schema,
    }


def _image_block(b: bytes) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(b).decode()},
    }


def _build_user_content(images_bytes: list[bytes], crops: Optional[list[list[bytes]]] = None) -> list[dict]:
    content: list[dict] = []
    for i, b in enumerate(images_bytes):
        content.append({"type": "text", "text": f"Page {i} (0-indexed):"})
        content.append(_image_block(b))
        for j, c in enumerate((crops or [[]] * len(images_bytes))[i]):
            content.append({"type": "text", "text": f"Page {i}, enlarged crop {j + 1} of the blue handwriting on this page:"})
            content.append(_image_block(c))
    content.append(
        {"type": "text", "text": "Extract all commitment forms from these pages using the extract_forms tool."}
    )
    return content


def _serialize_content(blocks) -> list[dict]:
    """Convert SDK response content blocks to plain dicts for message history replay."""
    out = []
    for b in blocks:
        if b.type == "thinking":
            out.append({"type": "thinking", "thinking": b.thinking, "signature": b.signature})
        elif b.type == "redacted_thinking":
            out.append({"type": "redacted_thinking", "data": b.data})
        elif b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif b.type == "text":
            out.append({"type": "text", "text": b.text})
    return out


def _unwrap_forms(raw: dict) -> dict:
    """The model sometimes sends `forms` as a JSON string, often wrapping a whole
    {"forms": [...]} object. Unwrap it before validation; anything else is left as-is."""
    forms = raw.get("forms")
    if isinstance(forms, str):
        try:
            forms = json.loads(forms)
        except json.JSONDecodeError:
            return raw  # let validation fail and the retry handle it
    if isinstance(forms, dict) and "forms" in forms:
        forms = forms["forms"]
    return {**raw, "forms": forms}


def _find_tool_use(response) -> Optional[tuple[str, dict]]:
    """Return (tool_use_id, input_dict) for the extract_forms call, or None."""
    for b in response.content:
        if b.type == "tool_use" and b.name == TOOL_NAME:
            return b.id, _unwrap_forms(b.input)
    return None


def _call_once(
    client: anthropic.Anthropic,
    messages: list[dict],
    model: str,
) -> object:
    return client.messages.create(
        model=model,
        max_tokens=4096,
        system=_load_prompt(),
        messages=messages,
        tools=[_tool_def()],
        tool_choice={"type": "auto"},
    )


def _run_with_retry(
    client: anthropic.Anthropic,
    images_bytes: list[bytes],
    model: str,
    crops: Optional[list[list[bytes]]] = None,
) -> tuple[ExtractionResponse, object, float]:
    """Make up to 2 API calls; return (parsed_response, last_usage, total_seconds).

    On the first call: if Pydantic validation fails, retry once with the error fed back.
    After the second failure, raise so the caller can return an error result.
    """
    base_messages: list[dict] = [
        {"role": "user", "content": _build_user_content(images_bytes, crops)}
    ]

    t0 = time.monotonic()
    response1 = _call_once(client, base_messages, model)
    elapsed = time.monotonic() - t0

    found = _find_tool_use(response1)
    if found is None:
        raise ValueError("Model did not call extract_forms tool")

    tool_id, raw_input = found
    validation_err = None
    try:
        parsed = ExtractionResponse.model_validate(raw_input)
        return parsed, response1.usage, elapsed
    except ValidationError as e:
        validation_err = e
        log.warning("Validation error on first attempt, retrying: %s", e)

    # Retry: send the bad response + error back
    retry_messages = base_messages + [
        {"role": "assistant", "content": _serialize_content(response1.content)},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": (
                        f"Your response failed schema validation:\n{validation_err}\n"
                        "Please fix the issues and call extract_forms again with a valid response."
                    ),
                }
            ],
        },
    ]

    t0 = time.monotonic()
    response2 = _call_once(client, retry_messages, model)
    elapsed += time.monotonic() - t0

    found2 = _find_tool_use(response2)
    if found2 is None:
        raise ValueError("Model did not call extract_forms tool on retry")

    _, raw_input2 = found2
    parsed2 = ExtractionResponse.model_validate(raw_input2)
    return parsed2, response2.usage, elapsed


def _fill_year(forms: list[ExtractedForm], upload_date: date) -> None:
    for form in forms:
        if form.received_date and form.received_date.year is None:
            form.received_date.year = upload_date.year
            form.received_date.year_inferred = True


def _flag_probable_continuations(forms: list[ExtractedForm]) -> None:
    for form in forms:
        has_name = (form.first_name and form.first_name.value) or (
            form.last_name and form.last_name.value
        )
        has_id = form.national_id and form.national_id.value
        has_central = bool(form.central_numbers)
        has_commitment = bool(form.commitment_numbers)
        if not (has_name or has_id or has_central or has_commitment):
            form.probable_continuation = True


def _flag_split_by_ai(forms: list[ExtractedForm]) -> None:
    if len(forms) > 1:
        for form in forms:
            form.split_by_ai = True


def extract_form(
    images: list[np.ndarray],
    upload_date: Optional[date] = None,
    no_cache: bool = False,
    group_flagged: bool = False,
) -> list[ExtractedForm]:
    """Extract fields from a single form group (all its page images).

    images: preprocessed BGR color images, one per page in the group (step 2 output).
    Returns a list - usually one ExtractedForm, but may be several if the AI splits the group.
    Never raises; returns a single error-flagged form on failure.
    """
    model = ANTHROPIC_MODEL
    if upload_date is None:
        upload_date = date.today()

    images_bytes = [_resize_to_jpeg(img) for img in images]
    crops = [blue_ink_crops(img) for img in images] if AI_BLUE_CROPS else None
    key = _cache_key(images_bytes + [c for page in crops or [] for c in page], model)

    if not no_cache:
        cached = _read_cache(key)
        if cached is not None:
            log.info("cache hit: %s", key[:12])
            forms = ExtractionResponse.model_validate(cached).forms
            _fill_year(forms, upload_date)
            _flag_probable_continuations(forms)
            _flag_split_by_ai(forms)
            return forms

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    try:
        parsed, usage, elapsed = _run_with_retry(client, images_bytes, model, crops)
    except Exception as exc:
        log.error("AI extraction failed: %s", exc)
        error_form = ExtractedForm(
            page_indexes=list(range(len(images))),
            notes=f"extraction_error: {exc}",
            probable_continuation=False,
        )
        return [error_form]

    log.info(
        "model=%s input_tokens=%s output_tokens=%s elapsed=%.1fs",
        model,
        usage.input_tokens,
        usage.output_tokens,
        elapsed,
    )

    if not no_cache:
        _write_cache(
            key,
            {
                **parsed.model_dump(mode="json"),
                "_meta": {
                    "model": model,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "elapsed_s": round(elapsed, 2),
                },
            },
        )

    forms = parsed.forms
    _fill_year(forms, upload_date)
    _flag_probable_continuations(forms)
    _flag_split_by_ai(forms)
    return forms
