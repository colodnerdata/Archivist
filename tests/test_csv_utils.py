import os

import pandas as pd
import pytest

import csv_utils


def test_safe_write_csv_basic(tmp_path):
    path = str(tmp_path / "data.csv")
    df = pd.DataFrame([{"a": 1}, {"a": 2}])

    csv_utils.safe_write_csv(df, path)

    out = pd.read_csv(path)
    assert out["a"].tolist() == [1, 2]


def test_safe_write_csv_retries_then_succeeds(tmp_path, monkeypatch):
    path = str(tmp_path / "data.csv")
    df = pd.DataFrame([{"a": 1}])

    calls = {"count": 0}
    real_replace = os.replace

    def flaky_replace(src, dst):
        calls["count"] += 1
        if calls["count"] < 3:
            raise PermissionError("file is locked")
        return real_replace(src, dst)

    monkeypatch.setattr(csv_utils.os, "replace", flaky_replace)

    csv_utils.safe_write_csv(df, path, retries=3, retry_delay_seconds=0)

    assert calls["count"] == 3
    out = pd.read_csv(path)
    assert out["a"].tolist() == [1]


def test_safe_write_csv_writes_pending_on_persistent_lock(tmp_path, monkeypatch):
    path = str(tmp_path / "data.csv")
    pending_path = path + ".pending"
    tmp_file = path + ".tmp"
    df = pd.DataFrame([{"a": 5}])

    real_replace = os.replace

    def locked_for_original(src, dst):
        if dst == path:
            raise PermissionError("file is locked")
        return real_replace(src, dst)

    monkeypatch.setattr(csv_utils.os, "replace", locked_for_original)

    with pytest.raises(PermissionError, match="Latest data was saved"):
        csv_utils.safe_write_csv(df, path, retries=1, retry_delay_seconds=0)

    assert os.path.exists(pending_path)
    assert not os.path.exists(tmp_file)
    out = pd.read_csv(pending_path)
    assert out["a"].tolist() == [5]
