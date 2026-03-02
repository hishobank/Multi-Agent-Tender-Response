from pydantic import BaseModel
from typing import Optional


class IngestResponse(BaseModel):
    indexed: int


class QuestionResult(BaseModel):
    question: str
    answer: str
    domain_tag: str
    confidence_level: str
    historical_alignment_indicator: bool
    flags: list[str]


class Summary(BaseModel):
    total_questions_processed: int
    flagged_count: int
    flagged_items: list[dict]
    completion_status: str


class TenderProcessResponse(BaseModel):
    results: list[QuestionResult]
    summary: Summary
