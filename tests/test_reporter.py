import csv

from reporter import run_duplicates_report


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_duplicates_report_includes_only_baseline_duplicates(tmp_path):
    drive_d = tmp_path / "drive_d.csv"
    drive_e = tmp_path / "drive_e.csv"
    output = tmp_path / "reports" / "baseline_duplicates.csv"

    _write_csv(drive_d, [
        {
            "path": r"D:\Users\Stephen\dup.txt",
            "filename": "dup.txt",
            "size_bytes": "100",
            "md5_hash": "abc",
            "is_dir": "False",
            "duplicate_kind": "baseline_scan",
            "duplicate_source_path": r"C:\Users\Stephen\dup.txt",
            "recommendation": "SKIP",
            "decision": "DELETE",
            "comment": "Duplicate of baseline scan file C:\\Users\\Stephen\\dup.txt",
        },
        {
            "path": r"D:\Users\Stephen\local-copy.txt",
            "filename": "local-copy.txt",
            "size_bytes": "50",
            "md5_hash": "def",
            "is_dir": "False",
            "duplicate_kind": "same_drive",
            "duplicate_source_path": r"D:\Users\Stephen\original.txt",
            "recommendation": "SKIP",
            "decision": "",
            "comment": "Duplicate of D:\\Users\\Stephen\\original.txt (same drive)",
        },
    ])
    _write_csv(drive_e, [
        {
            "path": r"E:\Users\Stephen\dup2.txt",
            "filename": "dup2.txt",
            "size_bytes": "200",
            "md5_hash": "ghi",
            "is_dir": "False",
            "duplicate_kind": "baseline_scan",
            "duplicate_source_path": r"C:\Users\Stephen\dup2.txt",
            "recommendation": "SKIP",
            "decision": "DELETE",
            "comment": "Duplicate of baseline scan file C:\\Users\\Stephen\\dup2.txt",
        },
    ])

    run_duplicates_report([str(drive_d), str(drive_e)], str(output))

    with open(output, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2
    assert {row["path"] for row in rows} == {r"D:\Users\Stephen\dup.txt", r"E:\Users\Stephen\dup2.txt"}
    assert {row["source_csv"] for row in rows} == {str(drive_d), str(drive_e)}


def test_duplicates_report_writes_header_when_empty(tmp_path):
    drive_d = tmp_path / "drive_d.csv"
    output = tmp_path / "baseline_duplicates.csv"

    _write_csv(drive_d, [
        {
            "path": r"D:\Users\Stephen\unique.txt",
            "filename": "unique.txt",
            "size_bytes": "100",
            "md5_hash": "abc",
            "is_dir": "False",
            "duplicate_kind": "",
            "duplicate_source_path": "",
            "recommendation": "INTERESTING",
            "decision": "",
            "comment": "",
        },
    ])

    run_duplicates_report([str(drive_d)], str(output))

    with open(output, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fieldnames = rows[0].keys() if rows else next(csv.reader(open(output, newline="", encoding="utf-8")))

    assert rows == []
    assert list(fieldnames) == [
        "source_csv",
        "path",
        "filename",
        "size_bytes",
        "md5_hash",
        "duplicate_source_path",
        "recommendation",
        "decision",
        "comment",
    ]