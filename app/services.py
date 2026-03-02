"""Embeddings, OpenSearch, file parsing, and LLM in one module."""
import json
import csv
import io
import re
from typing import Optional
from openai import OpenAI
from anthropic import Anthropic
from opensearchpy import OpenSearch
import openpyxl

from app.config import (
    OPENAI_API_KEY,
    ANTHROPIC_API_KEY,
    OPENSEARCH_URL,
    INDEX_NAME,
    EMBEDDING_DIM,
    RETRIEVAL_TOP_K,
    SIMILARITY_THRESHOLD,
    ALLOWED_LLM_MODELS,
    DEFAULT_LLM_MODEL,
    ANTHROPIC_MODEL_IDS,
    ALLOWED_FLAGS,
)


# --- Embeddings ---
def embed_texts(texts: list[str]) -> list[list[float]]:
    client = OpenAI(api_key=OPENAI_API_KEY)
    r = client.embeddings.create(model="text-embedding-3-small", input=texts)
    return [d.embedding for d in r.data]


# --- OpenSearch ---
def get_opensearch_client() -> OpenSearch:
    return OpenSearch(
        hosts=[OPENSEARCH_URL],
        use_ssl=False,
        verify_certs=False,
    )


def ensure_index(client: OpenSearch):
    if not client.indices.exists(index=INDEX_NAME):
        client.indices.create(
            index=INDEX_NAME,
            body={
                "settings": {"index": {"knn": True}},
                "mappings": {
                    "properties": {
                        "hist_question": {"type": "text"},
                        "hist_answer": {"type": "text"},
                        "hist_domain": {"type": "keyword"},
                        "source_id": {"type": "keyword"},
                        "embedding": {
                            "type": "knn_vector",
                            "dimension": EMBEDDING_DIM,
                            "method": {
                                "name": "hnsw",
                                "space_type": "cosinesimil",
                                "engine": "nmslib",
                                "parameters": {"ef_construction": 128, "m": 24},
                            },
                        },
                    }
                },
            },
        )


def index_docs(client: OpenSearch, docs: list[dict]):
    ensure_index(client)
    for i, doc in enumerate(docs):
        client.index(index=INDEX_NAME, body=doc, id=doc.get("source_id", str(i)))


def search_similar(client: OpenSearch, vector: list[float], k: int = RETRIEVAL_TOP_K) -> list[dict]:
    ensure_index(client)
    r = client.search(
        index=INDEX_NAME,
        body={
            "size": k,
            "query": {"knn": {"embedding": {"vector": vector, "k": k}}},
        },
    )
    out = []
    for hit in r.get("hits", {}).get("hits", []):
        s = hit.get("_score", 0)
        src = hit.get("_source", {})
        out.append({
            "hist_question": src.get("hist_question", ""),
            "hist_answer": src.get("hist_answer", ""),
            "hist_domain": src.get("hist_domain", ""),
            "score": s,
        })
    return out


# --- File parsing ---
def parse_history_csv(content: bytes) -> list[tuple[str, str, str]]:
    rows = []
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    for row in reader:
        q = (row.get("Question") or row.get("question", "")).strip()
        a = (row.get("Answer") or row.get("answer", "")).strip()
        d = (row.get("Domain") or row.get("domain", "")).strip()
        if q:
            rows.append((q, a or "", d or ""))
    return rows


def parse_history_json(content: bytes) -> list[tuple[str, str, str]]:
    data = json.loads(content)
    rows = []
    for item in (data if isinstance(data, list) else data.get("items", [])):
        q = str(item.get("question", item.get("Question", ""))).strip()
        a = str(item.get("answer", item.get("Answer", ""))).strip()
        d = str(item.get("domain", item.get("Domain", ""))).strip()
        if q:
            rows.append((q, a, d))
    return rows


def parse_history_file(content: bytes, filename: str) -> list[tuple[str, str, str]]:
    fn = filename.lower()
    if fn.endswith(".csv"):
        return parse_history_csv(content)
    if fn.endswith(".json"):
        return parse_history_json(content)
    raise ValueError(f"Unsupported format: {filename}")


def parse_excel_questions(content: bytes) -> list[str]:
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    ws = wb.active
    questions = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        q = str(row[0] if row else "").strip()
        if q:
            questions.append(q)
    wb.close()
    return questions


# --- LLM ---
SYSTEM_WITH_HISTORY = """You are a tender response assistant. You receive a question and historical answers from past tenders.
1. Generate an answer aligned with the historical context.
2. Do not claim certifications (ISO 27001, SOC 2, GDPR certified, etc.) unless history supports them.
3. Return valid JSON only: answer, domain_tag, historical_alignment_indicator, flags.
Domain tag: one of Architecture, Security, Infrastructure, AI, Compliance, Pricing.
Flags: use "unsupported_certification_claim" only if you claim a certification not in history; otherwise []."""

SYSTEM_WITHOUT_HISTORY = """You are a tender response assistant. You receive a tender question without prior responses to reference.
1. Generate a factual, conservative answer.
2. Do not claim certifications. If asked, recommend confirming with compliance team.
3. Return valid JSON only: answer, domain_tag, flags.
Domain tag: one of Architecture, Security, Infrastructure, AI, Compliance, Pricing.
Flags: use "needs_review" if the question asks about certifications/compliance; otherwise []."""


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response (handles markdown or extra text)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(m.group()) if m else {}


def call_llm(question: str, matches: Optional[list[dict]], model: str) -> dict:
    m = model if model in ALLOWED_LLM_MODELS else DEFAULT_LLM_MODEL

    if matches:
        sys_content = SYSTEM_WITH_HISTORY
        ctx = "\n---\n".join(
            f"Match {i+1} [score: {x.get('score', 0):.2f}]\nQ: {x.get('hist_question')}\nA: {x.get('hist_answer')}\nDomain: {x.get('hist_domain')}"
            for i, x in enumerate(matches)
        )
        user_content = f"Question: {question}\n\nHistorical context:\n{ctx}\n\nReturn only valid JSON: answer, domain_tag, historical_alignment_indicator, flags."
    else:
        sys_content = SYSTEM_WITHOUT_HISTORY
        user_content = f"Question: {question}\n\nReturn only valid JSON: answer, domain_tag, flags."

    if m.startswith("claude"):
        model_id = ANTHROPIC_MODEL_IDS.get(m, "claude-3-haiku-20240307")
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        r = client.messages.create(
            model=model_id,
            max_tokens=1024,
            system=sys_content,
            messages=[{"role": "user", "content": user_content}],
        )
        text = r.content[0].text
        return _extract_json(text)

    client = OpenAI(api_key=OPENAI_API_KEY)
    r = client.chat.completions.create(
        model=m,
        messages=[
            {"role": "system", "content": sys_content},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return json.loads(r.choices[0].message.content)


def filter_flags(flags: list) -> list[str]:
    return [f for f in flags if f in ALLOWED_FLAGS]


def compute_confidence(history_found: bool, best_score: float, flags: list[str]) -> str:
    bad = {"unsupported_certification_claim", "needs_review"}
    if any(f in bad for f in flags):
        return "Low"
    if not history_found or best_score < SIMILARITY_THRESHOLD:
        return "Low"
    if best_score >= 0.85:
        return "High"
    return "Medium"
