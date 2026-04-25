import csv
import hashlib
import logging
import os
import sys
from datetime import datetime
from pathlib import PureWindowsPath

from tqdm import tqdm

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "path", "filename", "extension", "is_dir", "size_bytes", "modified",
    "md5_hash", "is_duplicate", "recommendation", "confidence", "comment",
]


def run_scan(drive_path: str, output_csv: str, config: dict) -> None:
    kept_hashes = _load_kept_hashes(config.get("kept_hashes_path", "kept_hashes.csv"))
    exclude_dirs = set(config.get("exclude_dirs", []))
    exclude_exts = set(e.lower() for e in config.get("exclude_extensions", []))

    file_exists = os.path.exists(output_csv)
    seen_paths = _load_seen_paths(output_csv) if file_exists else set()

    os.makedirs(os.path.dirname(os.path.abspath(output_csv)), exist_ok=True)

    local_hashes: dict[str, str] = {}  # md5 -> first seen path in this scan

    with open(output_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()

        def on_walk_error(err: OSError) -> None:
            _log_walk_error(err)
            error_path = getattr(err, "filename", None)
            if error_path:
                _write_directory_row(
                    writer,
                    f,
                    seen_paths,
                    error_path,
                    recommendation="REVIEW",
                    confidence="0.0",
                    comment=f"INACCESSIBLE: {err.strerror or str(err)}",
                    modified="",
                )

        try:
            for dirpath, dirnames, filenames in tqdm(
                os.walk(drive_path, onerror=on_walk_error),
                desc="Scanning (Ctrl+C to cancel; rerun to resume)",
                unit=" dirs",
            ):
                dir_norm = _norm(dirpath)

                # Emit directory row for current directory
                if dir_norm not in seen_paths:
                    dirname_only = os.path.basename(dirpath)
                    excluded = dirname_only in exclude_dirs
                    _write_directory_row(
                        writer,
                        f,
                        seen_paths,
                        dirpath,
                        recommendation="SKIP" if excluded else "",
                        confidence="0.99" if excluded else "",
                        comment="Excluded by config" if excluded else "",
                    )

                # Emit SKIP rows for excluded subdirs before pruning them
                for excl_name in dirnames:
                    if excl_name in exclude_dirs:
                        _write_directory_row(
                            writer,
                            f,
                            seen_paths,
                            os.path.join(dirpath, excl_name),
                            recommendation="SKIP",
                            confidence="0.99",
                            comment="Excluded by config",
                        )

                # Prune excluded subdirs so os.walk doesn't descend into them
                dirnames[:] = [d for d in dirnames if d not in exclude_dirs]

                for filename in filenames:
                    ext = os.path.splitext(filename)[1].lower()
                    if ext in exclude_exts:
                        continue
                    file_path = os.path.join(dirpath, filename)
                    file_norm = _norm(file_path)
                    if file_norm in seen_paths:
                        continue

                    try:
                        stat = os.stat(file_path)
                        size = stat.st_size
                        modified = datetime.fromtimestamp(stat.st_mtime).isoformat()
                    except OSError as e:
                        logger.warning("stat failed for %s: %s", file_path, e)
                        size = 0
                        modified = ""

                    md5 = _compute_md5(file_path)
                    is_dup = False
                    comment = ""
                    if md5 and md5 in kept_hashes:
                        is_dup = True
                        h = kept_hashes[md5]
                        comment = f"Already kept from {h['original_path']} → {h['organized_path']}"
                    elif md5 and md5 in local_hashes:
                        is_dup = True
                        comment = f"Duplicate of {local_hashes[md5]} (same drive)"
                    elif md5:
                        local_hashes[md5] = file_norm

                    row = {
                        "path": file_norm,
                        "filename": filename,
                        "extension": ext,
                        "is_dir": False,
                        "size_bytes": size,
                        "modified": modified,
                        "md5_hash": md5,
                        "is_duplicate": is_dup,
                        "recommendation": "",
                        "confidence": "",
                        "comment": comment,
                    }
                    writer.writerow(row)
                    f.flush()
                    seen_paths.add(file_norm)
        except KeyboardInterrupt:
            print("\nScan interrupted. Re-run the same command to resume from the existing CSV.", file=sys.stderr)


def _write_directory_row(
    writer: csv.DictWriter,
    file_obj,
    seen_paths: set[str],
    path: str,
    recommendation: str,
    confidence: str,
    comment: str,
    modified: str | None = None,
) -> None:
    dir_norm = _norm(path)
    if dir_norm in seen_paths:
        return

    writer.writerow({
        "path": dir_norm,
        "filename": "",
        "extension": "",
        "is_dir": True,
        "size_bytes": 0,
        "modified": _mtime(path) if modified is None else modified,
        "md5_hash": "",
        "is_duplicate": False,
        "recommendation": recommendation,
        "confidence": confidence,
        "comment": comment,
    })
    file_obj.flush()
    seen_paths.add(dir_norm)


def _norm(path: str) -> str:
    return str(PureWindowsPath(path))


def _mtime(path: str) -> str:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
    except OSError:
        return ""


def _compute_md5(file_path: str) -> str:
    h = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError as e:
        logger.warning("md5 failed for %s: %s", file_path, e)
        return ""


def _load_kept_hashes(path: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    if not os.path.exists(path):
        return result
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("md5_hash"):
                    result[row["md5_hash"]] = row
    except (OSError, csv.Error) as e:
        logger.warning("Could not load kept_hashes from %s: %s", path, e)
    return result


def _load_seen_paths(csv_path: str) -> set[str]:
    seen: set[str] = set()
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("path"):
                    seen.add(row["path"])
    except (OSError, csv.Error) as e:
        logger.warning("Could not load seen paths from %s: %s", csv_path, e)
    return seen


def _log_walk_error(err: OSError) -> None:
    logger.warning("Walk error: %s", err)
