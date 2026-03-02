# Multi-Agent Tender Response

FastAPI service that generates tender responses using historical Q/A and LLM.

For steps, decisions, and scenario behaviour, see [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md).

## Setup (Docker - all services)

1. Copy `.env.example` to `.env` and add your `OPENAI_API_KEY`
2. Run: `docker compose up -d`
3. API: http://localhost:8000 | Swagger: http://localhost:8000/docs

## Endpoints

**POST /history/ingest**  
Upload CSV with columns: Question, Answer, Domain. Indexes into OpenSearch.

**POST /tender/process**  
Upload Excel with questions in first column. Optional form field `model`: `gpt-4o-mini` (default) or `gpt-4o`.

## Sample Data

- `samples/historical_tenders.csv` - Ingest this first
- `samples/new_tender.xlsx` - Use for /tender/process

## Example

```bash
# Ingest history
curl -X POST http://localhost:8000/history/ingest -F "file=@samples/historical_tenders.csv"

# Process tender
curl -X POST http://localhost:8000/tender/process -F "file=@samples/new_tender.xlsx"
```

## Env Vars (.env)

| Var | Purpose |
|-----|---------|
| OPENAI_API_KEY | Required for GPT models |
| ANTHROPIC_API_KEY | Required for Claude models |
| OPENSEARCH_URL | Docker sets opensearch:9200; local use localhost:9200 |

## Models (form param `model`)

| Model | Provider |
|-------|----------|
| gpt-4o-mini | OpenAI (default) |
| gpt-4o | OpenAI |
| claude-3-haiku | Anthropic |
| claude-3-sonnet | Anthropic |
| claude-3-5-sonnet | Anthropic |
