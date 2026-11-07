"""Batch calls to a large language model for the strict screening pass, plus the parser
for its JSON answers.

Two back ends, chosen with LLM_BACKEND:
  api    (default) an OpenAI-compatible chat-completions endpoint (LLM_API_URL / LLM_API_KEY / LLM_MODEL)
  codex  the OpenAI Codex CLI run non-interactively (`codex exec`), model from CODEX_MODEL
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LLM_API_URL, LLM_API_KEY, LLM_MODEL  # noqa: E402

BACKEND = os.environ.get("LLM_BACKEND", "api")
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
CODEX_MODEL = os.environ.get("CODEX_MODEL", "gpt-5")
CODEX_EFFORT = os.environ.get("CODEX_EFFORT", "medium")
CODEX_TIMEOUT = int(os.environ.get("CODEX_TIMEOUT", "420"))
CODEX_PREFIX = ("Answer from the text in this message only. Do not run commands, read files, search the web "
                "or call any tool. Your final message must be exactly the JSON array requested below.\n\n")


def call_api_batch(prompt, retries=3):
    for attempt in range(retries):
        try:
            r = requests.post(LLM_API_URL, headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                              json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
                              timeout=180)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            print(f"    API {r.status_code} (attempt {attempt + 1})", flush=True)
        except Exception as e:
            print(f"    API error: {e}", flush=True)
        time.sleep(5 * (attempt + 1))
    return None


def call_codex_batch(prompt, retries=3):
    """`codex exec` in a read-only sandbox without a saved session; the final answer is written to a temp file."""
    for attempt in range(retries):
        fd, out_path = tempfile.mkstemp(prefix="codex_out_", suffix=".txt")
        os.close(fd)
        try:
            cmd = [CODEX_BIN, "exec", "-m", CODEX_MODEL, "-c", f'model_reasoning_effort="{CODEX_EFFORT}"',
                   "-s", "read-only", "--ephemeral", "--skip-git-repo-check", "--color", "never", "-o", out_path, "-"]
            result = subprocess.run(cmd, input=CODEX_PREFIX + prompt, capture_output=True, text=True, timeout=CODEX_TIMEOUT)
            with open(out_path) as f:
                text = f.read().strip()
            if result.returncode == 0 and text:
                return text
            print(f"    codex failed (rc={result.returncode}, attempt {attempt + 1})", flush=True)
        except subprocess.TimeoutExpired:
            print(f"    codex timed out ({CODEX_TIMEOUT}s, attempt {attempt + 1})", flush=True)
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass
        time.sleep(5 * (attempt + 1))
    return None


def call_llm_batch(prompt, retries=3):
    return call_codex_batch(prompt, retries) if BACKEND == "codex" else call_api_batch(prompt, retries)


def parse_batch_response(raw, paper_ids):
    """Parse the JSON array of {id, verdict}; fall back to a line-by-line regex."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        items = json.loads(text)
        if isinstance(items, list):
            return {int(it["id"]): ("reject" if "reject" in it["verdict"].strip().lower() else "keep") for it in items}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass
    result = {}
    for pid in paper_ids:
        m = re.search(rf'"id"\s*:\s*{pid}\s*,\s*"verdict"\s*:\s*"(keep|reject)"', text, re.IGNORECASE)
        if m:
            result[pid] = m.group(1).lower()
    return result or None
