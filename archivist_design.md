# Archivist — Design Document v2

## Overview

Archivist is a Python CLI tool that scans old storage drives, triages their
contents using a local LLM, and guides recovery decisions through a structured
multi-phase pipeline. It produces a single CSV per drive that evolves through
all phases, organizes kept files into a coherent new directory structure, and
safely deletes the remainder with an explicit human approval gate before any
deletion occurs.

Runs entirely locally. No cloud dependencies. All LLM calls via Ollama.

---

## Goals

- One CSV per drive, evolving through all phases as the single source of truth
- Hierarchical review/decision propagation so you touch as few rows as possible
- Resumable at any phase — interruption loses no work
- Cross-drive deduplication via a persistent hash registry
- Read-only until Phase 6 — no files are moved or deleted until explicitly approved
- Safe delete with a mandatory human-reviewed manifest before any deletion

---

## Non-Goals

- No GUI (v1)
- No cloud API calls
- No OCR on scanned PDFs (plain text extraction only in v1)
- No automatic category override — LLM proposes, human decides

---

## CSV Schema

The CSV is the single source of truth. Columns are added as phases complete.
Rows exist for both files and directories.

```
Phase 1 adds:
  path            - full absolute path
  filename        - filename only (empty for directory rows)
  extension       - lowercase extension (empty for directories)
  is_dir          - True/False
  size_bytes      - 0 for directories
  modified        - ISO datetime
  md5_hash        - MD5 of file contents (empty for directories)
  is_duplicate    - True if hash seen before in this drive's scan
                    or in kept_hashes registry from a previous drive

Phase 2 adds:
  recommendation  - INTERESTING | REVIEW | SKIP
  confidence      - float 0.0–1.0
  comment         - one sentence from LLM explaining recommendation

Phase 3 (manual):
  review          - True | False | blank
  decision        - KEEP | DELETE | ARCHIVE | blank

Phase 4 adds:
  summary         - LLM-generated summary of file contents

Phase 5 adds:
  organized_path  - proposed target path within recovery folder

Phase 6 adds:
  copy_status     - COPIED | FAILED | SKIPPED
  delete_status   - DELETED | FAILED | PENDING | SKIPPED
```

---

## Hierarchy and Inheritance

### Directory rows
Phase 1 emits one row per directory in addition to one row per file.
Directory rows have `filename` empty and `is_dir=True`.
The LLM triages directory rows in Phase 2 exactly as it triages files.

### Review inheritance
At Phase 4 time, the effective `review` value for a row is resolved as:

1. If the row has an explicit `review` value → use it
2. Else walk up the path tree, find the nearest ancestor directory row
   with an explicit `review` value → inherit it
3. If no ancestor has an explicit value → treat as unreviewed, skip Phase 4

Child values always override parent values. A subdirectory or file with an
explicit value takes precedence over any ancestor, regardless of direction.

### Decision inheritance
`decision` resolves identically to `review`, with the same priority rules.
If a row has `decision` set (explicitly or inherited), it is considered final:
- No phase will overwrite it
- Phase 4 skips rows with `decision=DELETE` (inherited or explicit)
- Phase 6 copies rows with effective `decision=KEEP` or `decision=ARCHIVE`
- Phase 6c deletes rows with effective `decision=DELETE`

### Recommendation column
The LLM populates `recommendation` and `confidence` in Phase 2.
These columns inform your decisions but are never auto-applied to `review`
or `decision`. You set those manually.

### Precedence summary (highest to lowest)
```
decision (explicit on row)
decision (inherited from nearest ancestor)
review (explicit on row)
review (inherited from nearest ancestor)
recommendation (LLM suggestion, read-only)
```

---

## Phase Details

### Phase 1 — Scan

**Script:** `scanner.py`
**Input:** Drive letter or path (e.g. `D:\` or `/mnt/d/`)
**Output:** CSV with one row per file and one row per directory

**Behaviour:**
- Recursively walks the filesystem
- Emits a directory row before descending into each directory
- Computes MD5 hash of each file
- Checks hash against `kept_hashes.csv` registry — if found, marks
  `is_duplicate=True` with a comment noting the existing location
- Writes rows incrementally to CSV (not held in memory)
- If CSV already exists, resumes from last scanned path (skip already-seen rows)
- Applies exclude_dirs and exclude_extensions from config

**Default exclusions:**
```yaml
exclude_dirs:
  - "$RECYCLE.BIN"
  - "System Volume Information"
  - "Windows"
  - "Program Files"
  - "Program Files (x86)"
  - "ProgramData"
  - "AppData"

exclude_extensions:
  - .exe
  - .msi
  - .dll
  - .sys
  - .tmp
  - .log
  - .ini
  - .lnk
  - .url
  - .db
  - .cache
```

**Note:** excluded paths still get a directory row with `recommendation=SKIP,
confidence=0.99, comment="Excluded by config"` so you can see them and
override if needed.

---

### Phase 2 — Triage

**Script:** `triager.py`
**Input:** CSV from Phase 1
**Output:** CSV with `recommendation`, `confidence`, `comment` columns added

**Behaviour:**
- Reads all rows without a `recommendation` value
- Skips rows where `is_duplicate=True` — they automatically receive
  `recommendation=SKIP, confidence=0.99, comment="Duplicate of [hash]"`
- Groups remaining rows into batches of 50
- Sends each batch to the LLM as a single prompt
- Parses JSON response back to per-row values
- Writes results to CSV immediately after each batch (resumable)
- Directory rows are included in triage batches

**LLM prompt (per batch):**
```
You are reviewing files and directories from an old personal Windows computer.
For each item, provide:
  recommendation: INTERESTING | REVIEW | SKIP
  confidence: float 0.0 to 1.0
  comment: one sentence explaining your reasoning

Definitions:
  INTERESTING - likely unique personal content (financial docs, legal, creative
                work, personal projects, original writing, irreplaceable photos)
  REVIEW      - worth a closer look but uncertain (personal photos, mixed dirs,
                ambiguous filenames)
  SKIP        - generic, system, installer, or downloaded content unlikely to
                be unique

Base your assessment ONLY on: path, filename, extension, size_bytes, modified.
Do NOT hallucinate file contents.

Guidance:
- .docx, .pdf, .xlsx in user home directories → likely INTERESTING
- .jpg, .png, .psd, .raw, .tiff in personal folders → REVIEW (needs vision)
- .exe, .msi, .dll, .sys → SKIP
- Directories named Windows, Program Files, AppData → SKIP confidence 0.99
- Directories with personal project names, years, or proper nouns → INTERESTING
- Files in Downloads with generic names → SKIP
- Very small files (<1KB) outside project directories → SKIP

Respond ONLY with a valid JSON array. Each object must have keys:
path, recommendation, confidence, comment.
No preamble, no markdown, no explanation outside the JSON.

Items:
[JSON array of {path, filename, extension, is_dir, size_bytes, modified}]
```

**Error handling:**
- JSON parse failure → retry once with stricter prompt
- Second failure → mark entire batch as `recommendation=REVIEW,
  confidence=0.5, comment="LLM parse error — manual review needed"`

---

### Phase 3 — Manual Review

No code. Open the CSV in Excel, VS Code, or any spreadsheet tool.

**Recommended workflow:**
1. Sort by `recommendation` descending (INTERESTING first)
2. Filter `is_duplicate=False`
3. Filter `is_dir=True` first — set `decision=DELETE` on junk top-level
   directories (Windows, Program Files, etc.) to eliminate subtrees instantly
4. Set `decision=KEEP` or `decision=ARCHIVE` on valuable top-level directories
5. Handle exceptions at file or subdirectory level as needed
6. Rows left blank inherit from their nearest explicit ancestor

**Decision values:**
- `KEEP` — copy to organized recovery structure in Phase 6
- `ARCHIVE` — copy to recovery structure but in an /archive subfolder
- `DELETE` — exclude from recovery, include in delete manifest
- blank — inherit from parent directory

---

### Phase 4 — Summarize

**Script:** `summarizer.py`
**Input:** CSV after Phase 3
**Output:** CSV with `summary` column populated

**Behaviour:**
- Resolves effective `review` and `decision` for every row using inheritance
- Processes rows where effective review=True AND effective decision is not DELETE
- Skips rows that already have a `summary` value (resumable)
- Dispatches to the appropriate extractor based on extension
- Writes summary to CSV after each file

**Extractors:**
- `.docx` → `python-docx`
- `.pdf` → `pdfplumber` (text only, no OCR)
- `.xlsx` → `openpyxl` — sheet names + first 20 rows of each sheet
- `.txt`, `.md`, `.py`, `.js`, etc. → read directly
- `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`, `.raw`, `.psd` → vision LLM
- Unknown → attempt text read, fall back to metadata-only summary

All extracted text truncated to 2000 tokens before LLM call.

**Document summary prompt:**
```
Summarize this document in 2-3 sentences. Focus on:
- What the document is about
- Whether it appears personal/unique vs generic/downloaded
- Any notable names, dates, projects, or topics

[extracted text]
```

**Image summary prompt (vision):**
```
Describe this image in 1-2 sentences. Include:
- What is shown (people, places, objects, scenes)
- Approximate era if determinable from content or image quality
- Whether it appears personal (family, event, selfie) vs generic (stock, meme, download)
```

**Error handling:**
- Unreadable file → `summary = "UNREADABLE: [error message]"`
- LLM failure → `summary = "LLM_ERROR: [error message]"`
- Both allow Phase 5/6 to proceed — human can review these rows manually

---

### Phase 5 — Organize

**Script:** `organizer.py`
**Input:** CSV after Phase 4 (all summaries complete)
**Output:** CSV with `organized_path` column populated; proposed taxonomy printed

**Behaviour:**
- Collects all rows where effective decision=KEEP or ARCHIVE and summary is populated
- Sends all summaries to the LLM in one prompt to propose a taxonomy
- LLM returns a proposed top-level category structure
- Second LLM pass assigns each file to a category and proposes a target path
- Writes `organized_path` to CSV for each KEEP/ARCHIVE file
- Prints the proposed taxonomy to stdout for human review before Phase 6

**Taxonomy prompt (all summaries at once):**
```
You are organizing recovered files from an old personal computer into a clean
directory structure. Below are summaries of all files to be kept.

Propose a top-level category taxonomy (5-12 categories) that reflects the
actual content. Do not use generic categories like "Miscellaneous" unless
truly necessary. Base categories on what you actually see in the summaries.

Then assign each file to a category and propose a relative path within that
category. Paths should be meaningful, not just flat dumps.

Recovery root: recovered/

Respond with:
1. A taxonomy block listing each category and a one-line description
2. A JSON array where each object has: original_path, organized_path

[all summaries as JSON array: {path, filename, summary}]
```

**Human review:**
Phase 5 outputs the taxonomy and a preview of the organized structure but
does NOT write any files. You review the proposed `organized_path` values
in the CSV and can edit them before running Phase 6.

---

### Phase 6 — Execute

**Script:** `executor.py`
**Input:** CSV after Phase 5
**Three explicit sub-commands:**

#### Phase 6a — Copy
```bash
python archivist.py copy --csv reports/drive_d.csv --dest E:\recovered\
```
- Copies all files with effective decision=KEEP or ARCHIVE to their
  `organized_path` under the destination root
- Creates directory structure as needed
- Verifies each copy (size + hash check)
- Writes `copy_status=COPIED` or `copy_status=FAILED` to CSV
- Skips rows already marked COPIED (resumable)
- Does NOT delete anything

#### Phase 6b — Delete manifest
```bash
python archivist.py manifest --csv reports/drive_d.csv
```
- Resolves effective decision for every row using inheritance
- Collects all rows with effective decision=DELETE
- Writes `delete_manifest.csv` with columns:
  path, filename, size_bytes, decision_source (EXPLICIT or INHERITED from [path])
- Prints summary: total files, total size, breakdown by decision_source
- Does NOT delete anything
- You review this file before proceeding

#### Phase 6c — Delete
```bash
python archivist.py delete --csv reports/drive_d.csv --manifest reports/delete_manifest.csv --confirm
```
- Requires `--confirm` flag — will not run without it
- Requires the manifest CSV to exist and match the current CSV state
- Deletes every file in the manifest
- Writes `delete_status=DELETED` or `delete_status=FAILED` to CSV
- Skips directories (rmdir only if empty after file deletions)
- Logs every deletion to a timestamped log file

---

## Cross-Drive Deduplication

### kept_hashes.csv
After Phase 6a completes for a drive, hashes of all successfully copied files
are appended to a global `kept_hashes.csv`:

```
md5_hash, original_path, organized_path, drive, date_copied
```

This file lives in the archivist project directory, not in any drive.

### How it's used
When Phase 1 scans a new drive, it checks each file's MD5 against
`kept_hashes.csv`. Matches are marked `is_duplicate=True` with a comment:
`"Already kept from [original_path] → [organized_path]"`

These files automatically receive `recommendation=SKIP, confidence=0.99`
in Phase 2 and require no further processing unless you explicitly override.

---

## CLI Interface

```bash
# Phase 1: scan
python archivist.py scan --drive D:\ --output reports/drive_d.csv

# Phase 2: triage
python archivist.py triage --csv reports/drive_d.csv

# Phase 4: summarize
python archivist.py summarize --csv reports/drive_d.csv

# Phase 5: organize (propose taxonomy)
python archivist.py organize --csv reports/drive_d.csv

# Phase 6a: copy keeps to recovery folder
python archivist.py copy --csv reports/drive_d.csv --dest E:\recovered\

# Phase 6b: generate delete manifest
python archivist.py manifest --csv reports/drive_d.csv

# Phase 6c: delete (requires --confirm)
python archivist.py delete --csv reports/drive_d.csv --manifest reports/delete_manifest.csv --confirm

# Resolve and preview effective review/decision for any row
python archivist.py resolve --csv reports/drive_d.csv --path "D:\Users\Stephen\Documents"
```

---

## Configuration

```yaml
# config.yaml

# Ollama
ollama_base_url: "http://localhost:11434"
triage_model: "phi4:14b"
summarize_model: "phi4:14b"
vision_model: "phi4:14b"

# Performance
triage_batch_size: 50
max_text_tokens: 2000

# Cross-drive deduplication registry
kept_hashes_path: "kept_hashes.csv"

# Recovery destination root
recovery_root: "E:\\recovered"

# Scan exclusions
exclude_dirs:
  - "$RECYCLE.BIN"
  - "System Volume Information"
  - "Windows"
  - "Program Files"
  - "Program Files (x86)"
  - "ProgramData"
  - "AppData"

exclude_extensions:
  - .exe
  - .msi
  - .dll
  - .sys
  - .tmp
  - .log
  - .ini
  - .lnk
  - .url
  - .db
  - .cache
```

---

## Dependencies

```
python-docx       # .docx text extraction
pdfplumber        # .pdf text extraction
openpyxl          # .xlsx extraction
requests          # Ollama HTTP API
pyyaml            # config parsing
tqdm              # progress bars
pandas            # CSV manipulation
Pillow            # image loading for vision pass
```

---

## Resumability

Every phase is safe to interrupt and restart:

- **Phase 1:** skips paths already in CSV
- **Phase 2:** skips rows with existing recommendation
- **Phase 4:** skips rows with existing summary
- **Phase 5:** skips rows with existing organized_path
- **Phase 6a:** skips rows with copy_status=COPIED
- **Phase 6c:** skips rows with delete_status=DELETED

The CSV is written incrementally after each batch or file, never held
entirely in memory.

---

## Project Structure

```
archivist/
├── archivist.py        # CLI entry point and phase orchestration
├── scanner.py          # Phase 1
├── triager.py          # Phase 2
├── summarizer.py       # Phase 4
├── organizer.py        # Phase 5
├── executor.py         # Phase 6 (copy, manifest, delete)
├── extractor.py        # Text and image extraction utilities
├── inheritance.py      # Hierarchy resolution logic (review/decision propagation)
├── config.yaml         # Runtime configuration
├── kept_hashes.csv     # Cross-drive deduplication registry (grows over time)
├── requirements.txt
└── reports/            # One CSV per drive session
```

---

## Test Drive

The 500GB WD Blue (already mounted as a Windows drive letter) is the test
drive for v1. Run all six phases on it before touching the SATA drives via
USB reader.

Expected Phase 1 time: 5-15 minutes
Expected Phase 2 time: ~17 minutes for 10k files at 50/batch, 5s/call
Expected Phase 4 time: depends on review=True count and file types
Expected Phase 6a time: depends on total size of KEEP files

---

## Future Enhancements (out of scope for v1)

- OCR for scanned PDFs via tesseract
- Web UI for Phase 3 review instead of editing CSV directly
- Semantic clustering of document summaries before Phase 5
- Duplicate grouping view in Phase 3
- Auto-suggest decision based on recommendation + confidence threshold
- Support for Mac HFS+ and Linux ext4 drives via WSL2
