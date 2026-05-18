# Archivist

Archivist is a local-first Python CLI for recovering files from old drives.
It scans a drive into a CSV, uses a local Ollama model to triage and summarize
files, proposes an organized recovery structure, copies the files you want to
keep, and can generate a delete manifest for anything marked for removal.

The CSV is the working record for the pipeline. You can stop and resume runs
without losing progress.

## Command Sequence

```bash
# ── One-time baseline setup ──────────────────────────────────────────────────
python archivist.py scan --drive /mnt/c/ --output reports/drive_c.csv
# Set  baseline_scan_csv: "reports/drive_c.csv"  in config.yaml

# ── Per peripheral drive (repeat for each) ───────────────────────────────────

# Phase 1 — scan (resumable; Ctrl+C safe)
python archivist.py scan --drive /mnt/d/ --output reports/drive_d.csv

# Phase 2a — auto-mark duplicates (instant, no LLM)
#   Files with an exact copy on C: are auto-marked decision=DELETE.
#   Review the CSV here before committing LLM time to Phase 2b.
python archivist.py mark-duplicates --csv reports/drive_d.csv

# Phase 2b — LLM triage of remaining files  [requires Ollama]
python archivist.py triage --csv reports/drive_d.csv

# Phase 3 — manual review (no command)
#   Open the CSV in a spreadsheet. Set review=True and
#   decision=KEEP / ARCHIVE / DELETE on files and directories.
#   Decisions on a directory row inherit to all files beneath it.

# Phase 4 — summarize reviewed files  [requires Ollama]
python archivist.py summarize --csv reports/drive_d.csv

# Phase 5 — propose organized_path values  [requires Ollama]
python archivist.py organize --csv reports/drive_d.csv

# Phase 6a — copy kept files to recovery destination
python archivist.py copy --csv reports/drive_d.csv --dest /path/to/recovered/

# Phase 6b — generate delete manifest (review before deleting)
python archivist.py manifest --csv reports/drive_d.csv

# Phase 6c — delete (requires --confirm; validates manifest first)
python archivist.py delete \
  --csv reports/drive_d.csv \
  --manifest reports/delete_manifest.csv \
  --confirm
```

## Current Capabilities

- Phase 1 scan: recursive scan, incremental CSV writes, same-drive and
  cross-drive duplicate detection, exclusion rules, inaccessible-path records,
  and resume support.
- Phase 2 triage: batched local LLM recommendations written back into the CSV,
  with duplicate rows auto-marked as `SKIP`.
- Phase 3 manual review: edit `review` and `decision` columns directly in the
  CSV.
- Phase 4 summarize: inheritance-aware summarization for reviewed, non-deleted
  files using file-type-specific extraction.
- Phase 5 organize: proposes a taxonomy and writes `organized_path` values for
  files with summaries and effective `KEEP` or `ARCHIVE` decisions.
- Phase 6 copy/manifest/delete commands: copies kept files, writes a delete
  manifest, and deletes files from a supplied manifest when explicitly
  confirmed.
- Baseline duplicate reporting: exports a combined report of rows on D/E that
  are redundant with the C baseline scan.

## Scope Of This README

This README describes the behavior that exists in the current codebase and is
covered by the current tests. It is not a restatement of the broader design
document.

## Requirements

- Python 3.11+
- Ollama running locally
- The models configured in `config.yaml` pulled locally

## Installation

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Start Ollama if it is not already running:

```bash
ollama serve
```

Pull the configured models:

```bash
ollama pull phi4:14b
ollama pull llava:13b
```

## Configuration

Default configuration lives in `config.yaml`.

Important settings:

- `ollama_base_url`: local Ollama endpoint.
- `triage_model`: model used for Phase 2 triage.
- `summarize_model`: model used for text summarization.
- `vision_model`: vision-capable model used for image summarization.
- `estimate_scan_progress`: when true, scan performs a quick pre-count pass so the progress bar can show totals and ETA.
- `kept_hashes_path`: registry used for cross-drive duplicate detection.
- `baseline_scan_csv`: optional scan CSV to treat as an authoritative duplicate baseline.
- `max_workers`: concurrent LLM calls for triage and summarize (default `1`). Set to `3`–`4` alongside `OLLAMA_NUM_PARALLEL` for faster throughput on GPU hardware.
- `exclude_dirs`: directory names pruned during scan.
- `exclude_extensions`: file extensions skipped during scan.

## Typical Workflow

### 1. Scan a drive

From WSL, prefer Linux-style mount paths:

```bash
python archivist.py scan --drive /mnt/d/ --output reports/drive_d.csv
```

From native Windows Python, use a quoted drive path:

```powershell
python archivist.py scan --drive "D:\" --output reports/drive_d.csv
```

Notes:

- The scan progress display includes `Ctrl+C to cancel; rerun to resume`.
- By default, scan starts with a quick counting pass and then shows an estimated total and ETA.
- If you want scan to start immediately without the extra counting pass, set `estimate_scan_progress: false` in `config.yaml`.
- If you interrupt the scan, rerun the same command and Archivist will append
  only unseen rows.
- Inaccessible directories are recorded in the CSV with an `INACCESSIBLE:`
  comment so you can review what was skipped.

Optional C-baseline cleanup pass:

1. Scan C first:

```bash
python archivist.py scan --drive /mnt/c/ --output reports/drive_c.csv
```

2. Set `baseline_scan_csv: "reports/drive_c.csv"` in `config.yaml`.

3. Scan D or E normally. Files whose hashes match the C scan will be marked as
   duplicates with `duplicate_kind=baseline_scan` and `duplicate_source_path`
   filled in.

### 2a. Auto-mark duplicates (no LLM)

```bash
python archivist.py mark-duplicates --csv reports/drive_d.csv
```

Applies duplicate rules to the CSV without any LLM calls:

- All duplicate rows get `recommendation=SKIP`.
- Rows where `duplicate_kind=baseline_scan` (exact copy found on C:) get
  `decision=DELETE` automatically.

Run this before triage so you can review auto-deletions in the CSV and confirm
the baseline matches look right before committing any LLM time.

### 2b. Triage remaining files with the LLM

```bash
python archivist.py triage --csv reports/drive_d.csv
```

Fills in `recommendation`, `confidence`, and `comment` for rows that do not
already have a recommendation. Rows already marked by `mark-duplicates` are
skipped entirely.

To run triage faster on GPU hardware, set `max_workers: 3` in `config.yaml`
and start Ollama with `OLLAMA_NUM_PARALLEL=3 ollama serve`.

Triage, summarize, copy, and delete use progress bars with known totals,
so `tqdm` shows completed work, rate, and ETA automatically.

### 3. Manually review the CSV

Open the CSV in a spreadsheet tool or VS Code and set:

- `review=True` for rows that should be summarized.
- `decision=KEEP`, `ARCHIVE`, or `DELETE` for rows that should drive later
  actions.

For a C-baseline cleanup pass, review rows where `duplicate_kind=baseline_scan`
before generating a delete manifest.

Inheritance is supported, so setting a directory row can affect an entire
subtree.

### 4. Summarize reviewed files

```bash
python archivist.py summarize --csv reports/drive_d.csv
```

### 5. Propose an organized recovery structure

```bash
python archivist.py organize --csv reports/drive_d.csv
```

This prints a proposed taxonomy and writes `organized_path` values back into
the CSV. The command now shows a 2-step progress bar so the user can see when
taxonomy generation is complete and when file assignment generation is running.

### 6. Copy the files you want to keep

```bash
python archivist.py copy --csv reports/drive_d.csv --dest /path/to/recovered
```

Copied files are verified and successful hashes are appended to
`kept_hashes.csv`.

### 7. Generate a delete manifest

```bash
python archivist.py manifest --csv reports/drive_d.csv
```

Review `reports/delete_manifest.csv` before deleting anything.

If you want per-drive manifests for multiple CSVs in one step, use:

```bash
python archivist.py manifest-all \
  --csv reports/drive_d.csv \
  --csv reports/drive_e.csv \
  --output-dir reports/manifests
```

This writes unique files such as `drive_d_delete_manifest.csv` and
`drive_e_delete_manifest.csv` so they do not overwrite each other.

### Optional: Export a combined baseline-duplicate report

```bash
python archivist.py duplicates-report \
  --csv reports/drive_d.csv \
  --csv reports/drive_e.csv \
  --output reports/baseline_duplicates.csv
```

This writes a derived CSV containing only rows where `duplicate_kind=baseline_scan`.
It is useful for reviewing everything on D and E that appears redundant with C
before generating per-drive delete manifests.

### 8. Delete files from the reviewed manifest

```bash
python archivist.py delete --csv reports/drive_d.csv --manifest reports/delete_manifest.csv --confirm
```

The current implementation requires `--confirm` and a manifest path. It
validates that the manifest still matches the current CSV state and aborts if
a mismatch is detected.

## Other Commands

Resolve effective inherited values for a specific path:

```bash
python archivist.py resolve --csv reports/drive_d.csv --path "/mnt/d/Users/Stephen/Documents/file.txt"
```

Export a combined duplicate report for one or more drive CSVs:

```bash
python archivist.py duplicates-report --csv reports/drive_d.csv --csv reports/drive_e.csv --output reports/baseline_duplicates.csv
```

## Development

Run tests:

```bash
pytest -q
```

## Operational Notes

- On WSL, `/mnt/d/` is usually more reliable than `D:\` because shell escaping
  is simpler.
- Permission denied errors during scan are common on protected Windows system
  folders. Those paths are now recorded in the CSV rather than disappearing
  into logs alone.
- The tool is intended to run locally. There are no cloud API dependencies.