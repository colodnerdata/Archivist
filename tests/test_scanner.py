import csv
import errno
import os

import pytest

import scanner
from scanner import _compute_md5, _load_kept_hashes, _load_seen_paths, run_scan


@pytest.fixture
def basic_config(tmp_path):
    return {
        "kept_hashes_path": str(tmp_path / "kept_hashes.csv"),
        "exclude_dirs": ["AppData", "$RECYCLE.BIN"],
        "exclude_extensions": [".exe", ".dll"],
    }


def test_scan_writes_csv(tmp_path, basic_config):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.txt").write_text("hello")
    (tmp_path / "docs" / "setup.exe").write_bytes(b"\x00")

    out_csv = str(tmp_path / "out.csv")
    run_scan(str(tmp_path), out_csv, basic_config)

    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    paths = [r["path"] for r in rows]
    filenames = [r["filename"] for r in rows]

    assert "note.txt" in filenames
    assert "setup.exe" not in filenames  # excluded extension
    assert any(r["is_dir"] == "True" for r in rows)


def test_excluded_dir_gets_skip_row(tmp_path, basic_config):
    (tmp_path / "AppData").mkdir()
    (tmp_path / "AppData" / "file.dat").write_text("x")

    out_csv = str(tmp_path / "out.csv")
    run_scan(str(tmp_path), out_csv, basic_config)

    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    appdata_rows = [r for r in rows if r["filename"] == "" and "AppData" in r["path"]]
    assert any(r["recommendation"] == "SKIP" for r in appdata_rows)
    # The file inside AppData should not be scanned (dir pruned)
    filenames = [r["filename"] for r in rows]
    assert "file.dat" not in filenames


def test_scan_resumes(tmp_path, basic_config):
    (tmp_path / "file1.txt").write_text("first")
    out_csv = str(tmp_path / "out.csv")
    run_scan(str(tmp_path), out_csv, basic_config)

    with open(out_csv, newline="", encoding="utf-8") as f:
        count_first = sum(1 for _ in csv.DictReader(f))

    # Add a new file and re-run
    (tmp_path / "file2.txt").write_text("second")
    run_scan(str(tmp_path), out_csv, basic_config)

    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    filenames = [r["filename"] for r in rows]
    assert "file1.txt" in filenames
    assert "file2.txt" in filenames
    # No duplicate rows for file1.txt
    assert filenames.count("file1.txt") == 1


def test_md5_duplicate_detection(tmp_path, basic_config):
    scan_dir = tmp_path / "drive"
    scan_dir.mkdir()
    content = b"identical content"
    (scan_dir / "a.txt").write_bytes(content)
    (scan_dir / "b.txt").write_bytes(content)

    out_csv = str(tmp_path / "out.csv")
    run_scan(str(scan_dir), out_csv, basic_config)

    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["is_dir"] == "False"]

    dups = [r for r in rows if r["is_duplicate"].lower() == "true"]
    non_dups = [r for r in rows if r["is_duplicate"].lower() == "false"]
    assert len(dups) == 1
    assert len(non_dups) == 1
    dup_filenames = [r["filename"] for r in dups]
    assert any(fn in ("a.txt", "b.txt") for fn in dup_filenames)
    assert dups[0]["duplicate_kind"] == "same_drive"
    assert dups[0]["duplicate_source_path"] != ""


def test_baseline_scan_duplicate_detection(tmp_path, basic_config):
    baseline_csv = tmp_path / "drive_c.csv"
    baseline_path = r"C:\Users\Stephen\Documents\keep.txt"
    baseline_hash, _ = _compute_md5(str(_write_file(tmp_path / "baseline_source.txt", b"same content")))
    baseline_csv.write_text(
        "path,md5_hash\n"
        f"{baseline_path},{baseline_hash}\n",
        encoding="utf-8",
    )
    basic_config["baseline_scan_csv"] = str(baseline_csv)

    scan_dir = tmp_path / "drive_d"
    scan_dir.mkdir()
    (scan_dir / "dup.txt").write_bytes(b"same content")

    out_csv = str(tmp_path / "out.csv")
    run_scan(str(scan_dir), out_csv, basic_config)

    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["is_dir"] == "False"]

    assert len(rows) == 1
    assert rows[0]["is_duplicate"].lower() == "true"
    assert rows[0]["duplicate_kind"] == "baseline_scan"
    assert rows[0]["duplicate_source_path"] == baseline_path
    assert "baseline scan file" in rows[0]["comment"]


def test_compute_md5_consistent(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"test data 12345")
    h1, err1 = _compute_md5(str(f))
    h2, err2 = _compute_md5(str(f))
    assert h1 == h2
    assert len(h1) == 32
    assert err1 == ""
    assert err2 == ""


def test_compute_md5_missing_file(tmp_path):
    md5, err = _compute_md5(str(tmp_path / "nonexistent.txt"))
    assert md5 == ""


def test_load_kept_hashes_missing_file(tmp_path):
    result = _load_kept_hashes(str(tmp_path / "no_such_file.csv"))
    assert result == {}


def test_load_seen_paths(tmp_path):
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("path,filename\nD:\\foo\\bar.txt,bar.txt\nD:\\baz,\n")
    result = _load_seen_paths(str(csv_path))
    assert "D:\\foo\\bar.txt" in result
    assert "D:\\baz" in result


def test_inaccessible_dir_gets_recorded(tmp_path, basic_config, monkeypatch):
    denied_path = str(tmp_path / "protected")
    denied_norm = scanner._norm(denied_path)

    def fake_walk(path, onerror=None):
        if onerror is not None:
            onerror(PermissionError(errno.EACCES, "Permission denied", denied_path))
        yield str(tmp_path), [], []

    monkeypatch.setattr(scanner.os, "walk", fake_walk)

    out_csv = str(tmp_path / "out.csv")
    run_scan(str(tmp_path), out_csv, basic_config)

    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    denied_rows = [r for r in rows if r["path"] == denied_norm]
    assert len(denied_rows) == 1
    assert denied_rows[0]["is_dir"] == "True"
    assert denied_rows[0]["recommendation"] == "REVIEW"
    assert "INACCESSIBLE" in denied_rows[0]["comment"]


def test_inaccessible_dir_updates_progress(tmp_path, basic_config, monkeypatch):
    denied_path = str(tmp_path / "protected")
    progress_updates = []

    class FakeTqdm:
        def update(self, amount):
            progress_updates.append(amount)

        def close(self):
            pass

    def fake_walk(path, onerror=None):
        if onerror is not None:
            onerror(PermissionError(errno.EACCES, "Permission denied", denied_path))
        yield str(tmp_path), [], []

    monkeypatch.setattr(scanner, "tqdm", lambda *args, **kwargs: FakeTqdm())
    monkeypatch.setattr(scanner.os, "walk", fake_walk)

    out_csv = str(tmp_path / "out.csv")
    run_scan(str(tmp_path), out_csv, basic_config)

    assert progress_updates == [1, 1]


def test_scan_progress_mentions_ctrl_c(tmp_path, basic_config, monkeypatch):
    captured = {}

    class FakeTqdm:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def update(self, amount):
            captured["last_update"] = amount

        def close(self):
            captured["closed"] = True

    def fake_tqdm(*args, **kwargs):
        return FakeTqdm(*args, **kwargs)

    monkeypatch.setattr(scanner, "tqdm", fake_tqdm)

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.txt").write_text("hello")
    out_csv = str(tmp_path / "out.csv")
    run_scan(str(tmp_path), out_csv, basic_config)

    assert "Ctrl+C" in captured["desc"]
    assert captured["total"] >= 2
    assert captured["closed"] is True


def test_scan_interrupt_prints_resume_message(tmp_path, basic_config, monkeypatch, capsys):
    def interrupted_walk(path, onerror=None):
        raise KeyboardInterrupt
        yield  # pragma: no cover

    monkeypatch.setattr(scanner.os, "walk", interrupted_walk)

    out_csv = str(tmp_path / "out.csv")
    run_scan(str(tmp_path), out_csv, basic_config)

    captured = capsys.readouterr()
    assert "resume" in captured.err.lower()


def _write_file(path, content: bytes):
    """Write bytes to ``path`` and return the same path object for chaining."""
    path.write_bytes(content)
    return path
