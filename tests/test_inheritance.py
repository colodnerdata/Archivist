import math

import pandas as pd
import pytest

from inheritance import is_blank, resolve_all, resolve_effective


def test_explicit_value_wins(sample_df):
    val, source = resolve_effective(sample_df, r"D:\Windows", "decision")
    assert val == "DELETE"
    assert source == "explicit"


def test_parent_inheritance(sample_df):
    val, source = resolve_effective(sample_df, r"D:\Windows\notepad.exe", "decision")
    assert val == "DELETE"
    assert "inherited" in source.lower()


def test_child_overrides_parent(sample_df):
    # letter.pdf has explicit DELETE; its parent (old) has no decision; grandparent (Documents) has KEEP
    val, source = resolve_effective(sample_df, r"D:\Users\Stephen\Documents\old\letter.pdf", "decision")
    assert val == "DELETE"
    assert source == "explicit"


def test_grandparent_propagates(sample_df):
    # D:\Users\Stephen\Documents\old has no decision; parent Documents has KEEP
    val, source = resolve_effective(sample_df, r"D:\Users\Stephen\Documents\old", "decision")
    assert val == "KEEP"
    assert "inherited" in source.lower()


def test_unset_returns_none(sample_df):
    val, source = resolve_effective(sample_df, r"D:\Unreviewed\file.txt", "decision")
    assert val is None
    assert source == "unset"


def test_case_insensitive(sample_df):
    val, source = resolve_effective(sample_df, r"d:\windows\notepad.exe", "decision")
    assert val == "DELETE"


def test_resolve_all_length(sample_df):
    result = resolve_all(sample_df, "decision")
    assert len(result) == len(sample_df)


def test_resolve_all_explicit_row(sample_df):
    result = resolve_all(sample_df, "decision")
    windows_idx = sample_df[sample_df["path"] == r"D:\Windows"].index[0]
    assert result[windows_idx] == "DELETE"


def test_resolve_all_inherited_row(sample_df):
    result = resolve_all(sample_df, "decision")
    notepad_idx = sample_df[sample_df["path"] == r"D:\Windows\notepad.exe"].index[0]
    assert result[notepad_idx] == "DELETE"


def test_resolve_all_unset_row(sample_df):
    result = resolve_all(sample_df, "decision")
    unreviewed_idx = sample_df[sample_df["path"] == r"D:\Unreviewed\file.txt"].index[0]
    assert pd.isna(result[unreviewed_idx]) or result[unreviewed_idx] is None


def test_is_blank_none():
    assert is_blank(None)


def test_is_blank_nan():
    import math
    assert is_blank(float("nan"))


def test_is_blank_empty_string():
    assert is_blank("")
    assert is_blank("  ")


def test_is_blank_value():
    assert not is_blank("KEEP")
    assert not is_blank("True")


def test_missing_column_returns_none(sample_df):
    val, source = resolve_effective(sample_df, r"D:\Windows", "nonexistent_column")
    assert val is None


def test_resolve_all_missing_column(sample_df):
    result = resolve_all(sample_df, "nonexistent_column")
    assert all(v is None for v in result)
