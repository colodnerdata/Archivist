import csv
import hashlib
import logging
import os
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import PureWindowsPath

import pandas as pd
from tqdm import tqdm

from csv_utils import safe_write_csv
from inheritance import is_blank, resolve_all, resolve_to_set

logger = logging.getLogger(__name__)

_WRITE_BATCH = 50  # flush CSV and manifest every N files


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


def run_manifest(csv_paths: list[str], config: dict, output_path: str | None = None) -> str:
    """Generate a combined delete manifest from one or more drive CSVs.

    The manifest includes a source_csv column so run_delete knows which CSV to
    update when processing a multi-drive manifest.
    """
    all_delete_rows: list[dict] = []

    for csv_path in csv_paths:
        df = pd.read_csv(csv_path, dtype=str)
        eff_decision = resolve_all(df, "decision")

        for idx in df.index:
            eff = eff_decision[idx]
            if eff is None or str(eff).strip().upper() != "DELETE":
                continue
            del_status = df.at[idx, "delete_status"] if "delete_status" in df.columns else ""
            if not is_blank(del_status) and str(del_status).strip().upper() in ("DELETED", "ALREADY_GONE"):
                continue
            explicit_val = df.at[idx, "decision"] if "decision" in df.columns else ""
            if not is_blank(explicit_val) and str(explicit_val).strip().upper() == "DELETE":
                source = "EXPLICIT"
            else:
                _, source_desc = _resolve_source(df, str(df.at[idx, "path"]), "decision")
                source = f"INHERITED from {source_desc}" if "inherited" in source_desc.lower() else "EXPLICIT"

            all_delete_rows.append({
                "source_csv": csv_path,
                "path": str(df.at[idx, "path"]),
                "filename": str(df.at[idx, "filename"]),
                "size_bytes": str(df.at[idx, "size_bytes"]),
                "decision_source": source,
            })

    manifest_path = output_path or os.path.join(os.path.dirname(csv_paths[0]), "delete_manifest.csv")
    os.makedirs(os.path.dirname(os.path.abspath(manifest_path)), exist_ok=True)
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["source_csv", "path", "filename", "size_bytes", "decision_source"]
        )
        writer.writeheader()
        writer.writerows(all_delete_rows)

    total_files = len(all_delete_rows)
    try:
        total_bytes = sum(int(r["size_bytes"]) for r in all_delete_rows if r["size_bytes"].isdigit())
    except Exception:
        total_bytes = 0
    explicit_count = sum(1 for r in all_delete_rows if r["decision_source"] == "EXPLICIT")
    inherited_count = total_files - explicit_count

    print(f"\nDelete manifest written to: {manifest_path}")
    if len(csv_paths) > 1:
        print(f"  Covers {len(csv_paths)} drive CSV(s)")
    print(f"  Total files to delete: {total_files:,}")
    print(f"  Total size:            {total_bytes / (1024**3):.2f} GB")
    print(f"  Explicit decisions:    {explicit_count:,}")
    print(f"  Inherited decisions:   {inherited_count:,}")
    print("\nReview the manifest before running: archivist.py delete --confirm")
    return manifest_path


def run_manifest_batch(csv_paths: list[str], config: dict, output_dir: str | None = None) -> list[str]:
    """Generate one manifest per drive CSV (for per-drive deletion workflows)."""
    if not csv_paths:
        raise ValueError("At least one CSV path is required")

    manifest_paths = []
    for csv_path in csv_paths:
        manifest_path = _batch_manifest_path(csv_path, output_dir)
        run_manifest([csv_path], config, output_path=manifest_path)
        manifest_paths.append(manifest_path)

    print(f"\nGenerated {len(manifest_paths)} manifest(s).")
    return manifest_paths


def run_delete(csv_paths: list[str], manifest_path: str, config: dict) -> None:
    """Delete files listed in manifest and prune completed rows as work progresses.

    Accepts multiple CSVs so a single manifest can cover several drives.
    The manifest shrinks as files are deleted, so restarts skip already-done work.
    """
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    # Load manifest and group by source CSV
    manifest_rows = _load_manifest_rows(manifest_path)
    manifest_by_csv: dict[str, list[dict]] = defaultdict(list)
    for row in manifest_rows:
        manifest_by_csv[row["source_csv"]].append(row)

    # Backward compat: old manifests have no source_csv column (stored as "")
    if "" in manifest_by_csv:
        if len(csv_paths) != 1:
            raise ValueError(
                "Manifest lacks a source_csv column but multiple CSVs were provided. "
                "Regenerate the manifest."
            )
        manifest_by_csv[csv_paths[0]] = manifest_by_csv.pop("")

    # Validate: every path still in the manifest must still be DELETE in its CSV.
    # Paths already pruned from the manifest (from prior runs) are not checked.
    all_dfs: dict[str, pd.DataFrame] = {}
    for csv_path in csv_paths:
        df = pd.read_csv(csv_path, dtype=str)
        all_dfs[csv_path] = df
        current_delete = resolve_to_set(df, "decision", "DELETE")
        manifest_paths_for_csv = {r["path"] for r in manifest_by_csv.get(csv_path, [])}

        stale = manifest_paths_for_csv - current_delete
        if stale:
            example = next(iter(sorted(stale)))
            raise ValueError(
                f"Manifest does not match the current CSV state for "
                f"{os.path.basename(csv_path)}: {len(stale)} path(s) in the manifest "
                f"are no longer marked DELETE (e.g. {example}). "
                f"Regenerate the manifest before deleting."
            )

        # Warn about DELETE paths missing from manifest (added after manifest was generated)
        already_deleted = {
            str(df.at[idx, "path"]) for idx in df.index
            if "delete_status" in df.columns
            and str(df.at[idx, "delete_status"]).strip().upper() == "DELETED"
        }
        uncovered = (current_delete - manifest_paths_for_csv) - already_deleted
        if uncovered:
            print(
                f"WARNING: {len(uncovered)} path(s) in {os.path.basename(csv_path)} are "
                f"marked DELETE but absent from the manifest (likely added after it was "
                f"generated). They will be skipped this run. Regenerate the manifest to include them."
            )

    log_path = os.path.join(
        os.path.dirname(os.path.abspath(manifest_path)),
        f"delete_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    )
    log_handler = logging.FileHandler(log_path, encoding="utf-8")
    log_handler.setLevel(logging.INFO)
    del_logger = logging.getLogger("archivist.delete")
    del_logger.addHandler(log_handler)
    del_logger.setLevel(logging.INFO)

    rmtree_total = 0
    pending_prune: set[str] = set()

    for csv_path in csv_paths:
        df = all_dfs[csv_path]
        manifest_paths_for_csv = {r["path"] for r in manifest_by_csv.get(csv_path, [])}
        delete_norm = {_norm_path(p) for p in manifest_paths_for_csv}

        if "delete_status" not in df.columns:
            df["delete_status"] = ""

        # Find whole-subtree deletion candidates.
        # Explicit: directories in the manifest (is_dir=True, effectively DELETE).
        # Implicit: directories where every CSV-tracked file underneath is DELETE,
        #   even if the directory row itself wasn't explicitly marked.
        explicit_rmtrees, implicit_rmtrees = _find_rmtree_candidates(df, manifest_paths_for_csv)

        verified_implicit = [d for d in implicit_rmtrees if _has_only_delete_files(d, delete_norm)]
        skipped_implicit = len(implicit_rmtrees) - len(verified_implicit)
        if skipped_implicit:
            logger.info(
                "%d implicit rmtree candidate(s) skipped: contain files not recorded in the "
                "CSV (likely added since the scan).",
                skipped_implicit,
            )

        # Combine and deduplicate across categories (explicit may cover implicit subdirs)
        all_rmtree = _top_level_only(explicit_rmtrees + verified_implicit)
        rmtree_nps = [(_norm_path(d), _norm_path(d) + "\\") for d in all_rmtree]

        def _is_covered(path: str, _nps: list = rmtree_nps) -> bool:
            p = _norm_path(path)
            return any(p == n or p.startswith(pf) for n, pf in _nps)

        eligible = [
            idx for idx in df.index
            if str(df.at[idx, "path"]) in manifest_paths_for_csv
            and str(df.at[idx, "delete_status"]).strip().upper() != "DELETED"
            and str(df.at[idx, "is_dir"]).lower() != "true"
            and not _is_covered(str(df.at[idx, "path"]))
        ]

        # Delete whole subtrees first
        if all_rmtree:
            rmtree_succeeded: list[str] = []
            for dir_path in tqdm(all_rmtree, desc="Deleting (folders)", unit=" dirs"):
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
                        path_str = str(df.at[idx, "path"])
                        if path_str in manifest_paths_for_csv:
                            pending_prune.add(path_str)
                safe_write_csv(df, csv_path)
                _prune_manifest(manifest_path, pending_prune)
                pending_prune.clear()
                rmtree_total += len(rmtree_succeeded)

        # Delete remaining individual files
        deleted_dirs: set[str] = set()
        for i, idx in enumerate(tqdm(eligible, desc="Deleting", unit=" files")):
            path = str(df.at[idx, "path"])
            try:
                os.remove(path)
                df.at[idx, "delete_status"] = "DELETED"
                pending_prune.add(path)
                del_logger.info("DELETED: %s", path)
                deleted_dirs.add(os.path.dirname(path))
            except FileNotFoundError:
                df.at[idx, "delete_status"] = "DELETED"
                pending_prune.add(path)
                del_logger.info("ALREADY_GONE: %s", path)
            except Exception as e:
                df.at[idx, "delete_status"] = "FAILED"
                del_logger.error("FAILED: %s -- %s", path, e)

            if (i + 1) % _WRITE_BATCH == 0:
                safe_write_csv(df, csv_path)
                _prune_manifest(manifest_path, pending_prune)
                pending_prune.clear()

        safe_write_csv(df, csv_path)

        # Remove empty directories left behind by per-file deletions (deepest first)
        for d in sorted(deleted_dirs, key=lambda x: x.count(os.sep), reverse=True):
            try:
                if os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
                    del_logger.info("RMDIR: %s", d)
            except Exception as e:
                del_logger.warning("RMDIR failed for %s: %s", d, e)

    if pending_prune:
        _prune_manifest(manifest_path, pending_prune)

    log_handler.close()
    if rmtree_total:
        print(f"Deleted {rmtree_total} folder(s) as whole subtrees.")
    print(f"Delete complete. Log written to: {log_path}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_copy(src: str, dest: str, expected_md5: str) -> bool:
    try:
        if os.path.getsize(src) != os.path.getsize(dest):
            return False
        dest_md5 = _md5(dest)
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


def _load_manifest_rows(manifest_path: str) -> list[dict]:
    """Load manifest rows. If no source_csv column exists (old format), sets it to ''."""
    rows: list[dict] = []
    with open(manifest_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        has_source = "source_csv" in fieldnames
        for row in reader:
            if not has_source:
                row["source_csv"] = ""
            if row.get("path"):
                rows.append(row)
    return rows


def _prune_manifest(manifest_path: str, deleted_paths: set[str]) -> None:
    """Remove successfully-deleted paths from the manifest (atomic rewrite)."""
    if not deleted_paths or not os.path.exists(manifest_path):
        return
    deleted_norm = {_norm_path(p) for p in deleted_paths}
    tmp = manifest_path + ".tmp"
    remaining: list[dict] = []
    fieldnames: list[str] = []
    with open(manifest_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            if _norm_path(row.get("path", "")) not in deleted_norm:
                remaining.append(row)
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(remaining)
    os.replace(tmp, manifest_path)


def _norm_path(path: str) -> str:
    return str(PureWindowsPath(path)).lower()


def _top_level_only(candidates: list[str]) -> list[str]:
    """Return the shallowest paths, dropping any that are subdirectories of another candidate."""
    result: list[str] = []
    for c in sorted(candidates, key=lambda x: x.count(os.sep)):
        c_norm = _norm_path(c)
        if not any(c_norm.startswith(_norm_path(tl) + "\\") for tl in result):
            result.append(c)
    return result


def _find_rmtree_candidates(
    df: pd.DataFrame, manifest_paths: set[str]
) -> tuple[list[str], list[str]]:
    """Return (explicit_candidates, implicit_candidates).

    Explicit: directory rows that are in the manifest (is_dir=True, effectively DELETE)
    with no descendant carrying an explicit non-DELETE decision.

    Implicit: directory rows NOT in the manifest where every CSV-tracked file underneath
    is DELETE. Callers must verify on disk (via _has_only_delete_files) before using
    implicit candidates, since files added after the scan won't appear in the CSV.
    """
    delete_norm = {_norm_path(p) for p in manifest_paths}

    exception_norms: set[str] = set()
    if "decision" in df.columns:
        for idx in df.index:
            val = str(df.at[idx, "decision"]).strip().upper()
            if val and val not in ("", "NAN", "DELETE"):
                exception_norms.add(_norm_path(str(df.at[idx, "path"])))

    def has_exception_under(dir_norm: str) -> bool:
        prefix = dir_norm + "\\"
        return any(ep == dir_norm or ep.startswith(prefix) for ep in exception_norms)

    # Count CSV file descendants under each directory in O(n * depth)
    dir_file_count: dict[str, int] = {}
    dir_delete_count: dict[str, int] = {}
    for idx in df.index:
        if str(df.at[idx, "is_dir"]).lower() == "true":
            continue
        path_str = str(df.at[idx, "path"])
        in_delete = _norm_path(path_str) in delete_norm
        try:
            p = PureWindowsPath(path_str)
            drive = p.drive
            for parent in p.parents:
                parent_str = str(parent)
                if parent_str == drive or parent_str == drive + "\\":
                    break
                pn = _norm_path(parent_str)
                dir_file_count[pn] = dir_file_count.get(pn, 0) + 1
                if in_delete:
                    dir_delete_count[pn] = dir_delete_count.get(pn, 0) + 1
        except Exception:
            pass

    explicit: list[str] = []
    implicit: list[str] = []

    for idx in df.index:
        if str(df.at[idx, "is_dir"]).lower() != "true":
            continue
        dir_path = str(df.at[idx, "path"])
        dir_norm = _norm_path(dir_path)

        if not os.path.isdir(dir_path):
            continue
        if has_exception_under(dir_norm):
            continue

        if dir_path in manifest_paths:
            explicit.append(dir_path)
        else:
            count = dir_file_count.get(dir_norm, 0)
            if count > 0 and count == dir_delete_count.get(dir_norm, 0):
                implicit.append(dir_path)

    return _top_level_only(explicit), _top_level_only(implicit)


def _has_only_delete_files(dir_path: str, delete_norm: set[str]) -> bool:
    """Return True if every file present on disk under dir_path is in delete_norm.

    This guards against files added to the directory after the scan was taken.
    """
    try:
        for dirpath, _, filenames in os.walk(dir_path):
            for fn in filenames:
                if _norm_path(os.path.join(dirpath, fn)) not in delete_norm:
                    return False
        return True
    except Exception:
        return False


def _current_delete_paths(df: pd.DataFrame) -> set[str]:
    return resolve_to_set(df, "decision", "DELETE")


def _resolve_source(df: pd.DataFrame, path: str, column: str) -> tuple[str | None, str]:
    from inheritance import resolve_effective
    return resolve_effective(df, path, column)


def _batch_manifest_path(csv_path: str, output_dir: str | None) -> str:
    base_dir = output_dir or os.path.dirname(csv_path)
    base_name = os.path.splitext(os.path.basename(csv_path))[0]
    return os.path.join(base_dir, f"{base_name}_delete_manifest.csv")
