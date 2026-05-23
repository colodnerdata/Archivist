"""Interactive CLI wizard for Archivist.

Run with:  python wizard.py
"""

import os
import subprocess
import sys

import pandas as pd
import yaml


def _load_config() -> dict:
    try:
        with open("config.yaml", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _run(args: list[str]) -> None:
    cmd = [sys.executable, "archivist.py"] + args
    print(f"\n  $ {' '.join(cmd)}\n")
    subprocess.run(cmd)


def _ask(prompt: str, default: str = "") -> str:
    display = f"{prompt} [{default}]: " if default else f"{prompt}: "
    val = input(display).strip()
    return val if val else default


def _confirm(prompt: str) -> bool:
    return input(f"\n  {prompt} [y/N] ").strip().lower() == "y"


# ---------------------------------------------------------------------------
# Phase status detection
# ---------------------------------------------------------------------------

def _phase_status(csv_path: str) -> list[tuple[str, str]]:
    """Return list of (phase_label, status_string) for display."""
    if not os.path.exists(csv_path):
        return []

    df = pd.read_csv(csv_path, dtype=str)
    cols = set(df.columns)
    n_total = len(df)

    if "is_dir" in cols:
        files = df[df["is_dir"].str.lower() != "true"]
    else:
        files = df
    n_files = len(files)

    def _pct_filled(col: str, series: "pd.Series") -> tuple[int, int]:
        filled = series[col].notna() & (series[col].str.strip().str.len() > 0)
        return int(filled.sum()), len(series)

    rows = []

    # Phase 1
    rows.append(("Phase 1  Scan", f"done  ({n_total:,} rows)" if "path" in cols else "not started"))

    # Phase 2a/2b — recommendation column presence and fill rate
    if "recommendation" in cols:
        n, tot = _pct_filled("recommendation", files)
        if n == tot:
            rows.append(("Phase 2  Triage", f"done  ({n:,} files triaged)"))
        elif n > 0:
            rows.append(("Phase 2  Triage", f"in progress  ({n:,}/{tot:,})"))
        else:
            rows.append(("Phase 2  Triage", "column exists but empty — run mark-duplicates then triage"))
    else:
        rows.append(("Phase 2  Triage", "not started"))

    # Phase 3 — manual review
    if "decision" in cols:
        n, tot = _pct_filled("decision", files)
        rows.append(("Phase 3  Manual review", f"{n:,}/{tot:,} decisions set"))
    else:
        rows.append(("Phase 3  Manual review", "no decisions set yet  (open CSV in Excel/Sheets)"))

    # Phase 4 — summarize
    if "summary" in cols:
        n, tot = _pct_filled("summary", files)
        label = "done" if n >= tot * 0.9 else "in progress"
        rows.append(("Phase 4  Summarize", f"{label}  ({n:,}/{tot:,})"))
    else:
        rows.append(("Phase 4  Summarize", "not started"))

    # Phase 5 — organize
    if "organized_path" in cols:
        filled = (
            files["organized_path"].notna()
            & (files["organized_path"].str.strip().str.len() > 0)
            & (files["organized_path"].str.lower() != "nan")
        )
        n = int(filled.sum())
        tot = n_files
        label = "done" if n >= tot * 0.9 else "in progress"
        rows.append(("Phase 5  Organize", f"{label}  ({n:,}/{tot:,})"))
    else:
        rows.append(("Phase 5  Organize", "not started"))

    # Phase 6a — copy
    if "copy_status" in cols:
        n = int((files["copy_status"].str.upper() == "COPIED").sum())
        rows.append(("Phase 6a Copy", f"{n:,} files copied"))
    else:
        rows.append(("Phase 6a Copy", "not started"))

    # Phase 6b — manifest (check for file on disk)
    manifest_path = os.path.join(os.path.dirname(csv_path), "delete_manifest.csv")
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            n_lines = max(0, sum(1 for _ in f) - 1)
        rows.append(("Phase 6b Manifest", f"exists  ({n_lines:,} pending rows)"))
    else:
        rows.append(("Phase 6b Manifest", "not generated yet"))

    # Phase 6c — delete
    if "delete_status" in cols:
        n = int((files["delete_status"].str.upper() == "DELETED").sum())
        rows.append(("Phase 6c Delete", f"{n:,} files deleted"))
    else:
        rows.append(("Phase 6c Delete", "not started"))

    return rows


def _print_status(csv_path: str) -> None:
    status = _phase_status(csv_path)
    if not status:
        print("  (CSV not found)")
        return
    for label, val in status:
        print(f"  {label:30s}  {val}")


# ---------------------------------------------------------------------------
# CSV selector
# ---------------------------------------------------------------------------

def _find_report_csvs() -> list[str]:
    csvs = []
    if os.path.isdir("reports"):
        for f in sorted(os.listdir("reports")):
            if f.endswith(".csv") and "manifest" not in f and "log" not in f:
                csvs.append(os.path.join("reports", f))
    return csvs


def _select_csv() -> str | None:
    csvs = _find_report_csvs()
    if csvs:
        print("\n  Found CSVs:")
        for i, p in enumerate(csvs, 1):
            print(f"    {i}. {p}")
        print(f"    {len(csvs)+1}. Enter a different path")
        print("    0. Cancel")
        raw = input("\n  Pick a number (or paste a path): ").strip()
        if raw == "0":
            return None
        try:
            n = int(raw)
            if 1 <= n <= len(csvs):
                return csvs[n - 1]
            if n == len(csvs) + 1:
                return _ask("  CSV path") or None
        except ValueError:
            return raw or None
    else:
        return _ask("  CSV path (e.g. reports/drive_d.csv)") or None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    config = _load_config()
    csv_path: str | None = None

    # Auto-select if there's exactly one report CSV
    csvs = _find_report_csvs()
    if len(csvs) == 1:
        csv_path = csvs[0]

    while True:
        print("\n" + "=" * 62)
        print("  Archivist  —  Drive Recovery Wizard")
        print("=" * 62)

        if csv_path:
            print(f"\n  Working CSV: {csv_path}\n")
            _print_status(csv_path)
        else:
            print("\n  No CSV selected — start with option 1 (scan) or 2 (select).")

        print("""
  ── Setup ─────────────────────────────────────────────────
  1   Scan a drive                  [Phase 1]
  2   Select a different CSV

  ── Per-drive pipeline ────────────────────────────────────
  3   Mark duplicates               [Phase 2a  —  no LLM]
  4   Triage with LLM               [Phase 2b  —  needs Ollama]
  5   Manual review reminder        [Phase 3   —  you edit the CSV]
  6   Summarize                     [Phase 4   —  needs Ollama]
  7   Organize into folders         [Phase 5   —  needs Ollama]

  ── Execution ─────────────────────────────────────────────
  8   Copy kept files               [Phase 6a]
  9   Generate delete manifest      [Phase 6b  —  can cover multiple drives]
  10  Run deletions                 [Phase 6c  —  IRREVERSIBLE]

  0   Quit
""")

        choice = input("  > ").strip()

        # ── 0: quit ──────────────────────────────────────────────────────
        if choice == "0":
            break

        # ── 1: scan ──────────────────────────────────────────────────────
        elif choice == "1":
            drive = _ask("  Drive or path to scan (e.g. D:\\\\)")
            if not drive:
                continue
            default_out = f"reports/drive_{drive[0].lower()}.csv" if len(drive) >= 2 else "reports/drive.csv"
            out = _ask("  Output CSV path", default_out)
            _run(["scan", "--drive", drive, "--output", out])
            csv_path = out

        # ── 2: select csv ────────────────────────────────────────────────
        elif choice == "2":
            sel = _select_csv()
            if sel:
                csv_path = sel
                print(f"\n  Selected: {csv_path}")

        # ── 3–7: pipeline phases ─────────────────────────────────────────
        elif choice in ("3", "4", "5", "6", "7"):
            if not csv_path:
                print("  Select a CSV first (option 2).")
                continue

            if choice == "3":
                _run(["mark-duplicates", "--csv", csv_path])

            elif choice == "4":
                _run(["triage", "--csv", csv_path])

            elif choice == "5":
                print(f"""
  Phase 3 is manual — no command to run.

  Open the CSV in Excel, LibreOffice, or Google Sheets:
    {csv_path}

  Set the 'decision' column on files or directories:
    KEEP    — recover this file
    ARCHIVE — recover, lower priority
    DELETE  — permanently remove from source drive
    (blank) — inherit the parent directory's decision

  Tip: set a decision on a FOLDER to apply it to everything inside.

  To preview what decision a path will inherit, run:
    python archivist.py resolve --csv {csv_path} --path "D:\\\\SomeFolder"
""")
                input("  Press Enter when you're done reviewing...")

            elif choice == "6":
                _run(["summarize", "--csv", csv_path])

            elif choice == "7":
                _run(["organize", "--csv", csv_path])

        # ── 8: copy ──────────────────────────────────────────────────────
        elif choice == "8":
            if not csv_path:
                print("  Select a CSV first (option 2).")
                continue
            dest = str(config.get("recovery_root", ""))
            if dest:
                print(f"\n  recovery_root from config: {dest}")
                if not _confirm("Use this destination?"):
                    dest = _ask("  Destination root (e.g. E:\\\\recovered\\\\)")
            else:
                dest = _ask("  Destination root (e.g. E:\\\\recovered\\\\)")
            if not dest:
                print("  No destination specified.")
                continue
            _run(["copy", "--csv", csv_path, "--dest", dest])

        # ── 9: manifest ──────────────────────────────────────────────────
        elif choice == "9":
            if not csv_path:
                print("  Select a CSV first (option 2).")
                continue
            csv_list = [csv_path]
            print(f"\n  Primary CSV: {csv_path}")
            while True:
                more = input("  Add another drive CSV? (path or blank to continue): ").strip()
                if not more:
                    break
                if os.path.exists(more):
                    csv_list.append(more)
                    print(f"  Added: {more}")
                else:
                    print(f"  File not found: {more}")

            default_manifest = os.path.join(os.path.dirname(csv_list[0]), "delete_manifest.csv")
            out = _ask("  Manifest output path", default_manifest)
            csv_args = [arg for c in csv_list for arg in ("--csv", c)]
            _run(["manifest"] + csv_args + ["--output", out])

        # ── 10: delete ───────────────────────────────────────────────────
        elif choice == "10":
            if not csv_path:
                print("  Select a CSV first (option 2).")
                continue
            default_manifest = os.path.join(os.path.dirname(csv_path), "delete_manifest.csv")
            manifest = _ask("  Manifest path", default_manifest)
            if not os.path.exists(manifest):
                print(f"\n  Manifest not found: {manifest}")
                print("  Run option 9 first to generate it.")
                continue
            csv_list = [csv_path]
            while True:
                more = input("  Add another drive CSV covered by this manifest? (blank to continue): ").strip()
                if not more:
                    break
                if os.path.exists(more):
                    csv_list.append(more)
                else:
                    print(f"  File not found: {more}")

            print(f"\n  Manifest : {manifest}")
            print(f"  CSV(s)   : {', '.join(csv_list)}")
            if not _confirm("This will permanently delete files from disk. Proceed?"):
                print("  Cancelled.")
                continue
            csv_args = [arg for c in csv_list for arg in ("--csv", c)]
            _run(["delete"] + csv_args + ["--manifest", manifest, "--confirm"])

        else:
            print("  Invalid choice.")


if __name__ == "__main__":
    main()
