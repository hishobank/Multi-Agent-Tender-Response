"""
Multi-Agent Tender Response API
Endpoints: POST /history/ingest, POST /tender/process
"""
from fastapi import FastAPI, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    SIMILARITY_THRESHOLD,
    RETRIEVAL_TOP_K,
    ALLOWED_FLAGS,
    DEFAULT_LLM_MODEL,
)
from app.schemas import IngestResponse, TenderProcessResponse, QuestionResult, Summary
from app.services import (
    parse_history_file,
    parse_excel_questions,
    embed_texts,
    get_opensearch_client,
    ensure_index,
    index_docs,
    search_similar,
    call_llm,
    filter_flags,
    compute_confidence,
)

app = FastAPI(title="Multi-Agent Tender Response")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])


@app.post("/history/ingest", response_model=IngestResponse)
async def history_ingest(file: UploadFile):
    """Upload CSV/JSON with Question, Answer, Domain. Index into OpenSearch."""
    content = await file.read()
    try:
        rows = parse_history_file(content, file.filename or "")
    except Exception as e:
        raise HTTPException(400, f"Parse error: {e}")

    if not rows:
        return IngestResponse(indexed=0)

    questions = [r[0] for r in rows]
    vectors = embed_texts(questions)

    client = get_opensearch_client()
    ensure_index(client)

    docs = []
    for i, (q, a, d) in enumerate(rows):
        docs.append({
            "hist_question": q,
            "hist_answer": a,
            "hist_domain": d,
            "embedding": vectors[i],
            "source_id": f"{file.filename}_{i}",
        })

    index_docs(client, docs)
    return IngestResponse(indexed=len(docs))


def process_one_question(
    question: str,
    client,
    model: str,
) -> dict:
    """Process a single question: embed, retrieve, LLM, post-process."""
    try:
        vectors = embed_texts([question])
        matches = search_similar(client, vectors[0], k=RETRIEVAL_TOP_K)
        best_score = matches[0]["score"] if matches else 0.0
        history_found = best_score >= SIMILARITY_THRESHOLD
        matches_to_use = matches if history_found else None

        llm_out = call_llm(question, matches_to_use, model)
        answer = llm_out.get("answer", "")
        domain_tag = llm_out.get("domain_tag", "Compliance")
        flags = filter_flags(llm_out.get("flags", []))
        historical_alignment = llm_out.get("historical_alignment_indicator", False)

        if not history_found:
            historical_alignment = False
            if "needs_review" not in flags:
                flags = flags + ["needs_review"]

        confidence_level = compute_confidence(history_found, best_score, flags)

        return {
            "question": question,
            "answer": answer,
            "domain_tag": domain_tag,
            "confidence_level": confidence_level,
            "historical_alignment_indicator": historical_alignment,
            "flags": flags,
        }
    except Exception:
        return {
            "question": question,
            "answer": "Processing failed for this row",
            "domain_tag": "Compliance",
            "confidence_level": "Low",
            "historical_alignment_indicator": False,
            "flags": ["processing_error"],
        }


@app.post("/tender/process", response_model=TenderProcessResponse)
async def tender_process(
    file: UploadFile,
    model: str = Form(default=DEFAULT_LLM_MODEL),
):
    """Upload Excel with questions. Returns results + summary."""
    content = await file.read()
    try:
        questions = parse_excel_questions(content)
    except Exception as e:
        raise HTTPException(400, f"Excel parse error: {e}")

    if not questions:
        return TenderProcessResponse(
            results=[],
            summary=Summary(
                total_questions_processed=0,
                flagged_count=0,
                flagged_items=[],
                completion_status="completed",
            ),
        )

    client = get_opensearch_client()
    results = []
    for q in questions:
        res = process_one_question(q, client, model)
        results.append(QuestionResult(**res))

    flagged = [r for r in results if r.flags]
    status = "completed_with_flags" if flagged else "completed"

    return TenderProcessResponse(
        results=results,
        summary=Summary(
            total_questions_processed=len(results),
            flagged_count=len(flagged),
            flagged_items=[{"question": r.question, "flags": r.flags} for r in flagged],
            completion_status=status,
        ),
    )


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
