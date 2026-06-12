import csv
import hashlib
import logging
import os
import shutil
from datetime import datetime
from pathlib import PureWindowsPath

import pandas as pd
from tqdm import tqdm

from csv_utils import safe_write_csv
from inheritance import is_blank, resolve_all, resolve_to_set

logger = logging.getLogger(__name__)

_WRITE_BATCH = 50  # flush CSV to disk every N files


def run_copy(csv_path: str, dest_root: str, config: dict) -> None:
    df = pd.read_csv(csv_path, dtype=str)
    eff_decision = resolve_all(df, "decision")

    if "copy_status" not in df.columns:
        df["copy_status"] = ""

    eligible = [
        idx for idx in df.index
        if eff_decision[idx] is not None
        and str(eff_decision[idx]).strip().upper() in ("KEEP", "ARCHIVE")
        and str(df.at[idx, "is_dir"]).lower() != "true"
        and str(df.at[idx, "copy_status"]).strip().upper() != "COPIED"
    ]

    copied_rows = []
    for i, idx in enumerate(tqdm(eligible, desc="Copying", unit=" files")):
        src = str(df.at[idx, "path"])
        organized = str(df.at[idx, "organized_path"]) if "organized_path" in df.columns else ""
        if not organized or organized.strip() == "" or organized.strip().lower() == "nan":
            logger.warning("No organized_path for %s, skipping", src)
            df.at[idx, "copy_status"] = "SKIPPED"
            if (i + 1) % _WRITE_BATCH == 0:
                safe_write_csv(df, csv_path)
            continue

        dest = os.path.join(dest_root, organized)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        try:
            shutil.copy2(src, dest)
            src_md5 = str(df.at[idx, "md5_hash"])
            if _verify_copy(src, dest, src_md5):
                df.at[idx, "copy_status"] = "COPIED"
                copied_rows.append({
                    "md5_hash": src_md5,
                    "original_path": src,
                    "organized_path": organized,
                    "drive": _drive_letter(src),
                    "date_copied": datetime.now().isoformat(),
                })
            else:
                df.at[idx, "copy_status"] = "FAILED"
                logger.error("Verification failed for %s → %s", src, dest)
        except Exception as e:
            df.at[idx, "copy_status"] = "FAILED"
            logger.error("Copy failed for %s: %s", src, e)

        if (i + 1) % _WRITE_BATCH == 0:
            safe_write_csv(df, csv_path)

    safe_write_csv(df, csv_path)

    if copied_rows:
        _append_kept_hashes(copied_rows, config)
        print(f"Copied {len(copied_rows)} files. Hashes appended to kept_hashes registry.")


def run_manifest(csv_path: str, config: dict, output_path: str | None = None) -> str:
    df = pd.read_csv(csv_path, dtype=str)
    eff_decision = resolve_all(df, "decision")

    delete_rows = []
    for idx in df.index:
        eff = eff_decision[idx]
        if eff is not None and str(eff).strip().upper() == "DELETE":
            explicit_val = df.at[idx, "decision"] if "decision" in df.columns else ""
            if not is_blank(explicit_val) and str(explicit_val).strip().upper() == "DELETE":
                source = "EXPLICIT"
            else:
                _, source_desc = _resolve_source(df, str(df.at[idx, "path"]), "decision")
                source = f"INHERITED from {source_desc}" if "inherited" in source_desc.lower() else "EXPLICIT"

            delete_rows.append({
                "path": str(df.at[idx, "path"]),
                "filename": str(df.at[idx, "filename"]),
                "size_bytes": str(df.at[idx, "size_bytes"]),
                "decision_source": source,
            })

    manifest_path = output_path or os.path.join(os.path.dirname(csv_path), "delete_manifest.csv")
    os.makedirs(os.path.dirname(os.path.abspath(manifest_path)), exist_ok=True)
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["path", "filename", "size_bytes", "decision_source"])
        writer.writeheader()
        writer.writerows(delete_rows)

    total_files = len(delete_rows)
    try:
        total_bytes = sum(int(r["size_bytes"]) for r in delete_rows if r["size_bytes"].isdigit())
    except Exception:
        total_bytes = 0
    explicit_count = sum(1 for r in delete_rows if r["decision_source"] == "EXPLICIT")
    inherited_count = total_files - explicit_count

    print(f"\nDelete manifest written to: {manifest_path}")
    print(f"  Total files to delete: {total_files:,}")
    print(f"  Total size:            {total_bytes / (1024**3):.2f} GB")
    print(f"  Explicit decisions:    {explicit_count:,}")
    print(f"  Inherited decisions:   {inherited_count:,}")
    print("\nReview the manifest before running: archivist.py delete --confirm")
    return manifest_path


def run_manifest_batch(csv_paths: list[str], config: dict, output_dir: str | None = None) -> list[str]:
    if not csv_paths:
        raise ValueError("At least one CSV path is required")

    manifest_paths = []
    for csv_path in csv_paths:
        manifest_path = _batch_manifest_path(csv_path, output_dir)
        run_manifest(csv_path, config, output_path=manifest_path)
        manifest_paths.append(manifest_path)

    print(f"\nGenerated {len(manifest_paths)} manifest(s).")
    return manifest_paths


def run_delete(csv_path: str, manifest_path: str, config: dict) -> None:
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    df = pd.read_csv(csv_path, dtype=str)
    manifest_paths = _load_manifest_paths(manifest_path)
    current_delete_paths = _current_delete_paths(df)

    if manifest_paths != current_delete_paths:
        missing_from_manifest = sorted(current_delete_paths - manifest_paths)
        stale_manifest_entries = sorted(manifest_paths - current_delete_paths)
        details = []
        if missing_from_manifest:
            details.append(
                f"missing {len(missing_from_manifest)} current delete path(s), e.g. {missing_from_manifest[0]}"
            )
        if stale_manifest_entries:
            details.append(
                f"contains {len(stale_manifest_entries)} stale path(s), e.g. {stale_manifest_entries[0]}"
            )
        detail_text = "; ".join(details) if details else "manifest contents differ from the current CSV"
        raise ValueError(
            "Manifest does not match the current CSV state. "
            f"Regenerate the manifest before deleting: {detail_text}."
        )

    if "delete_status" not in df.columns:
        df["delete_status"] = ""

    log_path = os.path.join(
        os.path.dirname(csv_path),
        f"delete_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    )
    log_handler = logging.FileHandler(log_path, encoding="utf-8")
    log_handler.setLevel(logging.INFO)
    del_logger = logging.getLogger("archivist.delete")
    del_logger.addHandler(log_handler)
    del_logger.setLevel(logging.INFO)

    # Whole-subtree deletions: find directories safe to rmtree at once
    rmtree_dirs = _find_rmtree_candidates(df, manifest_paths)
    rmtree_nps = [(_norm_path(d), _norm_path(d) + "\\") for d in rmtree_dirs]

    def _is_covered(path: str) -> bool:
        p = _norm_path(path)
        return any(p == n or p.startswith(pf) for n, pf in rmtree_nps)

    deleted_dirs: set[str] = set()
    eligible = [
        idx for idx in df.index
        if str(df.at[idx, "path"]) in manifest_paths
        and str(df.at[idx, "delete_status"]).strip().upper() != "DELETED"
        and str(df.at[idx, "is_dir"]).lower() != "true"
        and not _is_covered(str(df.at[idx, "path"]))
    ]

    # Delete whole subtrees first
    rmtree_succeeded: list[str] = []
    if rmtree_dirs:
        for dir_path in tqdm(rmtree_dirs, desc="Deleting (folders)", unit=" dirs"):
            try:
                if os.path.exists(dir_path):
                    shutil.rmtree(dir_path)
                rmtree_succeeded.append(dir_path)
                del_logger.info("RMTREE: %s", dir_path)
            except Exception as e:
                del_logger.error("RMTREE FAILED: %s -- %s", dir_path, e)

        if rmtree_succeeded:
            succeeded_nps = [(_norm_path(d), _norm_path(d) + "\\") for d in rmtree_succeeded]
            for idx in df.index:
                p = _norm_path(str(df.at[idx, "path"]))
                if any(p == n or p.startswith(pf) for n, pf in succeeded_nps):
                    df.at[idx, "delete_status"] = "DELETED"
            safe_write_csv(df, csv_path)

    # Delete remaining individual files
    for i, idx in enumerate(tqdm(eligible, desc="Deleting", unit=" files")):
        path = str(df.at[idx, "path"])
        try:
            os.remove(path)
            df.at[idx, "delete_status"] = "DELETED"
            del_logger.info("DELETED: %s", path)
            deleted_dirs.add(os.path.dirname(path))
        except FileNotFoundError:
            df.at[idx, "delete_status"] = "DELETED"
            del_logger.info("ALREADY_GONE: %s", path)
        except Exception as e:
            df.at[idx, "delete_status"] = "FAILED"
            del_logger.error("FAILED: %s -- %s", path, e)

        if (i + 1) % _WRITE_BATCH == 0:
            safe_write_csv(df, csv_path)

    safe_write_csv(df, csv_path)

    # Remove empty directories left by per-file deletions (leaf first)
    for d in sorted(deleted_dirs, key=lambda x: x.count(os.sep), reverse=True):
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                del_logger.info("RMDIR: %s", d)
        except Exception as e:
            del_logger.warning("RMDIR failed for %s: %s", d, e)

    log_handler.close()
    if rmtree_succeeded:
        print(f"Deleted {len(rmtree_succeeded)} folder(s) as whole subtrees.")
    print(f"Delete complete. Log written to: {log_path}")


def _verify_copy(src: str, dest: str, expected_md5: str) -> bool:
    try:
        if os.path.getsize(src) != os.path.getsize(dest):
            return False
        dest_md5 = _md5(dest)
        # Fall back to hashing both sides if scan didn't record an MD5
        if not expected_md5 or expected_md5.strip().lower() in ("", "nan"):
            return _md5(src) == dest_md5
        return dest_md5 == expected_md5
    except Exception as e:
        logger.warning("Verification error: %s", e)
        return False


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _drive_letter(path: str) -> str:
    if len(path) >= 2 and path[1] == ":":
        return path[:2]
    return ""


def _append_kept_hashes(copied_rows: list[dict], config: dict) -> None:
    path = config.get("kept_hashes_path", "kept_hashes.csv")
    fieldnames = ["md5_hash", "original_path", "organized_path", "drive", "date_copied"]
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(copied_rows)


def _load_manifest_paths(manifest_path: str) -> set[str]:
    manifest_paths: set[str] = set()
    with open(manifest_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("path"):
                manifest_paths.add(row["path"])
    return manifest_paths


def _current_delete_paths(df: pd.DataFrame) -> set[str]:
    return resolve_to_set(df, "decision", "DELETE")


def _norm_path(path: str) -> str:
    return str(PureWindowsPath(path)).lower()


def _find_rmtree_candidates(df: pd.DataFrame, manifest_paths: set[str]) -> list[str]:
    """
    Return the shallowest directory paths that can be deleted as whole subtrees.
    A directory qualifies if it's in the delete manifest and no descendant has an
    explicit non-DELETE decision (which would survive the rmtree).
    """
    exception_norms: set[str] = set()
    if "decision" in df.columns:
        for idx in df.index:
            val = str(df.at[idx, "decision"]).strip().upper()
            if val and val not in ("", "NAN", "DELETE"):
                exception_norms.add(_norm_path(str(df.at[idx, "path"])))

    candidates: list[str] = []
    for idx in df.index:
        if str(df.at[idx, "is_dir"]).lower() != "true":
            continue
        path = str(df.at[idx, "path"])
        if path not in manifest_paths:
            continue
        if os.path.isdir(path):
            candidates.append(path)

    def has_exception_under(dir_norm: str) -> bool:
        prefix = dir_norm + "\\"
        return any(ep.startswith(prefix) for ep in exception_norms)

    clean = [c for c in candidates if not has_exception_under(_norm_path(c))]

    # Keep only top-level — skip subdirs already covered by a shallower candidate
    top_level: list[str] = []
    for c in sorted(clean, key=lambda x: x.count(os.sep)):
        c_norm = _norm_path(c)
        if not any(c_norm.startswith(_norm_path(tl) + "\\") for tl in top_level):
            top_level.append(c)

    return top_level


def _resolve_source(df: pd.DataFrame, path: str, column: str) -> tuple[str | None, str]:
    from inheritance import resolve_effective
    return resolve_effective(df, path, column)


def _batch_manifest_path(csv_path: str, output_dir: str | None) -> str:
    base_dir = output_dir or os.path.dirname(csv_path)
    base_name = os.path.splitext(os.path.basename(csv_path))[0]
    return os.path.join(base_dir, f"{base_name}_delete_manifest.csv")
