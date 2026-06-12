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


def resolve_all_multi(df: pd.DataFrame, columns: list[str]) -> dict[str, pd.Series]:
    """
    Resolve multiple columns in one pass, sharing path normalization.
    Returns a dict mapping column name → Series (None where unset).
    More efficient than calling resolve_all() once per column on the same DataFrame.
    """
    missing = [c for c in columns if c not in df.columns]
    present = [c for c in columns if c in df.columns]

    result: dict[str, pd.Series] = {
        c: pd.Series([None] * len(df), index=df.index) for c in missing
    }

    if not present:
        return result

    norm_cache: dict[str, str] = {}

    def _norm_cached(s: str) -> str:
        if s not in norm_cache:
            norm_cache[s] = _normalize(s).lower()
        return norm_cache[s]

    # Build one path_index per column in a single DataFrame pass
    path_indexes: dict[str, dict[str, str]] = {c: {} for c in present}
    for _, row in df.iterrows():
        p_norm = _norm_cached(str(row["path"]))
        for col in present:
            val = row[col]
            if not is_blank(val):
                path_indexes[col][p_norm] = str(val)

    # Resolve all rows, walking ancestors at most once per row regardless of column count
    row_results: dict[str, list] = {c: [] for c in present}
    for _, row in df.iterrows():
        row_path = str(row["path"])
        norm = _norm_cached(row_path)

        found: dict[str, str] = {}
        remaining = {c for c in present if norm not in path_indexes[c]}
        for col in present:
            if norm in path_indexes[col]:
                found[col] = path_indexes[col][norm]

        if remaining:
            try:
                p = PureWindowsPath(row_path)
                drive = p.drive
                for ancestor in p.parents:
                    ancestor_str = str(ancestor)
                    if ancestor_str == drive or ancestor_str == drive + "\\":
                        break
                    anc_norm = _norm_cached(ancestor_str)
                    for col in list(remaining):
                        if anc_norm in path_indexes[col]:
                            found[col] = path_indexes[col][anc_norm]
                            remaining.discard(col)
                    if not remaining:
                        break
            except Exception:
                pass

        for col in present:
            row_results[col].append(found.get(col))

    return {
        **result,
        **{col: pd.Series(row_results[col], index=df.index) for col in present},
    }


def resolve_to_set(df: pd.DataFrame, column: str, target_value: str) -> set[str]:
    """
    Returns the set of paths whose effective `column` value equals `target_value`.
    Builds the result set directly without allocating an intermediate Series.
    """
    if column not in df.columns:
        return set()

    target_upper = target_value.strip().upper()
    norm_cache: dict[str, str] = {}

    def _norm_cached(s: str) -> str:
        if s not in norm_cache:
            norm_cache[s] = _normalize(s).lower()
        return norm_cache[s]

    # Build index: normalized_path → uppercased value for all non-blank entries
    path_index: dict[str, str] = {}
    for _, row in df.iterrows():
        val = row[column]
        if not is_blank(val):
            path_index[_norm_cached(str(row["path"]))] = str(val).strip().upper()

    result: set[str] = set()
    for _, row in df.iterrows():
        path_str = str(row["path"])
        norm = _norm_cached(path_str)

        if norm in path_index:
            if path_index[norm] == target_upper:
                result.add(path_str)
            continue  # explicit value found; ancestor walk not needed

        try:
            p = PureWindowsPath(path_str)
        except Exception:
            continue

        drive = p.drive
        for ancestor in p.parents:
            ancestor_str = str(ancestor)
            if ancestor_str == drive or ancestor_str == drive + "\\":
                break
            anc_norm = _norm_cached(ancestor_str)
            if anc_norm in path_index:
                if path_index[anc_norm] == target_upper:
                    result.add(path_str)
                break  # nearest ancestor wins; stop walking

    return result
