# Multi-Agent Tender Response — Architecture

This document explains what happens at each step in the backend. No setup instructions—only the processing flow, what each agent does, and how confidence is calculated.

---

## 1. What Are the Agents?

In this system, an "agent" is a logical step that does one job. There are 5 agents in the tender process flow:

| Agent | What it does | LLM? |
|-------|--------------|------|
| Parser | Reads Excel/CSV, extracts questions or Q/A rows | No |
| Embedding | Converts text to vectors via OpenAI | No (API only) |
| Retrieval | Finds similar historical Q/A in OpenSearch | No |
| Generation | Calls LLM to write answer, domain_tag, flags | Yes |
| Post-Process | Filters flags, computes confidence, builds result | No |

They run in order. Failure in one question does not stop the rest.

---

## 2. Backend Architecture Diagram

### Ingest Flow (POST /history/ingest)

```
CSV upload
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ AGENT 1: Parser                                                  │
│ - Reads CSV: Question, Answer, Domain                           │
│ - Skips rows where Question is empty                            │
│ - Output: list of (question, answer, domain)                     │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ AGENT 2: Embedding                                               │
│ - Sends all questions to OpenAI text-embedding-3-small           │
│ - One batch call for all questions                               │
│ - Output: list of vectors (1536 dim each)                        │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ Index to OpenSearch                                              │
│ - For each row: store hist_question, hist_answer, hist_domain,   │
│   embedding, source_id                                          │
│ - Index uses HNSW k-NN (cosine similarity)                       │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
Return { "indexed": N }
```

### Tender Process Flow (POST /tender/process) — Per Question

```
Excel upload
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ AGENT 1: Parser                                                  │
│ - Reads first sheet, skips row 1 (header)                        │
│ - Takes first column only as question                            │
│ - Skips empty rows (if q.strip() is empty)                       │
│ - Output: list of question strings                               │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
FOR EACH question:
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ AGENT 2: Embedding                                               │
│ - Sends [question] to OpenAI text-embedding-3-small              │
│ - Output: 1 vector (1536 dim)                                    │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ AGENT 3: Retrieval                                               │
│ - OpenSearch k-NN search with vector, k=3                       │
│ - Returns top 3 matches with: hist_question, hist_answer,       │
│   hist_domain, score                                             │
│ - best_score = matches[0]["score"] if matches else 0.0            │
│ - history_found = best_score >= 0.75                             │
└─────────────────────────────────────────────────────────────────┘
    │
    ├── history_found = true ─────────────────────────────┐
    │                                                       │
    └── history_found = false ──────────────────────────────┤
                                                            │
                                                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ AGENT 4: Generation (LLM)                                        │
│ IF history_found:                                                │
│   - System: use historical context, return JSON                  │
│   - User: question + top 3 matches (Q, A, Domain, score)         │
│   - LLM returns: answer, domain_tag, historical_alignment, flags│
│ IF NOT history_found:                                            │
│   - System: no prior context, be conservative                    │
│   - User: question only                                         │
│   - LLM returns: answer, domain_tag, flags                       │
│ - Temperature: 0.2                                              │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ AGENT 5: Post-Process                                            │
│ 1. Filter flags: keep only unsupported_certification_claim,      │
│    needs_review, processing_error, parse_error                  │
│ 2. If NOT history_found:                                         │
│    - Set historical_alignment = false                            │
│    - Add "needs_review" to flags if not already                  │
│ 3. Compute confidence (see section 4)                             │
│ 4. Build result dict                                             │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
Append to results[]; continue to next question
    │
    ▼
Build summary (total_questions_processed, flagged_count, flagged_items, completion_status)
    │
    ▼
Return { results, summary }
```

---

## 3. End-to-End Diagram (One Question)

```
                    ┌──────────────┐
                    │   Question   │
                    │  (Excel row) │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │   Parser     │  Agent 1
                    │  Extract Q   │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐      OpenAI
                    │  Embedding   │  ◀── API
                    │  Q → vector  │  Agent 2
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐      OpenSearch
                    │  Retrieval   │  ◀── k-NN search
                    │  Top-3 +     │  Agent 3
                    │  best_score  │
                    └──────┬───────┘
                           │
              ┌────────────┴────────────┐
              │                         │
    best_score>=0.75            best_score<0.75
    history_found=true          history_found=false
              │                         │
              ▼                         ▼
    ┌─────────────────┐       ┌─────────────────┐
    │ LLM + 3 matches │       │ LLM, no matches │
    │ (with history)  │       │ (no history)    │
    └────────┬────────┘       └────────┬────────┘
             │                         │
             │   Agent 4: Generation   │
             └────────────┬────────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │  Post-Process   │  Agent 5
                 │  Filter flags   │
                 │  Compute conf   │
                 └────────┬────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │  Result object  │
                 └─────────────────┘
```

---

## 4. Confidence Score — Actual Implementation

**Location:** `app/services.py`, function `compute_confidence`

**Code:**
```python
def compute_confidence(history_found: bool, best_score: float, flags: list[str]) -> str:
    bad = {"unsupported_certification_claim", "needs_review"}
    if any(f in bad for f in flags):
        return "Low"
    if not history_found or best_score < SIMILARITY_THRESHOLD:
        return "Low"
    if best_score >= 0.85:
        return "High"
    return "Medium"
```

**Constants:** `SIMILARITY_THRESHOLD = 0.75` (from config)

**Logic (evaluated in order):**

| Step | Condition | Result |
|------|-----------|--------|
| 1 | flags contains `unsupported_certification_claim` OR `needs_review` | **Low** (stop) |
| 2 | `history_found` is False OR `best_score` < 0.75 | **Low** (stop) |
| 3 | `best_score` >= 0.85 | **High** |
| 4 | Otherwise (0.75 <= best_score < 0.85, history_found=true, no bad flags) | **Medium** |

**Important:** Bad flags override the score. Even if best_score is 0.95, if flags contain `needs_review`, confidence is Low.

---

## 5. Detailed Step-by-Step (What Happens)

### Agent 1: Parser

**Ingest:** Reads CSV with DictReader. For each row: `q = Question or question`, `a = Answer or answer`, `d = Domain or domain`. If `q` is non-empty after strip, append `(q, a or "", d or "")`. Empty question rows are skipped.

**Tender:** Uses openpyxl. First sheet, rows from 2 onward. `q = str(row[0]).strip()`. If `q` is non-empty, append. Empty first cell = row skipped.

### Agent 2: Embedding

Calls `embed_texts(texts)`. Sends list to `OpenAI.embeddings.create(model="text-embedding-3-small", input=texts)`. Returns list of 1536-dim vectors. In tender process, one question at a time so `[question]` → 1 vector.

### Agent 3: Retrieval

Calls `search_similar(client, vector, k=3)`. OpenSearch query: `knn` on `embedding` field with `vector` and `k=3`. Returns hits with `_score` and `_source`. Builds list of `{hist_question, hist_answer, hist_domain, score}`. `best_score = matches[0]["score"]` if matches else 0.0. `history_found = best_score >= 0.75`.

### Agent 4: Generation

If history_found: prompts include question + 3 matches (Q, A, Domain, score). LLM returns JSON: answer, domain_tag, historical_alignment_indicator, flags.

If not history_found: prompts include question only. LLM returns: answer, domain_tag, flags. Backend later sets historical_alignment=false and adds needs_review.

### Agent 5: Post-Process

1. `filter_flags(flags)` → keep only values in `ALLOWED_FLAGS`
2. If not history_found: `historical_alignment = False`; if `"needs_review"` not in flags, append it
3. `confidence_level = compute_confidence(history_found, best_score, flags)`
4. Build `{question, answer, domain_tag, confidence_level, historical_alignment_indicator, flags}`

---

## 6. Error Handling

If any step fails (embed, retrieve, LLM) inside `process_one_question`, the except block returns:

```python
{
    "question": question,
    "answer": "Processing failed for this row",
    "domain_tag": "Compliance",
    "confidence_level": "Low",
    "historical_alignment_indicator": False,
    "flags": ["processing_error"],
}
```

Processing continues with the next question. One failure does not stop the batch.

---

## 7. File Structure

```
main.py           # Endpoints, process_one_question loop
app/services.py   # All 5 agents: parse, embed, retrieve, call_llm, filter_flags, compute_confidence
app/config.py     # SIMILARITY_THRESHOLD, RETRIEVAL_TOP_K, ALLOWED_FLAGS
```

---

For scenario outputs and setup, see [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md).
