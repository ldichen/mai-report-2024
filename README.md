# MAI leaderboard, 2024 edition

Code and derived data behind the 2024 edition of the Model Academic Influence (MAI)
leaderboard of environmental models and methods. The pipeline collects publication and
citation records from Scopus, screens the links between records and models, computes the
index, and produces the tables and figures of the report.

## Index

For a model in year *t*,

    MAI_t = log10(1 + 2.91 P + 1.03 Pat + 1.12 Proj + 3.56 PC + 1.0 PatC)

with *P* publications, *Pat* patents, *Proj* funded projects, *PC* citing publications
(within two years of the cited paper) and *PatC* citing patents (within six years). The
annual score uses the records of year *t*; the lifetime score uses all records up to *t*.
Weights follow Xu et al. (2022). See `index/compute_mai.py`.

## Layout

| Directory   | Contents |
|-------------|----------|
| `collect/`  | Scopus client, PostgreSQL schema and helpers, collectors for publications and their citing records |
| `screen/`   | keyword filter, exact-phrase filter for projects, lenient and strict LLM screening of paper links |
| `index/`    | MAI computation and the citation-completeness check |
| `analysis/` | leaderboard export and LaTeX tables, rank mobility, reach and intensity by tier, figures |
| `data/`     | derived CSV files of the 2024 edition (panel, leaderboard, movers, composition, keyword and hint lists) |
| `output/`   | tables and figures written by the analysis scripts |

The database steps need a PostgreSQL instance with the schema in `collect/schema.sql` and
the credentials described in `config.py` (environment variables; nothing is read from the
repository). Everything in `analysis/` after the `export` step runs from `data/` alone:

```bash
pip install -r requirements.txt
python analysis/rank_dynamics.py analyse     # tier retention, top-20 flux, risers and fallers
python analysis/channels.py analyse          # reach by rank band, intensity by tier
python analysis/report_tables.py tables      # leaderboard tables, licence summary, momentum table
python analysis/figures.py                   # the four figures of the report
```

## Pipeline

1. `collect/collect_papers.py` queries Scopus for every model in the `model` table
   (subject areas ENVI, AGRI, EART, DECI) and stores the records; `collect/collect_citations.py`
   fetches the citing publications of every linked paper.
2. `screen/keyword_filter.py` applies the model-specific keyword lists in `data/model_keywords.csv`
   to title, abstract and keywords and sets the link relevance (1 keep, 0 undecided, -1 reject).
3. `screen/llm_screen.py` sends undecided and borderline links to an LLM with the model hints in
   `data/model_hints.csv` (lenient: keep when in doubt). `screen/llm_screen_strict.py` re-screens
   models whose names are common words or ambiguous acronyms with the strict hints in
   `data/model_hints_strict.csv`. `screen/phrase_filter_projects.py` keeps project links only when
   the model name appears as an exact phrase.
4. `index/compute_mai.py` computes annual and lifetime scores for every model and year and writes
   the `mai_score` table. `index/check_citations.py` compares collected citation counts with the
   counts reported by Scopus.
5. `analysis/report_tables.py export`, `analysis/rank_dynamics.py export` and
   `analysis/channels.py export` write the CSV files in `data/`; the `analyse` and `tables`
   commands and `analysis/figures.py` produce the results.

Patent records (Google Patents) and funded-project records (Global Funded Research
database, funresearch.cn) were collected with site-specific scripts that depend on a
logged-in browser session; they are not included. The model panel (284 entries) is given by
the `model_id` and `label` columns of `data/leaderboard_2024.csv`; the Scopus search
expressions of individual models are not part of this repository.

## Data terms

The underlying publication, citation, patent and project records are subject to the terms of
Scopus, Google Patents and funresearch.cn and are not redistributed. The files in `data/` are
aggregates per model and year.

Entries with equal scores share no rank: in `data/rank_panel.csv` ties are broken by `model_id`, in
`data/leaderboard_2024.csv` by the order of the model table. The two files therefore give different
2024 annual ranks to 29 tied entries below rank 213; their scores are identical.

## Licence

MIT, see `LICENSE`.
