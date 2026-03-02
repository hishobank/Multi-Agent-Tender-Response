"""Config: reads only from .env. Add values in .env (copy from .env.example)."""
import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or ""
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or ""
OPENSEARCH_URL = os.getenv("OPENSEARCH_URL") or ""

SIMILARITY_THRESHOLD = 0.75
RETRIEVAL_TOP_K = 3
EMBEDDING_DIM = 1536
INDEX_NAME = "tender_history"

ALLOWED_LLM_MODELS = {
    "gpt-4o-mini", "gpt-4o",
    "claude-3-haiku", "claude-3-sonnet", "claude-3-5-sonnet",
}
DEFAULT_LLM_MODEL = "gpt-4o-mini"

ANTHROPIC_MODEL_IDS = {
    "claude-3-haiku": "claude-3-haiku-20240307",
    "claude-3-sonnet": "claude-3-5-sonnet-20241022",
    "claude-3-5-sonnet": "claude-3-5-sonnet-20241022",
}

ALLOWED_FLAGS = frozenset([
    "unsupported_certification_claim",
    "needs_review",
    "processing_error",
    "parse_error",
])
