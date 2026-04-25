import argparse
import logging
import sys

import pandas as pd
import yaml


def load_config(path: str = "config.yaml") -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"ERROR: Config file not found: {path}")
        print("Expected config.yaml in the current directory.")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"ERROR: Could not parse config.yaml: {e}")
        sys.exit(1)
    if not isinstance(config, dict):
        print("ERROR: config.yaml must be a YAML mapping.")
        sys.exit(1)
    return config


def _cmd_scan(args, config):
    from scanner import run_scan
    run_scan(args.drive, args.output, config)


def _cmd_triage(args, config):
    from triager import run_triage
    run_triage(args.csv, config)


def _cmd_summarize(args, config):
    from summarizer import run_summarize
    run_summarize(args.csv, config)


def _cmd_organize(args, config):
    from organizer import run_organize
    run_organize(args.csv, config)


def _cmd_copy(args, config):
    from executor import run_copy
    run_copy(args.csv, args.dest, config)


def _cmd_manifest(args, config):
    from executor import run_manifest
    run_manifest(args.csv, config)


def _cmd_delete(args, config):
    if not args.confirm:
        print("ERROR: --confirm flag required to run delete.")
        print("Review delete_manifest.csv first, then re-run with --confirm.")
        sys.exit(1)
    from executor import run_delete
    run_delete(args.csv, args.manifest, config)


def _cmd_resolve(args, config):
    import pandas as pd
    from inheritance import resolve_effective
    df = pd.read_csv(args.csv, dtype=str)
    for col in ("review", "decision"):
        val, source = resolve_effective(df, args.path, col)
        print(f"{col:10s}: {val!r:12}  ({source})")


def main():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="archivist",
        description="Multi-phase drive recovery and archival tool.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p = sub.add_parser("scan", help="Phase 1: scan a drive and produce a CSV")
    p.add_argument("--drive", required=True, help="Drive or path to scan (e.g. D:\\ or /mnt/d/)")
    p.add_argument("--output", required=True, help="Output CSV path (e.g. reports/drive_d.csv)")
    p.add_argument("--config", default="config.yaml")

    # triage
    p = sub.add_parser("triage", help="Phase 2: LLM triage of scanned CSV")
    p.add_argument("--csv", required=True)
    p.add_argument("--config", default="config.yaml")

    # summarize
    p = sub.add_parser("summarize", help="Phase 4: generate file summaries")
    p.add_argument("--csv", required=True)
    p.add_argument("--config", default="config.yaml")

    # organize
    p = sub.add_parser("organize", help="Phase 5: propose taxonomy and organized_path values")
    p.add_argument("--csv", required=True)
    p.add_argument("--config", default="config.yaml")

    # copy
    p = sub.add_parser("copy", help="Phase 6a: copy KEEP/ARCHIVE files to recovery destination")
    p.add_argument("--csv", required=True)
    p.add_argument("--dest", required=True, help="Destination root directory (e.g. E:\\recovered\\)")
    p.add_argument("--config", default="config.yaml")

    # manifest
    p = sub.add_parser("manifest", help="Phase 6b: generate delete manifest for review")
    p.add_argument("--csv", required=True)
    p.add_argument("--config", default="config.yaml")

    # delete
    p = sub.add_parser("delete", help="Phase 6c: delete files listed in manifest (requires --confirm)")
    p.add_argument("--csv", required=True)
    p.add_argument("--manifest", required=True, help="Path to delete_manifest.csv")
    p.add_argument("--confirm", action="store_true", help="Required safety flag to enable deletion")
    p.add_argument("--config", default="config.yaml")

    # resolve
    p = sub.add_parser("resolve", help="Preview effective review/decision for a specific path")
    p.add_argument("--csv", required=True)
    p.add_argument("--path", required=True, help="Path to resolve (must match a row in the CSV)")
    p.add_argument("--config", default="config.yaml")

    args = parser.parse_args()
    config = load_config(args.config)

    dispatch = {
        "scan": _cmd_scan,
        "triage": _cmd_triage,
        "summarize": _cmd_summarize,
        "organize": _cmd_organize,
        "copy": _cmd_copy,
        "manifest": _cmd_manifest,
        "delete": _cmd_delete,
        "resolve": _cmd_resolve,
    }
    dispatch[args.command](args, config)


if __name__ == "__main__":
    main()
