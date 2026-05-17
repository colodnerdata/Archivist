from pathlib import PureWindowsPath

import pandas as pd


def is_blank(val) -> bool:
    if val is None:
        return True
    if isinstance(val, float):
        import math
        return math.isnan(val)
    return str(val).strip() == ""


def _normalize(path: str) -> str:
    return str(PureWindowsPath(path))


def resolve_effective(df: pd.DataFrame, row_path: str, column: str) -> tuple[str | None, str]:
    """
    Returns (value, source).
    source: 'explicit' | 'inherited from <path>' | 'unset'
    """
    norm = _normalize(row_path).lower()

    # Build index once: normalized_path_lower → value (only non-blank entries)
    path_index: dict[str, str] = {}
    if column in df.columns:
        for _, row in df.iterrows():
            val = row[column]
            if not is_blank(val):
                path_index[_normalize(str(row["path"])).lower()] = str(val)

    # Check own row
    if norm in path_index:
        return (path_index[norm], "explicit")

    # Walk ancestors nearest-first
    try:
        p = PureWindowsPath(row_path)
    except Exception:
        return (None, "unset")

    drive = p.drive  # e.g. "D:"
    for ancestor in p.parents:
        ancestor_str = str(ancestor)
        # Skip the drive root itself
        if ancestor_str == drive or ancestor_str == drive + "\\":
            break
        anc_norm = _normalize(ancestor_str).lower()
        if anc_norm in path_index:
            return (path_index[anc_norm], f"inherited from {ancestor_str}")

    return (None, "unset")


def resolve_all(df: pd.DataFrame, column: str) -> pd.Series:
    """
    Bulk O(n * depth) resolution. Returns Series aligned to df.index, None where unset.
    """
    if column not in df.columns:
        return pd.Series([None] * len(df), index=df.index)

    # Shared cache so the same raw path string is only normalized once across both loops
    norm_cache: dict[str, str] = {}

    def _norm_cached(s: str) -> str:
        if s not in norm_cache:
            norm_cache[s] = _normalize(s).lower()
        return norm_cache[s]

    # Build index of non-blank values: normalized_path_lower → value
    path_index: dict[str, str] = {}
    for _, row in df.iterrows():
        val = row[column]
        if not is_blank(val):
            path_index[_norm_cached(str(row["path"]))] = str(val)

    results = []
    for _, row in df.iterrows():
        row_path = str(row["path"])
        norm = _norm_cached(row_path)

        # Own explicit value
        if norm in path_index:
            results.append(path_index[norm])
            continue

        # Walk ancestors
        try:
            p = PureWindowsPath(row_path)
        except Exception:
            results.append(None)
            continue

        drive = p.drive
        found = None
        for ancestor in p.parents:
            ancestor_str = str(ancestor)
            if ancestor_str == drive or ancestor_str == drive + "\\":
                break
            anc_norm = _norm_cached(ancestor_str)
            if anc_norm in path_index:
                found = path_index[anc_norm]
                break

        results.append(found)

    return pd.Series(results, index=df.index)
