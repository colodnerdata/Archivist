import os

import pandas as pd

from csv_utils import safe_write_csv

REPORT_COLUMNS = [
    "source_csv",
    "path",
    "filename",
    "size_bytes",
    "md5_hash",
    "duplicate_source_path",
    "recommendation",
    "decision",
    "comment",
]


def run_duplicates_report(csv_paths: list[str], output_csv: str) -> None:
    if not csv_paths:
        raise ValueError("At least one CSV path is required")

    report_frames: list[pd.DataFrame] = []
    for csv_path in csv_paths:
        df = pd.read_csv(csv_path, dtype=str)
        filtered = df[
            (df.get("duplicate_kind", pd.Series(dtype=str)).fillna("") == "baseline_scan")
            & (df.get("is_dir", "False").str.lower() != "true")
        ].copy()

        if filtered.empty:
            continue

        filtered["source_csv"] = csv_path
        for column in REPORT_COLUMNS:
            if column not in filtered.columns:
                filtered[column] = ""

        report_frames.append(filtered[REPORT_COLUMNS])

    if report_frames:
        report_df = pd.concat(report_frames, ignore_index=True)
    else:
        report_df = pd.DataFrame(columns=REPORT_COLUMNS)

    os.makedirs(os.path.dirname(os.path.abspath(output_csv)), exist_ok=True)
    safe_write_csv(report_df, output_csv)

    total_files = len(report_df)
    total_bytes = pd.to_numeric(report_df.get("size_bytes", pd.Series(dtype=str)), errors="coerce").fillna(0).sum()
    print(f"Baseline duplicate report written to: {output_csv}")
    print(f"  Duplicate files: {total_files:,}")
    print(f"  Total size:      {total_bytes / (1024**3):.2f} GB")
