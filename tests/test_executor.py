import csv
import os

import pandas as pd
import pytest

from executor import run_copy, run_delete, run_manifest, run_manifest_batch


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

    run_manifest([csv_path], basic_config)

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

    run_manifest([csv_path], basic_config)

    manifest_path = os.path.join(os.path.dirname(csv_path), "delete_manifest.csv")
    with open(manifest_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 0


def test_manifest_includes_explicit_delete_from_baseline_duplicate(tmp_path, basic_config):
    csv_path = str(tmp_path / "drive.csv")

    _write_csv(csv_path, [{
        "path": r"D:\Users\Stephen\dup.txt",
        "filename": "dup.txt",
        "extension": ".txt",
        "is_dir": "False",
        "size_bytes": "500",
        "modified": "",
        "md5_hash": "abc123",
        "is_duplicate": "True",
        "duplicate_kind": "baseline_scan",
        "duplicate_source_path": r"C:\Users\Stephen\dup.txt",
        "recommendation": "SKIP",
        "confidence": "0.99",
        "comment": "Duplicate of baseline scan file C:\\Users\\Stephen\\dup.txt",
        "review": "",
        "decision": "DELETE",
        "summary": "",
        "organized_path": "",
        "copy_status": "",
        "delete_status": "",
    }])

    run_manifest([csv_path], basic_config)

    manifest_path = os.path.join(os.path.dirname(csv_path), "delete_manifest.csv")
    with open(manifest_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    assert rows[0]["path"] == r"D:\Users\Stephen\dup.txt"


def test_manifest_batch_writes_unique_files(tmp_path, basic_config):
    drive_d_csv = str(tmp_path / "drive_d.csv")
    drive_e_csv = str(tmp_path / "drive_e.csv")

    _write_csv(drive_d_csv, [{
        "path": r"D:\Users\Stephen\dup.txt",
        "filename": "dup.txt",
        "extension": ".txt",
        "is_dir": "False",
        "size_bytes": "100",
        "modified": "",
        "md5_hash": "abc",
        "is_duplicate": "True",
        "recommendation": "SKIP",
        "confidence": "0.99",
        "comment": "",
        "review": "",
        "decision": "DELETE",
        "summary": "",
        "organized_path": "",
        "copy_status": "",
        "delete_status": "",
    }])
    _write_csv(drive_e_csv, [{
        "path": r"E:\Users\Stephen\dup2.txt",
        "filename": "dup2.txt",
        "extension": ".txt",
        "is_dir": "False",
        "size_bytes": "200",
        "modified": "",
        "md5_hash": "def",
        "is_duplicate": "True",
        "recommendation": "SKIP",
        "confidence": "0.99",
        "comment": "",
        "review": "",
        "decision": "DELETE",
        "summary": "",
        "organized_path": "",
        "copy_status": "",
        "delete_status": "",
    }])

    manifest_paths = run_manifest_batch([drive_d_csv, drive_e_csv], basic_config)

    assert manifest_paths == [
        os.path.join(str(tmp_path), "drive_d_delete_manifest.csv"),
        os.path.join(str(tmp_path), "drive_e_delete_manifest.csv"),
    ]
    assert os.path.exists(manifest_paths[0])
    assert os.path.exists(manifest_paths[1])

    with open(manifest_paths[0], newline="", encoding="utf-8") as f:
        rows_d = list(csv.DictReader(f))
    with open(manifest_paths[1], newline="", encoding="utf-8") as f:
        rows_e = list(csv.DictReader(f))

    assert rows_d[0]["path"] == r"D:\Users\Stephen\dup.txt"
    assert rows_e[0]["path"] == r"E:\Users\Stephen\dup2.txt"


def test_delete_rejects_stale_manifest(tmp_path, basic_config):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    doomed = src_dir / "doomed.txt"
    doomed.write_text("delete me")
    spared = src_dir / "spared.txt"
    spared.write_text("keep me")

    csv_path = str(tmp_path / "drive.csv")
    manifest_path = str(tmp_path / "delete_manifest.csv")

    _write_csv(csv_path, [
        {
            "path": str(doomed),
            "filename": "doomed.txt",
            "extension": ".txt",
            "is_dir": "False",
            "size_bytes": str(doomed.stat().st_size),
            "modified": "",
            "md5_hash": "",
            "is_duplicate": "False",
            "recommendation": "",
            "confidence": "",
            "comment": "",
            "review": "",
            "decision": "KEEP",
            "summary": "",
            "organized_path": "",
            "copy_status": "",
            "delete_status": "",
        },
        {
            "path": str(spared),
            "filename": "spared.txt",
            "extension": ".txt",
            "is_dir": "False",
            "size_bytes": str(spared.stat().st_size),
            "modified": "",
            "md5_hash": "",
            "is_duplicate": "False",
            "recommendation": "",
            "confidence": "",
            "comment": "",
            "review": "",
            "decision": "DELETE",
            "summary": "",
            "organized_path": "",
            "copy_status": "",
            "delete_status": "",
        },
    ])

    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["path", "filename", "size_bytes", "decision_source"])
        writer.writeheader()
        writer.writerow({
            "path": str(doomed),
            "filename": "doomed.txt",
            "size_bytes": str(doomed.stat().st_size),
            "decision_source": "EXPLICIT",
        })

    with pytest.raises(ValueError, match="Manifest does not match"):
        run_delete([csv_path], manifest_path, basic_config)

    assert doomed.exists()
    assert spared.exists()


def test_delete_accepts_matching_manifest(tmp_path, basic_config):
    src_file = tmp_path / "doomed.txt"
    src_file.write_text("delete me")
    csv_path = str(tmp_path / "drive.csv")

    _write_csv(csv_path, [{
        "path": str(src_file),
        "filename": "doomed.txt",
        "extension": ".txt",
        "is_dir": "False",
        "size_bytes": str(src_file.stat().st_size),
        "modified": "",
        "md5_hash": "",
        "is_duplicate": "False",
        "recommendation": "",
        "confidence": "",
        "comment": "",
        "review": "",
        "decision": "DELETE",
        "summary": "",
        "organized_path": "",
        "copy_status": "",
        "delete_status": "",
    }])

    run_manifest([csv_path], basic_config)
    manifest_path = os.path.join(os.path.dirname(csv_path), "delete_manifest.csv")

    run_delete([csv_path], manifest_path, basic_config)

    assert not src_file.exists()
    df = pd.read_csv(csv_path, dtype=str)
    assert df.loc[0, "delete_status"] == "DELETED"
