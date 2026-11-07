"""Exact-phrase filter for project links (applied after the keyword filter, before the LLM pass).

A project record is kept only if its text (names, keywords and abstracts, Chinese and English)
contains the model's full name as a phrase, or the abbreviation as a whole word (letters-only
abbreviations are matched case-sensitively, a version suffix is allowed). Links that fail are set
to relevance = -2.

Usage:
  python screen/phrase_filter_projects.py            # dry run over all models with status = 1
  python screen/phrase_filter_projects.py --apply
"""
import re
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PG_DSN  # noqa: E402


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def name_patterns(abbrev, aliases):
    names = sorted({n.strip() for n in [abbrev] + (aliases or "").replace(";", ",").split("|") if n.strip()})
    # names with digits, hyphens or spaces (MRI-CGCM, E3SM) are matched case-insensitively;
    # letters-only abbreviations (MIROC, UKESM) case-sensitively, to avoid ordinary words
    return [re.compile(r"(?<![A-Za-z0-9])" + re.escape(n).replace(r"\-", r"[-\s]?").replace(r"\ ", r"[-\s]?")
                       + r"(?:[-\s]?v?\d[\w.]*)?(?![A-Za-z0-9])", 0 if n.isalpha() else re.I) for n in names]


def main():
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    cur.execute("SELECT id, abbrev, full_name, aliases FROM model WHERE status = 1 ORDER BY id")
    models = cur.fetchall()
    print(f"{'id':>4} {'abbrev':<12} {'links':>6} {'keep':>6} {'drop':>6}")
    total_drop = 0
    for mid, abbrev, full, aliases in models:
        phrase = norm(full)
        pats = name_patterns(abbrev, aliases)
        cur.execute("""SELECT p.id, p.project_name, p.project_name_en, p.keyword, p.keyword_en, p.abstract, p.abstract_en
                       FROM model_project mp JOIN project p ON p.id = mp.project_id
                       WHERE mp.model_id = %s AND mp.relevance = 1""", (mid,))
        keep, drop = [], []
        for row in cur.fetchall():
            txt = " ".join(x for x in row[1:] if x)
            ok_phrase = bool(phrase) and re.search(r"(?<!\S)" + re.escape(phrase) + r"(?:v?\d[\w.]*)?(?!\S)", norm(txt)) is not None
            ok_abbrev = any(p.search(txt) for p in pats)
            (keep if ok_phrase or ok_abbrev else drop).append(row[0])
        print(f"{mid:>4} {abbrev:<12} {len(keep) + len(drop):>6} {len(keep):>6} {len(drop):>6}")
        total_drop += len(drop)
        if apply and drop:
            cur.execute("UPDATE model_project SET relevance = -2 WHERE model_id = %s AND project_id = ANY(%s)", (mid, drop))
    if apply:
        conn.commit()
    print(f"dropped {total_drop} links" + ("" if apply else " (dry run)"))
    conn.close()


if __name__ == "__main__":
    main()
