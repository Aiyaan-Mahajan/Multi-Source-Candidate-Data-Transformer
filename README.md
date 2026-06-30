# Multi-Source Candidate Data Transformer

*Ingest messy candidate data from many sources and emit one clean, deterministic, fully-traceable canonical profile per person — reshaped at runtime by config, with no code changes.*

---

## Setup & Installation (macOS)

**Read this first.** These steps assume you are on macOS with Python 3.9+ installed (developed and verified on 3.13).

### Common mistake: the folder is not a command

If you are already inside the project and type:

```bash
Eightfold_Transformer/
```

zsh will respond with **`permission denied`** (or similar). That is expected — **`Eightfold_Transformer/` is a directory, not an executable program.** You cannot "run" a folder.

Instead, **navigate into the project**, create a virtual environment, activate it, and run `python main.py` (see below).

### Step-by-step (from scratch)

Run these commands **in order** from any terminal. Replace the `cd` path with wherever you cloned the repo (e.g. `~/Eightfold_Transformer`).

```bash
# 1. Go to the project root (the folder that contains main.py)
cd /path/to/Eightfold_Transformer

# 2. Create a virtual environment (required on modern macOS — see PEP 668 note below)
python3 -m venv venv

# 3. Activate the virtual environment (your prompt should show "(venv)")
source venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Run the test suite (191 tests; should all pass)
pytest
# or, quieter output:
pytest -q

# 6. When finished, deactivate the venv
deactivate
```

> **Why a virtual environment?** macOS ships with a system-managed Python. Recent versions enforce [PEP 668](https://peps.python.org/pep-0668/) ("externally managed environment"), which **blocks global `pip install`** with an error like *"externally-managed-environment"*. Always use a venv (`python3 -m venv venv` + `source venv/bin/activate`) before `pip install -r requirements.txt`. Do **not** use `sudo pip install` or `--break-system-packages` unless you know exactly why.

Dependencies are intentionally minimal — CSV/JSON/argparse/hashing all come from the standard library:

| Package | Why it's here |
|---|---|
| `pydantic` | Canonical/partial data models + typed structural schema validation. |
| `phonenumbers` | Deterministic, region-aware phone parsing → E.164. |
| `python-dateutil` | Robust deterministic date parsing → `YYYY-MM`. |
| `pdfplumber` | PDF resume text extraction *(for planned PDF ingestion; not yet wired in)*. |
| `python-docx` | DOCX resume text extraction *(for planned DOCX ingestion; not yet wired in)*. |
| `pytest` | Test suite. |

---

## Running the project

**Prerequisites:** you are in the **project root directory** (the folder containing `main.py`) **and** your virtual environment is activated (`source venv/bin/activate` — prompt shows `(venv)`).

### Default run (sample data)

```bash
python main.py --csv data/candidates.csv --resume data/resume.txt --config configs/default.json
```

Equivalent module form:

```bash
python -m eightfold_transformer.app --csv data/candidates.csv --resume data/resume.txt --config configs/default.json
```

### Custom projection config

```bash
python main.py --csv data/candidates.csv --resume data/resume.txt --config configs/example_custom_config.json
```

### Optional flags

```bash
# Write JSON to a file instead of stdout
python main.py --csv data/candidates.csv --resume data/resume.txt --config configs/default.json --out output.json

# Skip structural validation of projected output (validation is ON by default)
python main.py --csv data/candidates.csv --resume data/resume.txt --config configs/default.json --no-validate
```

**Flags:**

| Flag | Description |
|---|---|
| `--csv PATH` | Recruiter CSV source *(optional)*. |
| `--resume PATH` | Plain-text resume source *(optional)*. |
| `--config PATH` | Projection config JSON. Defaults to `configs/default.json` (then `configs/default_config.json`) at the repo root. |
| `--out PATH` | Write JSON output to a file instead of stdout. |
| `--no-validate` | Skip structural validation of projected output against the config-implied schema. Validation is **on by default**; a failure is fatal (exit `1`). |

At least one of `--csv` / `--resume` is required.

**I/O contract:**

- **stdout is pure JSON** — always a JSON array of projected objects (one per candidate; `[]` for zero candidates), ordered by `candidate_id` for input-order-independent determinism. Safe to pipe into `jq` or a file.
- **stderr carries the stage logs** (`INFO`/`WARNING`), so they never pollute the JSON. On a successful validated run you will see e.g. `validated 5 record(s)`.

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | Success, *or* a valid-but-empty result (`[]`). |
| `2` | Usage error (e.g. neither `--csv` nor `--resume` given). |
| `1` | Fatal runtime error (missing/malformed config, none of the provided sources readable, projection error, or **validation failure**). |

**Fail-soft behavior:** a provided `--csv`/`--resume` path that does not exist is a *soft* failure — it warns on stderr and continues with the remaining source. Only when *none* of the provided sources are readable is it fatal. Empty-but-readable files are valid and simply contribute zero records.

---

## Problem statement

Recruiting data arrives from many places at once: a recruiter's CSV export, a resume someone pasted in, an ATS dump, a GitHub profile, free-text notes. Each source is **heterogeneous** (different shapes), **dirty** (typos, junk values, inconsistent formats), and **overlapping** (the same person shows up several times, described slightly differently each time).

The job of this system is to turn that pile into **one clean canonical profile per real candidate**:

- a **fixed schema** so downstream consumers know exactly what they're getting,
- **normalized formats** (phones in E.164, dates as `YYYY-MM`, countries as ISO-3166 alpha-2, canonical skill names from a controlled vocabulary),
- **deduplicated** identities (the four "Priya Sharma" CSV rows and the resume collapse into one merged profile),
- **provenance** for every value (which source produced it, and how), and
- **confidence** for every value (how much you should trust it).

The guiding principle throughout is:

> **Wrong-but-confident is worse than honestly-empty.**

If we cannot determine a value, the field is `null` (or an empty list) — never invented. A `555` phone number that `phonenumbers` rejects as invalid is dropped rather than emitted as a plausible-looking lie.

On top of this, the "required twist": a **runtime projection config** reshapes the output — selecting, renaming, nesting, and toggling provenance/confidence — **with no code changes**.

### Constraints

- **Deterministic & explainable.** Same inputs → byte-identical output. Every emitted value is traceable to a source and an extraction method. No clocks, no randomness.
- **Robust / fail-soft.** A missing or garbage source never crashes the pipeline; unknown values become `null`, never fabricated. No traceback ever reaches the user.
- **Scales.** Designed to handle thousands of candidates with simple, transparent data structures.
- **No LLM / no ML / no network** in the core transform. Everything is rule-based, table-driven, and offline.

---

## Architecture

The pipeline is a straight line of deterministic stages, each isolated in its own package so it can be read, tested, and reasoned about independently:

```
detect → ingest/extract → normalize → merge/dedupe → confidence → project → validate
```

Three ideas hold the design together:

**1. The source-adapter pattern.** Each input source has its own *ingestion adapter* whose only job is to read raw values and stamp them with provenance — it never normalizes, merges, or validates. Today two adapters are implemented: a structured **recruiter CSV** reader and an unstructured **resume `.txt`** parser (regex/rule-based, no LLM). Adapters for ATS JSON, GitHub API, LinkedIn, and recruiter notes are scaffolded as placeholders (see [Future improvements](#future-improvements)). Adding a source is additive: write one adapter that emits `PartialRecord`s; the rest of the pipeline is untouched.

**2. Canonical record vs. projection.** The pipeline produces a single rich **canonical record** (`CanonicalCandidate`) that is the full truth — every field, every provenance trail, every confidence. *How that truth is presented* is a separate concern owned entirely by the **projection** layer, which is driven by a runtime config and is strictly read-only over the canonical record. This separation is what lets the same merged data be emitted in completely different shapes without touching any transform logic.

**3. Provenance travels with the value.** Rather than maintaining side-tables, every extracted value is wrapped in a generic `TrackedValue[T]` carrying `value` / `source` / `confidence` / `extraction_method`. The audit trail stays local to the data all the way through merge.

### Component / folder overview

| Package | Responsibility |
|---|---|
| `app/cli.py` | Thin I/O surface: argument parsing, stage orchestration, exit codes. **No business logic.** |
| `app/models/` | `schema.py` (canonical `CanonicalCandidate` + `TrackedValue`) and `partial.py` (per-source partial records). |
| `app/ingestion/` | Source adapters. Implemented: `csv_reader.py`, `resume_parser.py`. Placeholders: `ats_json.py`, `github_api.py`, `recruiter_notes.py`, `base.py`. |
| `app/normalization/` | Pure per-field normalizers: `phones.py`, `dates.py`, `skills.py`, `location.py`, `companies.py`. |
| `app/merger/` | `matcher.py` (identity clustering + blocking), `entity_resolution.py` (composite scoring), `resolver.py` (conflict resolution + experience dedupe), `confidence.py`, `provenance.py`, `__init__.py` (orchestration + `candidate_id`). |
| `app/projection/` | `config.py` (the config model/loader) and `projector.py` (config-driven output shaping). |
| `app/validation/` | Structural validation of projected output against the schema implied by the projection config (wired in CLI by default). |
| `configs/` | Runtime projection configs (`default.json`, `example_custom_config.json`) and skill vocabulary (`skills.json`). |
| `data/` | Sample inputs (`candidates.csv`, `resume.txt`). |

---

## Data flow diagram

```
                ┌──────────────────────────────────────────────────────────┐
  raw sources   │  recruiter CSV      resume .txt      [ATS / GitHub /       │
                │  (structured)       (unstructured)    notes — future]      │
                └─────────┬───────────────┬────────────────────┬────────────┘
                          │               │                    │
                          ▼               ▼                    ▼
                ┌──────────────────────────────────────────────────────────┐
  ingestion     │  Source adapters → PartialRecord[]                        │
  (extract)     │  raw capture only; each value wrapped in TrackedValue     │
                │  with {source, confidence, extraction_method}             │
                └─────────────────────────┬────────────────────────────────┘
                                          │
                                          ▼
                ┌──────────────────────────────────────────────────────────┐
  merge/dedupe  │  matcher: exact email/profile keys + name blocking        │
                │  entity_resolution: composite score (name, phone, company,  │
                │    title, resume corroboration) → union-find clusters       │
                │  resolver: per-field conflict ladder + list union/dedupe     │
                │    normalization runs HERE  ── phones → E.164              │
                │    (phones/dates/skills/country/companies)                 │
                │    experience dedupe by normalized company key               │
                └─────────────────────────┬────────────────────────────────┘
                                          │
                                          ▼
                ┌──────────────────────────────────────────────────────────┐
  confidence    │  per-field score = 0.5·source_trust                       │
                │                   + 0.3·agreement_ratio                    │
                │                   + 0.2·method_reliability                 │
                │  provenance: flattened {field, source, method} ledger      │
                └─────────────────────────┬────────────────────────────────┘
                                          │
                                          ▼
                ┌──────────────────────────────────────────────────────────┐
  canonical     │  CanonicalCandidate  (fixed schema, full-shaped)          │
  record        │  + per-value TrackedValue   + provenance[]                │
                │  + overall_confidence                                     │
                └─────────────────────────┬────────────────────────────────┘
                                          │
          projection config  ───────────▶│   (runtime JSON: fields, renames,
          (configs/*.json)                │    normalize toggles, on_missing,
                                          ▼    include_confidence/provenance)
                ┌──────────────────────────────────────────────────────────┐
  projection    │  projector: select / rename / nest / normalize            │
  (config-driven)│  read-only over the canonical record                     │
                └─────────────────────────┬────────────────────────────────┘
                                          │
                                          ▼
                ┌──────────────────────────────────────────────────────────┐
  validation    │  validate_projected(dict, config) — ON by default         │
                │  checks required fields, types, nested shape; fatal on fail │
                │  skip with --no-validate                                  │
                └─────────────────────────┬────────────────────────────────┘
                                          │
                                          ▼
                ┌──────────────────────────────────────────────────────────┐
  output        │  JSON array → stdout (or --out file); logs → stderr        │
                └──────────────────────────────────────────────────────────┘
```

The **runtime config plugs in at the projection stage**: the canonical record is computed identically every time, and the config decides only how it is shaped on the way out. **Validation runs after projection** (unless `--no-validate` is passed) so malformed output never silently ships.

---

## Sample input / output

### Input

`data/candidates.csv` (note the deliberately messy duplicates and junk):

```csv
name,email,phone,current_company,title
Priya Sharma,priya.sharma@gmail.com,(555) 123-4567,Acme Corp,Senior Backend Engineer
Priya Sharma,priya.sharma@gmail.com,+1-555-123-4567,Acme Corporation,Backend Engineer
Priya Sharma,priya.s@work.io,555.123.4567,Acme Corp,Staff Backend Engineer
P. Sharma,,5551234567,Acme Corporation,Engineer
Jordan Kim,jordan.kim@globex.com,call me,Globex Inc,Product Manager
...
```

`data/resume.txt` (excerpt):

```
Priya Sharma
Engineering Lead, Distributed Systems
Email: priya.sharma@gmail.com | Phone: +1 (555) 123.4567 | github.com/priyasharma

SKILLS
py, JS, Golang, k8s, Docker, PostgreSQL, Ninjutsu

EXPERIENCE
Acme Corporation - Engineering Lead
Jan 2019 - Mar 2023
  Built event-driven services and owned the on-call rotation for billing.
...
EDUCATION
B.S. Computer Science, State University, 2016
```

### Output — default config

The sample run ingests **8 CSV rows + 1 resume** (9 partial records) and merges them into **5 canonical candidates**.

All four "Priya Sharma" CSV variants **and** the resume collapse into **one** merged profile. Composite entity resolution (`entity_resolution.py`) scores pairs on normalized name, initials, email/phone overlap, **company normalization** (`Acme Corp` ≡ `Acme Corporation`), title overlap, and resume corroboration — so `P. Sharma` and the row with `priya.s@work.io` join the cluster even without a shared email on every pair.

Real CLI output for the merged Priya candidate (`configs/default.json`):

```json
{
  "candidate_id": "cand_086d12a7e164b775",
  "full_name": "Priya Sharma",
  "emails": ["priya.s@work.io", "priya.sharma@gmail.com"],
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
    { "institution": "State University", "degree": "B.S.", "field": "Computer Science", "end_year": 2016 }
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
  "links": { "linkedin": null, "github": "github.com/priyasharma", "portfolio": null, "other": [] }
}
```

Things worth pointing out, because they show the principles in action:

- **`phones` is empty.** Every phone in the sample data is a fictional `555` number that `phonenumbers` rejects as invalid, so they are *dropped* rather than emitted. Honestly-empty beats wrong-but-confident.
- **Skills are canonicalized** via `configs/skills.json` (`py → Python`, `JS → JavaScript`, `Golang → Go`, `k8s → Kubernetes`). Tokens **not** in the vocabulary (e.g. `Ninjutsu`) are **dropped at merge** — the gazetteer is a deliberate allow-list, not an open-ended parser.
- **Experience is deduped under the same employer.** Multiple CSV rows and resume entries for `Acme Corp` / `Acme Corporation` normalize to one company key and collapse into a single experience row (best title/dates/summary win per field).
- **Resume dates are parsed** to `YYYY-MM`; the year-only `2016 - 2018` becomes `2016-01`/`2018-01` (see the date tradeoff below).
- The default config doesn't list `confidence`/`provenance`, so they're omitted here — that's purely a projection choice; the data exists on the canonical record.

The full default-config run emits **5 candidates** total: merged Priya, two distinct Marcus Lee rows (different emails), Dana Patel, and Jordan Kim.

### Output — custom config (the runtime twist)

`configs/example_custom_config.json` renames fields, nests contact info, wraps scalar fields with confidence, and canonicalizes skill names:

```json
{
  "fields": [
    { "path": "name", "from": "full_name", "type": "string", "required": true, "normalize": false },
    { "path": "contact.phone", "from": "phones[0]", "type": "string", "required": false, "normalize": "E164" },
    { "path": "contact.email", "from": "emails[0]", "type": "string", "required": false, "normalize": false },
    { "path": "country", "from": "location.country", "type": "string", "required": false, "normalize": "country" },
    { "path": "skills", "from": "skills[].name", "type": "array", "required": false, "normalize": "canonical" }
  ],
  "include_confidence": true,
  "on_missing": "null"
}
```

Real projected output for the merged Priya candidate (trimmed):

```json
{
  "name": { "value": "Priya Sharma", "confidence": 0.85 },
  "contact": {
    "phone": null,
    "email": { "value": "priya.s@work.io", "confidence": 0.75 }
  },
  "country": null,
  "skills": ["Docker", "Go", "JavaScript", "Kubernetes", "PostgreSQL", "Python"]
}
```

`contact.phone` is `null` (no valid E.164 phone survived normalization). `on_missing: "null"` emits explicit `null` for missing nested fields rather than omitting keys. Same canonical data, completely different shape — no code changed.

---

## Design decisions

- **`TrackedValue[T]` as a provenance carrier.** A single generic wrapper (`value` / `source` / `confidence` / `extraction_method`) travels with every extracted value through ingestion and merge. Provenance is therefore *local to the data* rather than reconstructed from side-tables, and the top-level `provenance[]` ledger is just a flattened, denormalized view of those carriers.
- **Determinism by construction.** No `datetime.now()`, no RNG, no network. Dates use a **fixed anchor** (`2000-01-01`) so missing components are filled deterministically. The **source-trust** and **method-reliability** tables are hand-picked constants, not learned. Clusters, list fields, skills, and the provenance ledger are all **sorted**; output is ordered by `candidate_id`. The `candidate_id` is a stable SHA-256 over the strongest available match key.
- **Conservative-but-capable merging.** Exact email and GitHub/LinkedIn keys still union records immediately. For weaker signals, **name blocking** (`matcher.py`) limits comparisons to plausible pairs, and **composite entity resolution** (`entity_resolution.py`) scores name, initials, email/phone/company/title overlap plus resume corroboration. A high composite score can merge records that share no single strong key (e.g. `P. Sharma` + `priya.s@work.io` + shared Acme employer). Conflicting strong identifiers still require a higher override score before union.
- **Company normalization.** Legal suffixes and common variants (`Corp`, `Corporation`, `Inc`, …) are stripped/normalized so `Acme Corp` and `Acme Corporation` match for clustering and **experience dedupe**.
- **Skill vocabulary gate.** `configs/skills.json` maps aliases to canonical names; unknown tokens are dropped at merge so junk like `Ninjutsu` never pollutes output.
- **Confidence that punishes disagreement.** `field_confidence = clamp01(0.5·source_trust + 0.3·agreement_ratio + 0.2·method_reliability)`. The agreement ratio (sources backing the winner / sources that spoke) means a contested value is scored *lower*, exactly as it should be. `overall_confidence` is the mean of the present core identity fields.
- **Config-driven projection, no hardcoded output schema.** Every key in the output is declared in `config.fields`; nothing is implicit. The projector supports select, rename (`path` vs `from`), nested output paths (`contact.email`), canonical remaps (`emails[0]`, `skills[].name`), per-field `normalize` directives, an `on_missing` policy (`null` / `omit` / `error`), and `include_confidence` / `include_provenance` toggles — all while being strictly read-only over the canonical record.
- **Post-projection validation (on by default).** After projection, `validate_projected()` checks each emitted dict against the schema implied by the config (required fields present and non-null, declared types satisfied). Failures are fatal (exit `1`). Use `--no-validate` only when debugging a config.
- **Unknown → null, never invented.** Each normalizer returns `None` (or leaves the value unchanged, per its documented stage policy) on unrecognized input; unparseable phones and unmapped countries are dropped rather than guessed.

---

## Tradeoffs

Honest about where the design leans, and why:

- **Composite merging can still mis-merge or under-merge edge cases.** Richer signals help collapse obvious duplicates (`P. Sharma` + shared employer), but two people with similar names at the same company and no conflicting email could still merge incorrectly. The score thresholds and anti-over-merge guards bias toward safety, not perfection.
- **Year-only dates fabricate a month.** `2016 → 2016-01`. The schema mandates `YYYY-MM`, and a bare year can't be represented otherwise; month `01` is the conventional "start of year" choice. It's a small, documented deviation from "never invent" made to honor the schema contract.
- **Rule-based unstructured parsing is precise but brittle.** The resume parser is accurate on the expected section/line layout but won't gracefully handle arbitrarily formatted resumes. This is the deliberate cost of staying deterministic and LLM-free.
- **Skill allow-list drops unknown tokens.** `Ninjutsu` in the sample resume is intentionally filtered — better to omit than emit nonsense skills. Expanding `configs/skills.json` is the supported path for new domains.
- **Normalization runs *inside* merge.** Convenient (the merger needs normalized keys to dedupe), but it couples the two stages more tightly than a standalone normalize pass would.

---

## Future improvements

- **PDF/DOCX resume ingestion** via the already-listed `pdfplumber` / `python-docx` dependencies.
- **Flesh out the scaffolded adapters**: ATS JSON, GitHub API, LinkedIn, and recruiter notes (interfaces already stubbed under `app/ingestion/`).
- **Richer skills gazetteer and country table** (the current ones are deliberate seed sets in `configs/skills.json`).
- **Deterministic network ingestion** by caching GitHub API snapshots to disk, so an online source can feed the offline transform reproducibly.

---

## Project structure

```
Eightfold_Transformer/
├── main.py                      # entry-point shim → app.cli.main
├── requirements.txt
├── README.md
├── configs/
│   ├── default.json             # default projection config
│   ├── default_config.json      # legacy default (fallback)
│   ├── example_custom_config.json
│   └── skills.json              # skill alias → canonical name vocabulary
├── data/
│   ├── candidates.csv           # sample recruiter CSV (messy, overlapping)
│   └── resume.txt               # sample plain-text resume
├── eightfold_transformer/
│   └── app/
│       ├── cli.py               # CLI orchestration + exit codes
│       ├── __main__.py          # enables `python -m eightfold_transformer.app`
│       ├── models/              # schema.py (canonical + TrackedValue), partial.py
│       ├── ingestion/           # csv_reader.py, resume_parser.py (+ scaffolds)
│       ├── normalization/       # phones.py, dates.py, skills.py, location.py, companies.py
│       ├── merger/              # matcher.py, entity_resolution.py, resolver.py, …
│       ├── projection/          # config.py, projector.py
│       ├── validation/          # validator.py, schemas.py (projected output checks)
│       └── utils/               # shared deterministic helpers
└── tests/                       # pytest suite (191 tests)
```

### Testing

The project ships with a comprehensive, fully deterministic **pytest** suite (**191 tests**) spanning ingestion, normalization, merge, entity resolution, projection, validation, the CLI, and end-to-end acceptance (schema validity, provenance correctness, confidence scoring, and byte-for-byte determinism).

From the **project root directory**, with the venv activated:

```bash
pytest
# or
pytest -q
```

All tests are offline and deterministic — no network, no clock, no randomness — so they produce identical results on every run.

---

## Assumptions & explicitly descoped

- **Implemented sources:** recruiter CSV (`name,email,phone,current_company,title`) and plain-text resumes. ATS JSON, GitHub API, LinkedIn, and recruiter notes are *scaffolded but not implemented* — they are deliberately out of scope for this submission.
- **Phone default region is `IN`** (configurable in the normalizer), chosen to match the assignment's worked Indian-number example; numbers carrying an explicit `+<country code>` are parsed as-is.
- **Resume format** is assumed to follow recognizable section headers (`SUMMARY`, `SKILLS`, `EXPERIENCE`, `EDUCATION`). Arbitrary layouts are out of scope for the rule-based parser.
- **Skills** must appear in `configs/skills.json` (or the inline fallback set) to survive merge; unknown tokens are dropped by design.
