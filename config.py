"""Runtime settings for the MAI pipeline.

Credentials are read from environment variables so that nothing secret is kept in
the repository:

  MAI_PG_DSN              PostgreSQL connection string, e.g. "host=localhost dbname=mai user=mai password=..."
  SCOPUS_API_KEY_FILES    one or more text files (one Scopus API key per line), separated by os.pathsep
  LLM_API_URL / LLM_API_KEY / LLM_MODEL
                          an OpenAI-compatible chat-completions endpoint used by screen/llm_screen.py
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

# PostgreSQL
PG_DSN = os.environ.get("MAI_PG_DSN", "host=localhost port=5432 dbname=mai user=mai")

# Scopus API
SCOPUS_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
SCOPUS_ABSTRACT_URL = "https://api.elsevier.com/content/abstract/eid/"
SCOPUS_PAGE_SIZE = 25        # standard key limit; 200 with an institutional token
SCOPUS_MAX_OFFSET = 5000     # beyond this the query is split by year
SCOPUS_INTERVAL = 0.15       # seconds between requests per key (9 req/s)
API_KEY_FILES = [p for p in os.environ.get("SCOPUS_API_KEY_FILES", "").split(os.pathsep) if p]
CITE_WORKERS = 20
MAX_RETRIES = 5
REQUEST_TIMEOUT = 30

# Scopus subject-area filter applied to every model query
VALID_SUBJECT_AREAS = {"ENVI", "AGRI", "EART", "DECI"}
SUBJAREA_FILTER = "SUBJAREA(" + " OR ".join(sorted(VALID_SUBJECT_AREAS)) + ")"

# resumable collection: progress files
CHECKPOINT_DIR = os.path.join(ROOT, "output", "checkpoints")

# LLM screening (screen/llm_screen.py uses an OpenAI-compatible API)
LLM_API_URL = os.environ.get("LLM_API_URL", "https://api.deepseek.com/chat/completions")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
