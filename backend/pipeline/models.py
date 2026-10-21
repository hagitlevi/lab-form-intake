"""Pydantic models for step 4 AI extraction output."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

Origin = Literal["printed", "handwritten", "stamp", "sticker", "barcode_label"]
DateSource = Literal["received_stamp", "handwritten", "visit_date", "other"]


class FieldValue(BaseModel):
    value: Optional[str] = None
    raw_text: Optional[str] = None
    origin: Optional[Origin] = None


class DateValue(BaseModel):
    value: Optional[str] = None
    raw_text: Optional[str] = None
    origin: Optional[Origin] = None
    day: Optional[int] = None
    month: Optional[int] = None
    year: Optional[int] = None
    date_source: Optional[DateSource] = None
    year_inferred: bool = False  # set by post-processing, never by the AI


class ExtractedForm(BaseModel):
    page_indexes: list[int]
    first_name: Optional[FieldValue] = None
    last_name: Optional[FieldValue] = None
    national_id: Optional[FieldValue] = None
    central_numbers: list[FieldValue] = []
    received_date: Optional[DateValue] = None
    sender: Optional[FieldValue] = None
    commitment_numbers: list[FieldValue] = []
    notes: Optional[str] = None
    # flags set by post-processing
    probable_continuation: bool = False
    split_by_ai: bool = False


class ExtractionResponse(BaseModel):
    forms: list[ExtractedForm]
