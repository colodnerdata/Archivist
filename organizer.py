import json
import logging
import re

import pandas as pd
from tqdm import tqdm

import llm_client
from csv_utils import safe_write_csv
from inheritance import resolve_all

logger = logging.getLogger(__name__)


def run_organize(csv_path: str, config: dict) -> None:
    model = config["triage_model"]  # reuse triage model for organize
    llm_client.check_ollama(config["ollama_base_url"], [model])

    df = pd.read_csv(csv_path, dtype=str)
    eff_decision = resolve_all(df, "decision")

    keep_mask = eff_decision.apply(
        lambda d: d is not None and str(d).strip().upper() in ("KEEP", "ARCHIVE")
    )
    has_summary = df.get("summary", pd.Series(dtype=str)).apply(
        lambda s: isinstance(s, str) and s.strip() != "" and not s.startswith("UNREADABLE") and not s.startswith("LLM_ERROR")
    )
    eligible = df[keep_mask & has_summary & (df.get("is_dir", "False").str.lower() != "true")]

    if eligible.empty:
        print("No files with summaries found to organize. Run summarize first.")
        return

    file_summaries = eligible[["path", "filename", "summary"]].to_dict("records")

    print(f"\nOrganizing {len(file_summaries)} files...\n")
    progress = tqdm(total=2, desc="Organizing", unit=" step")

    try:
        progress.set_postfix_str("taxonomy")
        taxonomy_prompt = _build_taxonomy_prompt(file_summaries)
        taxonomy_response = llm_client.generate(
            config["ollama_base_url"], model, taxonomy_prompt, temperature=0.2
        )
        progress.update(1)
        print("=== Proposed Taxonomy ===")
        print(taxonomy_response)
        print("========================\n")

        progress.set_postfix_str("assignments")
        assignment_prompt = _build_assignment_prompt(taxonomy_response, file_summaries)
        assignment_response = llm_client.generate(
            config["ollama_base_url"], model, assignment_prompt, temperature=0.1
        )

        try:
            assignments = _parse_assignment_response(assignment_response)
        except ValueError as e:
            logger.error("Failed to parse assignment response: %s", e)
            print(f"ERROR: Could not parse LLM assignment response: {e}")
            return

        progress.update(1)

        by_path = {a["original_path"]: a["organized_path"] for a in assignments}

        if "organized_path" not in df.columns:
            df["organized_path"] = ""

        assigned_count = 0
        for idx in eligible.index:
            path = str(df.at[idx, "path"])
            if path in by_path:
                df.at[idx, "organized_path"] = by_path[path]
                assigned_count += 1

        safe_write_csv(df, csv_path)
        print(f"Assigned organized_path for {assigned_count}/{len(file_summaries)} files.")
        print("Review the organized_path column in the CSV before running Phase 6 (copy).")
    finally:
        progress.close()


def _build_taxonomy_prompt(file_summaries: list[dict]) -> str:
    summaries_json = json.dumps(
        [{"path": s["path"], "filename": s["filename"], "summary": s["summary"]} for s in file_summaries]
    )
    return f"""You are organizing recovered files from an old personal computer into a clean
directory structure. Below are summaries of all files to be kept.

Propose a top-level category taxonomy (5-12 categories) that reflects the
actual content. Do not use generic categories like "Miscellaneous" unless
truly necessary. Base categories on what you actually see in the summaries.

Recovery root: recovered/

Respond with a taxonomy block listing each category and a one-line description.
No JSON, just readable text.

Files:
{summaries_json}"""


def _build_assignment_prompt(taxonomy: str, file_summaries: list[dict]) -> str:
    summaries_json = json.dumps(
        [{"path": s["path"], "filename": s["filename"], "summary": s["summary"]} for s in file_summaries]
    )
    return f"""You are assigning files to a recovery directory structure using this taxonomy:

{taxonomy}

Assign each file to a category and propose a relative path within that category.
Paths should be meaningful (e.g. "Finance/Taxes/2018/1040.pdf"), not flat dumps.
The root is "recovered/".

Respond ONLY with a valid JSON array. Each object must have:
  original_path: the exact original path from the input
  organized_path: the proposed relative path under recovered/ (do NOT include "recovered/" prefix)

No preamble, no markdown, no text outside the JSON array.

Files:
{summaries_json}"""


def _parse_assignment_response(response_text: str) -> list[dict]:
    text = re.sub(r"^```(?:json)?\s*", "", response_text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text.strip()).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse error: {e}") from e

    if not isinstance(data, list):
        raise ValueError("Response is not a JSON array")

    for item in data:
        if "original_path" not in item or "organized_path" not in item:
            raise ValueError(f"Item missing required keys: {item}")

    return data
