# Archivist

Archivist is a local-first Python CLI for recovering files from old drives.
It scans a drive into a CSV, uses a local Ollama model to triage and summarize
files, proposes an organized recovery structure, copies the files you want to
keep, and can generate a delete manifest for anything marked for removal.

The CSV is the working record for the pipeline. You can stop and resume runs
without losing progress.

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
- `kept_hashes_path`: registry used for cross-drive duplicate detection.
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
- If you interrupt the scan, rerun the same command and Archivist will append
  only unseen rows.
- Inaccessible directories are recorded in the CSV with an `INACCESSIBLE:`
  comment so you can review what was skipped.

### 2. Triage the scan with the LLM

```bash
python archivist.py triage --csv reports/drive_d.csv
```

This fills in `recommendation`, `confidence`, and `comment` for rows that do
not already have a recommendation. Rows marked as duplicates are filled without
an LLM call.

### 3. Manually review the CSV

Open the CSV in a spreadsheet tool or VS Code and set:

- `review=True` for rows that should be summarized.
- `decision=KEEP`, `ARCHIVE`, or `DELETE` for rows that should drive later
  actions.

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
the CSV.

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

## Development

Run tests:

```bash
pytest -q
```

Current test status in this workspace: 46 passing.

## Operational Notes

- On WSL, `/mnt/d/` is usually more reliable than `D:\` because shell escaping
  is simpler.
- Permission denied errors during scan are common on protected Windows system
  folders. Those paths are now recorded in the CSV rather than disappearing
  into logs alone.
- The tool is intended to run locally. There are no cloud API dependencies.