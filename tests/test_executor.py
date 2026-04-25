import csv
import os

import pandas as pd
import pytest

from executor import run_copy, run_manifest


@pytest.fixture
def basic_config(tmp_path):
    return {
        "kept_hashes_path": str(tmp_path / "kept_hashes.csv"),
    }


def _write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_copy_creates_dest_file(tmp_path, basic_config):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_file = src_dir / "doc.txt"
    src_file.write_text("important content")

    dest_root = tmp_path / "dest"
    csv_path = str(tmp_path / "drive.csv")

    _write_csv(csv_path, [{
        "path": str(src_file),
        "filename": "doc.txt",
        "extension": ".txt",
        "is_dir": "False",
        "size_bytes": str(src_file.stat().st_size),
        "modified": "",
        "md5_hash": "",
        "is_duplicate": "False",
        "recommendation": "INTERESTING",
        "confidence": "0.9",
        "comment": "",
        "review": "True",
        "decision": "KEEP",
        "summary": "A document.",
        "organized_path": "Documents/doc.txt",
        "copy_status": "",
        "delete_status": "",
    }])

    run_copy(csv_path, str(dest_root), basic_config)

    assert (dest_root / "Documents" / "doc.txt").exists()

    df = pd.read_csv(csv_path, dtype=str)
    assert df.loc[0, "copy_status"] == "COPIED"


def test_copy_skips_already_copied(tmp_path, basic_config):
    src_file = tmp_path / "file.txt"
    src_file.write_text("data")
    dest_root = tmp_path / "dest"
    csv_path = str(tmp_path / "drive.csv")

    _write_csv(csv_path, [{
        "path": str(src_file),
        "filename": "file.txt",
        "extension": ".txt",
        "is_dir": "False",
        "size_bytes": "4",
        "modified": "",
        "md5_hash": "",
        "is_duplicate": "False",
        "recommendation": "",
        "confidence": "",
        "comment": "",
        "review": "",
        "decision": "KEEP",
        "summary": "",
        "organized_path": "file.txt",
        "copy_status": "COPIED",  # already done
        "delete_status": "",
    }])

    run_copy(csv_path, str(dest_root), basic_config)

    # dest should NOT be created because it was already COPIED
    assert not (dest_root / "file.txt").exists()


def test_manifest_includes_inherited_delete(tmp_path, basic_config):
    csv_path = str(tmp_path / "drive.csv")

    _write_csv(csv_path, [
        {
            "path": r"D:\Windows",
            "filename": "",
            "extension": "",
            "is_dir": "True",
            "size_bytes": "0",
            "modified": "",
            "md5_hash": "",
            "is_duplicate": "False",
            "recommendation": "SKIP",
            "confidence": "0.99",
            "comment": "",
            "review": "",
            "decision": "DELETE",
            "summary": "",
            "organized_path": "",
            "copy_status": "",
            "delete_status": "",
        },
        {
            "path": r"D:\Windows\notepad.exe",
            "filename": "notepad.exe",
            "extension": ".exe",
            "is_dir": "False",
            "size_bytes": "100000",
            "modified": "",
            "md5_hash": "",
            "is_duplicate": "False",
            "recommendation": "SKIP",
            "confidence": "0.99",
            "comment": "",
            "review": "",
            "decision": "",  # inherits DELETE from parent
            "summary": "",
            "organized_path": "",
            "copy_status": "",
            "delete_status": "",
        },
    ])

    run_manifest(csv_path, basic_config)

    manifest_path = os.path.join(os.path.dirname(csv_path), "delete_manifest.csv")
    assert os.path.exists(manifest_path)

    with open(manifest_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    paths_in_manifest = [r["path"] for r in rows]
    assert r"D:\Windows\notepad.exe" in paths_in_manifest


def test_manifest_does_not_include_keep(tmp_path, basic_config):
    csv_path = str(tmp_path / "drive.csv")

    _write_csv(csv_path, [{
        "path": r"D:\Users\Stephen\doc.txt",
        "filename": "doc.txt",
        "extension": ".txt",
        "is_dir": "False",
        "size_bytes": "500",
        "modified": "",
        "md5_hash": "",
        "is_duplicate": "False",
        "recommendation": "INTERESTING",
        "confidence": "0.9",
        "comment": "",
        "review": "True",
        "decision": "KEEP",
        "summary": "A doc.",
        "organized_path": "Documents/doc.txt",
        "copy_status": "",
        "delete_status": "",
    }])

    run_manifest(csv_path, basic_config)

    manifest_path = os.path.join(os.path.dirname(csv_path), "delete_manifest.csv")
    with open(manifest_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 0
