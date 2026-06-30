# Multi-Source Candidate Data Transformer

**Deterministic, rule-based pipeline that ingests heterogeneous recruiting data and emits one canonical profile per person — reshaped at runtime by JSON config, with no LLM.**

---

## Overview

Recruiting data arrives from many sources at once — recruiter CSV exports, plain-text resumes, and (in production) ATS exports, GitHub profiles, and free-text notes. **This implementation ingests recruiter CSV and plain-text resume files**; additional adapters are extension points only.

This project solves that by running a fixed four-stage pipeline: **ingest → merge → project → validate**. Source adapters capture raw values with provenance; merge normalizes, deduplicates, and resolves conflicts into a rich canonical record; projection reshapes output from a runtime config without code changes; validation enforces the config-implied schema before anything is emitted.

Every emitted value carries **provenance** (source, extraction method) and **confidence** (source trust, agreement, method reliability). The system is fully **deterministic** — same inputs produce byte-identical output — and **fail-soft**: bad sources warn and continue; unparseable values become `null`, never invented.

---



## Quick reference


| Command                                                 | Purpose                                     |
| ------------------------------------------------------- | ------------------------------------------- |
| `python main.py --csv PATH --resume PATH --config PATH` | Run the full pipeline; JSON array to stdout |
| `python -m eightfold_transformer.app ...`               | Module entry point (equivalent flags)       |
| `python main.py ... --out output.json`                  | Write JSON to a file instead of stdout      |
| `python main.py ... --no-validate`                      | Skip post-projection structural validation  |
| `pytest` / `pytest -q`                                  | Run the test suite (191 tests)              |


---



## Setup & Installation

Requires **Python 3.9+** (developed and verified on 3.13). Dependencies are minimal; CSV, JSON, argparse, and hashing use the standard library.

### Clone the repository

```bash
git clone https://github.com/Aiyaan-Mahajan/Multi-Source-Candidate-Data-Transformer.git
```


### macOS


```bash
# 1. Go to the project root (the folder containing main.py)
cd Multi-Source-Candidate-Data-Transformer

# 2. Create a virtual environment (required — see PEP 668 note below)
python3 -m venv venv

# 3. Activate (prompt should show "(venv)")
source venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Verify install
pytest -q
```

> **PEP 668:** macOS ships a system-managed Python that blocks global `pip install` with an *externally-managed-environment* error. Always use a venv before installing. Do not use `sudo pip install` or `--break-system-packages`.

Key dependencies: `pydantic`, `phonenumbers`, `python-dateutil`, `pytest`. `pdfplumber` and `python-docx` are listed for planned PDF/DOCX ingestion (not yet wired).

### Windows

```powershell
# 1. Go to the project root
cd Multi-Source-Candidate-Data-Transformer

# 2. Create a virtual environment
python -m venv venv

# 3. Activate
venv\Scripts\activate
# PowerShell (if execution policy blocks .bat): venv\Scripts\Activate.ps1

# 4. Install dependencies
pip install -r requirements.txt

# 5. Verify install
pytest -q
```

On Windows, use `python` (not `python3`) if that is your Python launcher convention. The venv requirement applies equally — avoid installing packages into the system interpreter.

---



## Running the project

**Prerequisites:** project root directory (the folder containing `main.py`), virtual environment activated.

### Default run (sample data)

```bash
python main.py --csv data/candidates.csv --resume data/resume.txt --config configs/default.json


Module equivalent: `python -m eightfold_transformer.app --csv data/candidates.csv --resume data/resume.txt --config configs/default.json`

### Custom projection config

```bash
python main.py --csv data/candidates.csv --resume data/resume.txt --config configs/example_custom_config.json
```

When `--config is omitted, the CLI uses configs/default.json, falling back to configs/default_config.json`.

### Save Output to Files
```bash
mkdir -p output
python main.py --csv data/candidates.csv --resume data/resume.txt --config configs/default.json --out output/default_run.json
python main.py --csv data/candidates.csv --resume data/resume.txt --config configs/example_custom_config.json --out output/custom_config_run.json
```

Pre-generated sample outputs are committed in the repo:

output/default_run.json — default projection config (full candidate fields)
output/custom_config_run.json — nested contact fields + confidence wrappers

### Optional flags


| Flag            | Description                                                    |
| --------------- | -------------------------------------------------------------- |
| `--csv PATH`    | Recruiter CSV source *(optional)*                              |
| `--resume PATH` | Plain-text resume source *(optional)*                          |
| `--config PATH` | Projection config JSON                                         |
| `--out PATH`    | Write JSON to file instead of stdout                           |
| `--no-validate` | Skip structural validation *(on by default; failure is fatal)* |


At least one of `--csv` / `--resume` is required.

### Expected verification logs

Stage progress is logged to **stderr** only. Verified output from a default sample run:

```
INFO eightfold_transformer.cli: Loaded projection config from configs/default.json
INFO eightfold_transformer.cli: Ingested 8 record(s) from CSV data/candidates.csv
INFO eightfold_transformer.cli: Ingested resume data/resume.txt
INFO eightfold_transformer.cli: Stage 1/4 ingestion: 9 partial record(s) total
INFO eightfold_transformer.cli: Stage 2/4 merge: 5 canonical candidate(s)
INFO eightfold_transformer.cli: Stage 3/4 projection: shaped 5 object(s)
INFO eightfold_transformer.cli: validated 5 record(s)
INFO eightfold_transformer.cli: Stage 4/4 output: wrote 5 object(s) to stdout
```

**I/O contract:**

- **stdout** — pure JSON array of projected objects (one per candidate; `[]` for zero candidates), ordered by `candidate_id`
- **stderr** — stage logs (`INFO`/`WARNING`); safe to pipe stdout into `jq`

**Exit codes:**


| Code | Meaning                                                                              |
| ---- | ------------------------------------------------------------------------------------ |
| `0`  | Success, or valid-but-empty result (`[]`)                                            |
| `1`  | Fatal runtime error (bad config, no readable sources, projection/validation failure) |
| `2`  | Usage error (neither `--csv` nor `--resume` given)                                   |


**Fail-soft behavior:** a missing `--csv`/`--resume` path warns on stderr and continues with the remaining source. Fatal only when *none* of the provided paths are readable. Empty-but-readable files contribute zero records.

---



## Architecture

The pipeline is a straight line of deterministic stages:

```
ingest → merge (normalize + entity resolution + confidence) → project → validate
```

**Source-adapter pattern.** Each input source has an ingestion adapter that reads raw values and stamps provenance — it never normalizes, merges, or validates. Implemented: recruiter CSV (`csv_reader.py`) and plain-text resume (`resume_parser.py`, including LinkedIn/GitHub URL extraction from resume text). Additional adapters (ATS JSON, GitHub API, recruiter notes) are included as extension points but are outside the scope of this assignment.

**Canonical record vs. projection.** Merge produces a rich `CanonicalCandidate` — every field, provenance trail, and confidence score. Projection is a separate, config-driven, read-only layer that selects, renames, and nests fields for output. Validation runs **after** projection against the schema implied by the config.

**Provenance travels with the value.** Every extracted value is wrapped in `TrackedValue[T]` carrying `value`, `source`, `confidence`, and `extraction_method`.

### Data flow

```
  CSV / resume .txt / [future adapters]
              │
              ▼
  INGESTION ──► PartialRecord[]  (adapters; raw capture + TrackedValue)
              │
              ▼
  MERGE ──────► CanonicalCandidate[]  (normalize, entity resolution, confidence)
              │
              │   projection config (runtime JSON)
              ▼
  PROJECTION ─► dict per candidate  (select / rename / nest; read-only)
              │
              ▼
  VALIDATION ─► validate_projected()  (ON by default; --no-validate to skip)
              │
              ▼
  OUTPUT ─────► JSON array → stdout (or --out); stage logs → stderr
```

---



## Runtime projection config

The main differentiator: output shape is declared in JSON at runtime — no code changes required. Every output key comes from `config.fields`; nothing is implicit.

Minimal example (matches actual config format):

```json
{
  "fields": [
    { "path": "full_name", "from": "full_name", "type": "string", "required": true, "normalize": false },
    { "path": "skills", "from": "skills", "type": "array", "required": false, "normalize": true }
  ],
  "include_confidence": false,
  "include_provenance": false,
  "on_missing": "null"
}
```

**`default.json` vs `default_config.json`:** `default.json` is the primary projection config and includes the full field set (`candidate_id`, `headline`, `full_name`, `emails`, `phones`, `location_country`, `skills`, `education`, `experience`, `links`). `default_config.json` is a legacy fallback with a subset of those fields (no `candidate_id`, no `headline`). The CLI uses `default.json` first and falls back to `default_config.json` only if the former is missing. Fields such as `candidate_id` appear in output only when declared in the active config.

See `configs/example_custom_config.json` for nested contact fields, confidence wrappers, and flattened skill names. The projector supports field select/rename, nested paths, array indexing, per-field `normalize`, `on_missing` (`null`/`omit`/`error`), and confidence/provenance toggles.

---



## Sample output

The default sample run ingests **8 CSV rows + 1 resume** (9 partial records) and merges them into **5 canonical candidates**. All four "Priya Sharma" CSV variants and the resume collapse into one merged profile via composite entity resolution (name, initials, email/phone overlap, company normalization, title overlap, resume corroboration).

Verified Priya output from a live CLI run (`configs/default.json`):

```json
{
  "candidate_id": "cand_086d12a7e164b775",
  "full_name": "Priya Sharma",
  "emails": [
    "priya.s@work.io",
    "priya.sharma@gmail.com"
  ],
  "phones": [],
  "location_country": null,
  "headline": "Engineering Lead, Distributed Systems",
  "skills": [
    { "name": "Docker", "sources": ["resume"] },
    { "name": "Go", "sources": ["resume"] },
    { "name": "JavaScript", "sources": ["resume"] },
    { "name": "Kubernetes", "sources": ["resume"] },
    { "name": "PostgreSQL", "sources": ["resume"] },
    { "name": "Python", "sources": ["resume"] }
  ],
  "education": [
    {
      "institution": "State University",
      "degree": "B.S.",
      "field": "Computer Science",
      "end_year": 2016
    }
  ],
  "experience": [
    {
      "company": "Acme Corporation",
      "title": "Senior Backend Engineer",
      "start": "2019-01",
      "end": null,
      "summary": "Built event-driven services and owned the on-call rotation for billing."
    },
    {
      "company": "Globex Inc",
      "title": "Backend Engineer",
      "start": "2016-01",
      "end": "2018-01",
      "summary": "Shipped the first version of the internal payments API."
    }
  ],
  "links": {
    "linkedin": null,
    "github": "github.com/priyasharma",
    "portfolio": null,
    "other": []
  }
}
```

`phones` is empty because sample `555` numbers fail `phonenumbers` validation. Skills are canonicalized via `configs/skills.json` (`Ninjutsu` dropped). Experience dedupes under normalized company keys. Full run: 5 candidates (merged Priya, two Marcus Lee rows, Dana Patel, Jordan Kim).

### Example verification


| Stage        | Result                                                          |
| ------------ | --------------------------------------------------------------- |
| **Input**    | 9 partial records (8 CSV rows + 1 resume)                       |
| **Pipeline** | ingestion → merge/dedupe → confidence → projection → validation |
| **Output**   | 5 deterministic canonical candidate profiles                    |


---



## Implemented sources

| Category | Component |
|----------|-----------|
| **Structured (Implemented)** | Recruiter CSV ingestion |
| **Unstructured (Implemented)** | Plain-text resume parsing |
| **Core pipeline (Implemented)** | Merge & deduplication, entity resolution, confidence scoring, runtime projection, post-projection validation |
| **Future extensions** | ATS JSON (`ats_json.py`), GitHub API (`github_api.py`), recruiter notes (`recruiter_notes.py`) |
| **Not implemented** | PDF/DOCX resume ingestion (`pdfplumber`, `python-docx` dependencies included but no adapter connected) |
| **Partial** | LinkedIn URLs extracted from resume text only (no dedicated adapter) |


---



## Design decisions

- `TrackedValue[T]` **as provenance carrier.** A single generic wrapper (`value` / `source` / `confidence` / `extraction_method`) travels with every extracted value. The top-level `provenance[]` ledger is a flattened view of those carriers.
- **Determinism by construction.** No `datetime.now()`, no RNG, no network. Dates use a fixed anchor (`2000-01-01`) for missing components. Clusters, list fields, skills, and provenance are sorted; output ordered by `candidate_id` (stable SHA-256 over strongest match key).
- **Conservative-but-capable merging.** Exact email and GitHub/LinkedIn keys union records immediately. Name blocking limits comparisons; composite entity resolution scores name, initials, email/phone/company/title overlap plus resume corroboration. High composite scores can merge records sharing no single strong key (e.g. P. Sharma merges with Priya Sharma when phone/company overlap and composite score exceeds threshold.).
- **Company normalization.** Legal suffixes and variants (`Corp`, `Corporation`, `Inc`, …) stripped so `Acme Corp` and `Acme Corporation` match for clustering and experience dedupe.
- **Skill vocabulary gate.** `configs/skills.json` maps aliases to canonical names; unknown tokens dropped at merge.
- **Confidence that punishes disagreement.** `field_confidence = clamp01(0.5·source_trust + 0.3·agreement_ratio + 0.2·method_reliability)`. Contested values score lower. `overall_confidence` is the mean of present core identity fields.
- **Config-driven projection.** Every output key declared in `config.fields`; projector supports select, rename, nested paths, array indexing, per-field normalize directives, `on_missing` policy, and confidence/provenance toggles — strictly read-only over the canonical record.
- **Post-projection validation (on by default).** `validate_projected()` checks required fields, types, and nested shape. Failures are fatal (exit `1`). Use `--no-validate` only when debugging a config.
- **Unknown → null, never invented.** Unparseable phones and unmapped countries are dropped; unrecognizable input returns `None` per normalizer policy.

---



## Tradeoffs

- **Composite merging can mis-merge or under-merge edge cases.** Two people with similar names at the same company and no conflicting email could merge incorrectly. Thresholds bias toward safety over perfection.
- **Year-only dates fabricate a month.** `2016 → 2016-01` — schema mandates `YYYY-MM`; month `01` is the conventional start-of-year choice.
- **Rule-based resume parsing is precise but brittle.** Accurate on expected section/line layout; won't handle arbitrarily formatted resumes. Deliberate cost of staying deterministic and LLM-free.
- **Skill allow-list drops unknown tokens.** Better to omit than emit nonsense. Expanding `configs/skills.json` is the supported path for new domains.
- **Normalization runs inside merge.** Convenient (merger needs normalized keys to dedupe) but couples the two stages more tightly than a standalone normalize pass.

---



## Testing

191 deterministic pytest tests covering ingestion, normalization, merge, entity resolution, projection, validation, CLI, and acceptance flows. Run from project root with venv activated: `pytest` or `pytest -q`. All tests are offline — no network, no clock, no randomness.

---



## Project structure

```
Eightfold_Transformer/
├── main.py                          # entry-point shim → app.cli.main
├── requirements.txt
├── README.md
├── configs/
│   ├── default.json                 # default projection config (includes candidate_id)
│   ├── default_config.json          # legacy fallback; subset (no candidate_id, no headline)
│   ├── example_custom_config.json   # nested/renamed output example
│   └── skills.json                  # skill alias → canonical name vocabulary
├── data/
│   ├── candidates.csv               # sample recruiter CSV (messy, overlapping)
│   └── resume.txt                   # sample plain-text resume
├── eightfold_transformer/
│   └── app/
│       ├── cli.py                   # CLI orchestration + exit codes
│       ├── __main__.py              # enables python -m eightfold_transformer.app
│       ├── models/                  # schema.py, partial.py, canonical.py
│       ├── ingestion/               # csv_reader.py, resume_parser.py (+ scaffolds)
│       ├── normalization/           # phones.py, dates.py, skills.py, location.py, companies.py
│       ├── merger/                  # matcher.py, entity_resolution.py, resolver.py, …
│       ├── projection/              # config.py, projector.py
│       ├── validation/              # validator.py, schemas.py
│       └── utils/                   # scaffold (hashing/ordering/io stubs)
└── tests/                           # pytest suite (191 tests)
```

---



## Assumptions & out of scope

- **Implemented sources:** This implementation supports the required structured source (Recruiter CSV: name, email, phone, current_company, title) and unstructured source (plain-text resumes, including LinkedIn/GitHub URL extraction). Additional adapters such as ATS JSON, GitHub API, and recruiter notes are intentionally left as future extensions. PDF/DOCX resume ingestion is planned but not yet wired.
- **Phone default region is** `IN` (configurable in the normalizer); numbers with explicit `+<country code>` are parsed as-is.
- **Resume format** assumes recognizable section headers (`SUMMARY`, `SKILLS`, `EXPERIENCE`, `EDUCATION`). Arbitrary layouts are out of scope for the rule-based parser.
- **Skills** must appear in `configs/skills.json` to survive merge; unknown tokens are dropped by design.
- **No LLM, no ML, no network** in the core transform. Everything is rule-based, table-driven, and offline.

