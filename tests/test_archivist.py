import archivist
import executor
import reporter


def test_duplicates_report_cli_dispatch(monkeypatch, tmp_path):
    output = tmp_path / "baseline_duplicates.csv"
    drive_d = tmp_path / "drive_d.csv"
    drive_e = tmp_path / "drive_e.csv"
    called = {}

    monkeypatch.setattr(archivist, "load_config", lambda path: {})

    def fake_run_duplicates_report(csv_paths, output_csv):
        called["csv_paths"] = csv_paths
        called["output_csv"] = output_csv

    monkeypatch.setattr(reporter, "run_duplicates_report", fake_run_duplicates_report)
    monkeypatch.setattr(
        "sys.argv",
        [
            "archivist.py",
            "duplicates-report",
            "--csv",
            str(drive_d),
            "--csv",
            str(drive_e),
            "--output",
            str(output),
        ],
    )

    archivist.main()

    assert called["csv_paths"] == [str(drive_d), str(drive_e)]
    assert called["output_csv"] == str(output)


def test_manifest_all_cli_dispatch(monkeypatch, tmp_path):
    output_dir = tmp_path / "manifests"
    drive_d = tmp_path / "drive_d.csv"
    drive_e = tmp_path / "drive_e.csv"
    called = {}

    monkeypatch.setattr(archivist, "load_config", lambda path: {})

    def fake_run_manifest_batch(csv_paths, config, output_dir=None):
        called["csv_paths"] = csv_paths
        called["config"] = config
        called["output_dir"] = output_dir

    monkeypatch.setattr(executor, "run_manifest_batch", fake_run_manifest_batch)
    monkeypatch.setattr(
        "sys.argv",
        [
            "archivist.py",
            "manifest-all",
            "--csv",
            str(drive_d),
            "--csv",
            str(drive_e),
            "--output-dir",
            str(output_dir),
        ],
    )

    archivist.main()

    assert called["csv_paths"] == [str(drive_d), str(drive_e)]
    assert called["output_dir"] == str(output_dir)