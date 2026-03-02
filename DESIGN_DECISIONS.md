# Multi-Agent Tender Response — Design & Implementation Guide

A clear guide to how the system works, why we made each choice, and what happens at every step. For architecture overview, see [ARCHITECTURE.md](ARCHITECTURE.md). You can export this to PDF from your editor or browser (Print → Save as PDF).

---

## Models We Use

We use two providers: OpenAI and Anthropic. You choose the model when calling the tender endpoint.

**OpenAI (default)**

| Model        | Role                             | When to use                          |
|--------------|-----------------------------------|--------------------------------------|
| gpt-4o-mini  | Default. Writes tender answers.   | Normal use. Best cost and speed.     |
| gpt-4o       | Stronger writing and reasoning.    | When quality matters more than cost. |

**Anthropic (Claude)**

| Model            | Role                             | When to use                          |
|------------------|-----------------------------------|--------------------------------------|
| claude-3-haiku   | Fast, lower cost.                 | Good alternative to gpt-4o-mini.     |
| claude-3-sonnet  | Higher quality answers.           | When you prefer Claude over GPT.     |
| claude-3-5-sonnet | Best Claude model we support.    | Highest quality when using Claude.   |

**Embeddings (always OpenAI)**  
We use `text-embedding-3-small` to turn text into vectors. It is only used for search, not for writing answers. All models (GPT and Claude) use the same embedding model.

---

## Step 1: Setup

**What you do:** Copy `.env.example` to `.env` and add your API keys.

**What happens:** The app reads `OPENAI_API_KEY` for GPT and `ANTHROPIC_API_KEY` for Claude. Embeddings use the OpenAI key. All values stay in `.env` so nothing is hardcoded.

**Why:** One place for secrets, easy to change per environment.

---

## Step 2: Run the System

**What you do:** Run `docker compose up -d`.

**What happens:** Docker starts two containers:
- OpenSearch on port 9200 (vector search database).
- FastAPI backend on port 8000 (API that processes tenders).

**Why:** Everything runs in containers so it works the same on different machines.

---

## Step 3: Ingest Historical Data

**What you do:** Call `POST /history/ingest` with a CSV file (columns: Question, Answer, Domain).

**What happens:**
1. The file is parsed into rows.
2. All questions are turned into vectors with the embedding model.
3. Each row (question, answer, domain, vector) is stored in OpenSearch.
4. The API returns how many rows were indexed.

**Why:** This builds the long-term memory we use when answering new tender questions.

---

## Step 4: Process a New Tender

**What you do:** Call `POST /tender/process` with an Excel file of questions. You can pass `model=gpt-4o-mini` (default) or any other supported model.

**What happens (per question):**
1. The question text is read from the Excel.
2. The question is turned into a vector.
3. OpenSearch finds the 3 most similar historical questions (and their answers).
4. If the best match score is at least 0.75, we treat it as “history found.”
5. If history is found: the LLM gets the question plus the top 3 historical Q&A.
6. If history is not found: the LLM gets only the question.
7. The LLM returns: answer, domain_tag, flags, and (if history) historical_alignment_indicator.
8. We filter flags to allowed values only.
9. If there is no history, we set alignment=false and add `needs_review`.
10. We compute confidence from: history_found, best score, and flags.
11. The result is added to the list and we continue to the next question.

**Why:** This flow makes sure we only use strong matches, and we always handle “no history” safely.

---

## Step 5: Config and Environment

**What happens:** The app reads all config from `.env`. `config.py` only calls `os.getenv()`; it never stores secrets or URLs.

**Why:** Values come from one source. In Docker, compose can override `OPENSEARCH_URL` for the container network.

---

## Step 6: Long-Term Memory (OpenSearch)

**What happens:** OpenSearch stores:
- `hist_question`, `hist_answer`, `hist_domain`
- `embedding` (vector)
- `source_id`

We use HNSW indexing for fast similarity search with cosine similarity.

**Why:** OpenSearch supports vector search and runs in Docker. HNSW keeps retrieval fast even with more data.

---

## Step 7: Embeddings

**What happens:** Questions are sent to OpenAI `text-embedding-3-small`. Each question becomes a vector of 1536 numbers. In ingest we send all questions in one call; in process we send all new questions in one call per tender.

**Why:** Same embedding model everywhere, batch calls to reduce cost and latency.

---

## Step 8: Retrieval

**What happens:** For each new question we run a k-NN search in OpenSearch with Top-K=3. We take the best score. If best_score >= 0.75 we use the matches; otherwise we treat it as “no history” and do not send any matches to the LLM.

**Why:** Three matches give enough context without making prompts too long. The threshold separates “good match” from “no match.”

---

## Step 9: LLM Call

**What happens:** We call the model you chose (or the default). With history: system prompt asks the model to follow historical context and return JSON. Without history: system prompt says there is no prior context and asks for a conservative answer. We ask for JSON with answer, domain_tag, flags, and (if history) historical_alignment_indicator. Temperature is 0.2.

**Why:** Low temperature keeps answers stable. Different prompts for history vs no history avoid confusion.

---

## Step 10: Flags and Confidence

**What happens:** Flags must be from: `unsupported_certification_claim`, `needs_review`, `processing_error`, `parse_error`. We drop any other values. Confidence is computed in the backend:
- Any bad flag (unsupported_certification_claim, needs_review) → Low.
- No history or low score → Low.
- Score >= 0.85 and no bad flags → High.
- Otherwise → Medium.

**Why:** Fixed flags keep the API consistent. Backend rules for confidence make it deterministic.

---

## Step 11: Error Handling

**What happens:** If one question fails (parse, embed, or LLM), we add a result with answer `"Processing failed for this row"`, flags `["processing_error"]`, and confidence Low. We then continue with the rest.

**Why:** A single failure does not stop the whole tender.

---

## Scenarios: Question, Behaviour, Output

### Scenario 1 — History match, no certification

**Question:** Does the platform enforce TLS 1.2+?  
**History:** Yes; similar question with answer about TLS 1.2+.

**What happens:** history_found=true, high score. LLM gets history and returns an aligned answer.

**Output:**
```json
{
  "question": "Does the platform enforce TLS 1.2+?",
  "answer": "Yes, we enforce TLS 1.2 and above for all production traffic.",
  "domain_tag": "Security",
  "confidence_level": "High",
  "historical_alignment_indicator": true,
  "flags": []
}
```

---

### Scenario 2 — No history, certification asked

**Question:** Are you ISO 27001 certified?  
**History:** No match.

**What happens:** history_found=false. LLM gets question only. Backend sets alignment=false, adds needs_review, confidence=Low.

**Output:**
```json
{
  "question": "Are you ISO 27001 certified?",
  "answer": "Please confirm with our compliance team.",
  "domain_tag": "Compliance",
  "confidence_level": "Low",
  "historical_alignment_indicator": false,
  "flags": ["needs_review"]
}
```

---

### Scenario 3 — History supports certification

**Question:** Are you ISO 27001 certified?  
**History:** Yes; similar question with “Yes we are ISO 27001 certified.”

**What happens:** history_found=true. LLM uses that answer. No fabrication.

**Output:**
```json
{
  "question": "Are you ISO 27001 certified?",
  "answer": "Yes, we are ISO 27001 certified.",
  "domain_tag": "Compliance",
  "confidence_level": "High",
  "historical_alignment_indicator": true,
  "flags": []
}
```

---

### Scenario 4 — LLM fabricates certification

**Question:** Are you SOC 2 certified?  
**History:** Match found, but no SOC 2 in those answers.

**What happens:** LLM might still claim SOC 2. We instruct it not to; if it does, we expect flag `unsupported_certification_claim`. Confidence is Low.

**Output:**
```json
{
  "question": "Are you SOC 2 certified?",
  "answer": "Yes, we are SOC 2 Type II certified.",
  "domain_tag": "Compliance",
  "confidence_level": "Low",
  "historical_alignment_indicator": false,
  "flags": ["unsupported_certification_claim"]
}
```

---

### Scenario 5 — No history, general question

**Question:** Do you support multi-tenancy?  
**History:** No match.

**What happens:** LLM gives a conservative answer. Backend adds needs_review, confidence=Low.

**Output:**
```json
{
  "question": "Do you support multi-tenancy?",
  "answer": "Our platform supports multi-tenant architecture. Please confirm with the product team for your use case.",
  "domain_tag": "Architecture",
  "confidence_level": "Low",
  "historical_alignment_indicator": false,
  "flags": ["needs_review"]
}
```

---

### Scenario 6 — Processing error

**Question:** Any.  
**What happens:** Embed or LLM call fails with an exception.

**Output:**
```json
{
  "question": "Does the platform support SSO?",
  "answer": "Processing failed for this row",
  "domain_tag": "Compliance",
  "confidence_level": "Low",
  "historical_alignment_indicator": false,
  "flags": ["processing_error"]
}
```

---

## Summary Block (Every Response)

Each response includes a summary:

```json
{
  "total_questions_processed": 5,
  "flagged_count": 2,
  "flagged_items": [
    {"question": "Are you ISO 27001 certified?", "flags": ["needs_review"]},
    {"question": "Are you SOC 2 certified?", "flags": ["unsupported_certification_claim"]}
  ],
  "completion_status": "completed_with_flags"
}
```

---

**To export as PDF:** Open this file in VS Code or a browser, then use Print → Save as PDF.
