# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Archivist is a local-first Python CLI for recovering and organizing files from old storage drives. It implements a 6-phase pipeline that scans drives, triages their contents using a local LLM (Ollama), guides manual recovery decisions through a CSV interface, and safely copies/deletes files based on explicit human approval.

Key constraint: The tool is read-only until Phase 6 — no files are moved or deleted until explicitly approved by the user after review.

## Tech Stack

- Language: Python 3.11+
- Core dependencies: pandas, requests, pyyaml, tqdm
- Document extraction: python-docx, pdfplumber, openpyxl, Pillow
- LLM backend: Ollama (local, HTTP-based)
- Testing: pytest
- No cloud APIs — entirely local execution

## Quick Start

Setup:
```
python -m venv .venv
.venv\Scripts\Activate.ps1   # PowerShell (Windows)
pip install -r requirements.txt
```

Run Tests:
```
pytest -q                    # Quick run
pytest -v                    # Verbose
pytest tests/test_scanner.py # Single test file
pytest -k test_scan_writes_csv -v  # Single test by name
```

Build/Install:
```
pip install -e .             # Install in development mode (editable)
```

Interactive wizard (recommended starting point):
```
python wizard.py
```
The wizard detects the current phase of a CSV, shows a numbered menu, prompts for required inputs, and calls archivist.py. Use it when you're not sure which command to run next.

Key Commands (main entry point is archivist.py):

```
# Phase 1: Scan a drive
python archivist.py scan --drive D:\ --output reports/drive_d.csv

# Phase 2a: Auto-mark duplicates (no LLM — run before triage to review auto-deletions first)
python archivist.py mark-duplicates --csv reports/drive_d.csv

# Phase 2b: Triage with LLM (skips rows already marked by mark-duplicates)
python archivist.py triage --csv reports/drive_d.csv

# Phase 4: Summarize reviewed files
python archivist.py summarize --csv reports/drive_d.csv

# Phase 5: Propose organization taxonomy
python archivist.py organize --csv reports/drive_d.csv

# Phase 6a: Copy kept files to recovery destination
python archivist.py copy --csv reports/drive_d.csv --dest E:\recovered\

# Phase 6b: Generate delete manifest (can cover multiple drives in one file)
python archivist.py manifest --csv reports/drive_d.csv --csv reports/drive_e.csv --output reports/delete_manifest.csv

# Phase 6b (per-drive): Generate one manifest file per drive instead
python archivist.py manifest-all --csv reports/drive_d.csv --csv reports/drive_e.csv --output-dir reports/

# Phase 6c: Delete files (pass all CSVs covered by the manifest)
python archivist.py delete --csv reports/drive_d.csv --csv reports/drive_e.csv --manifest reports/delete_manifest.csv --confirm

# Utility: Resolve effective review/decision for a path
python archivist.py resolve --csv reports/drive_d.csv --path "/mnt/d/Users/Stephen/Documents"

# Utility: Generate combined baseline-duplicate report
python archivist.py duplicates-report --csv reports/drive_d.csv --csv reports/drive_e.csv --output reports/baseline_duplicates.csv
```

## Architecture & Data Flow

### The CSV as Single Source of Truth

Each drive scan produces one CSV that evolves through all phases. This is the only persistent state — no database, no temporary files except .tmp during writes.

CSV columns (grow as phases complete):
- Phase 1: path, filename, extension, is_dir, size_bytes, modified, md5_hash, is_duplicate, duplicate_kind, duplicate_source_path
- Phase 2 (triage): adds recommendation, confidence, comment
- Phase 3 (manual): user sets review and decision columns directly in the CSV
- Phase 4 (summarize): adds summary
- Phase 5 (organize): adds organized_path
- Phase 6 (execute): adds copy_status, delete_status

### Module Organization

The codebase is organized by pipeline phase + utilities:

- **archivist.py** — CLI orchestration, argument parsing, phase dispatch
- **scanner.py** (Phase 1) — Recursive filesystem walk, MD5 hashing, resume logic, cross-drive dedup registry
- **triager.py** (Phase 2a/2b) — `run_mark_duplicates` auto-marks without LLM; `run_triage` batch LLM calls with JSON retry logic; run 2a first so baseline/kept-hash deletions are reviewable before LLM sees them
- **summarizer.py** (Phase 4) — Extract text/images from files, dispatch to type-specific extractors, generate LLM summaries
- **organizer.py** (Phase 5) — Two-pass LLM: propose taxonomy → assign files to categories
- **executor.py** (Phase 6) — Copy files with verification, generate delete manifest, delete with safety checks
- **inheritance.py** — Resolve effective review and decision values via directory hierarchy (parent→child)
- **extractor.py** — File type handlers: .docx, .pdf, .xlsx, images, plain text (returns content + type)
- **llm_client.py** — Ollama HTTP API wrapper, model validation, streaming support
- **csv_utils.py** — Safe atomic CSV writes with retry logic (prevents corruption if file is open in Excel)
- **reporter.py** — Generate baseline-duplicate reports for cross-drive analysis

### Inheritance & Decision Propagation

The key pattern for handling directory hierarchies:

- `resolve_effective(df, row_path, "decision")` walks the path tree upward to find the nearest ancestor with an explicit decision
- Child values always override parent values
- Used in Phase 4 to skip summarization of deleted subtrees, Phase 6a/6c to determine which files to copy/delete
- `resolve_all(df, column)` — bulk O(n * depth) resolution returning a Series; single-column
- `resolve_all_multi(df, columns)` — resolves multiple columns in one pass sharing path normalization; use when you need both `review` and `decision` at once
- `resolve_to_set(df, column, target_value)` — returns the set of paths whose effective value equals `target_value`; used by executor to build the delete set without allocating an intermediate Series

### Resumability

Every phase is interruptible. On resume:
- Phase 1: Skip paths already in CSV (tracked via `_load_seen_paths()`)
- Phase 2: Skip rows with existing recommendation
- Phase 4: Skip rows with existing summary
- Phase 5: Skip rows with existing organized_path
- Phase 6a: Skip rows with copy_status=COPIED
- Phase 6c: Skip rows with delete_status=DELETED

CSV writes are incremental, never held entirely in memory.

### Cross-Drive Deduplication

After Phase 6a completes, successfully copied file hashes are appended to kept_hashes.csv (global registry). When scanning a new drive, Phase 1 checks each file's MD5 against this registry; matches are marked is_duplicate=True and receive recommendation=SKIP, confidence=0.99 automatically in Phase 2.

Optional: Set baseline_scan_csv in config.yaml to treat a previous drive scan as an authoritative baseline; files matching its hashes are marked with duplicate_kind=baseline_scan and auto-decision=DELETE for easy cleanup review.

## Configuration

See config.yaml:
- **Ollama settings:** ollama_base_url, triage_model, summarize_model, vision_model
- **Performance:** triage_batch_size (default 8), max_text_tokens (default 2000)
- **Registries:** kept_hashes_path, optional baseline_scan_csv
- **Exclusions:** exclude_dirs, exclude_extensions (applied during Phase 1 scan)
- **Debug flags (undocumented):** triage_debug_print_prompt, triage_debug_stream_output, estimate_scan_progress

## Testing Strategy

Tests cover:
- scanner.py — CSV write, resume logic, exclusion handling, hash detection
- triager.py — Batch prompt building, JSON parsing, duplicate marking
- organizer.py — Taxonomy + assignment prompts, decision filtering
- executor.py — Copy verification, manifest generation, delete safety
- inheritance.py — Path resolution, boundary cases (blank values, missing parents)
- extractor.py — Content extraction for each file type
- reporter.py — Filtering and combining duplicate rows
- csv_utils.py — Atomic write logic

Common patterns:
- Fixtures use tmp_path for temporary CSVs and test directories
- Tests verify CSV structure, data integrity, and phase invariants
- No mocking of Ollama (would require network stub)

## Important Patterns & Conventions

1. **String normalization:** Paths are normalized using PureWindowsPath to handle both / and \ separators and case-insensitivity. See inheritance._normalize().

2. **Blank value handling:** is_blank() in inheritance.py checks for None, nan, and empty strings. This is used throughout to distinguish "explicitly unset" from "set to a value".

3. **Safe CSV writes:** All CSV mutations use safe_write_csv() which writes to a .tmp file and atomically renames. If the file is open in Excel, it retries 3 times before saving to .pending and raising a clear error.

4. **Progress bars:** tqdm is used throughout. Phase 5 uses a 2-step progress bar to show taxonomy generation then assignment. Phase 1 optionally estimates remaining work with a pre-count pass.

5. **Error handling in LLM calls:** Phase 2 triage retries once with a stricter prompt if JSON parsing fails. Phase 4 summarization fails gracefully per-file (marks summary="UNREADABLE: [error]" or "LLM_ERROR: [error]") and continues.

6. **Phase 3 is manual:** No code for Phase 3 — the user opens the CSV in a spreadsheet tool and sets review and decision columns. The tool provides a resolve command to preview inheritance effects before committing.

7. **Delete manifest safety check:** `run_delete` validates that the manifest exactly matches the current CSV's effective DELETE set before touching any files. If the CSV was edited after the manifest was generated, the run is aborted with a clear error. Always regenerate the manifest after any CSV edits.

8. **Delete log:** Phase 6c writes a timestamped log (`delete_log_YYYYMMDD_HHMMSS.log`) next to the CSV recording every DELETED, ALREADY_GONE, RMTREE, and FAILED operation. Whole-subtree deletions via `shutil.rmtree` are attempted first (for directories whose entire contents are marked DELETE with no exceptions beneath them), then individual files.

## Development Notes

- The config.yaml file is required at runtime; missing config causes early exit
- Ollama must be running and reachable at the configured ollama_base_url
- Required models must be pre-pulled (e.g., ollama pull phi4:14b)
- File I/O is intentionally synchronous (no asyncio) to keep the pipeline simple and debuggable
- Vision model for images and complex PDFs can differ from text model (e.g., vision_model: llava:13b)
- The tool logs to WARNING level by default; set DEBUG=True in env for verbose output
