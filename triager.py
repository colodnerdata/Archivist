import json
import logging
import re

import pandas as pd
from tqdm import tqdm

import llm_client
from csv_utils import safe_write_csv

logger = logging.getLogger(__name__)

_STRICT_SUFFIX = (
    "\n\nIMPORTANT: Your previous response was not valid JSON. "
    "Respond ONLY with a valid JSON array. No preamble, no markdown, no text outside the array."
)


def run_triage(csv_path: str, config: dict) -> None:
    model = config["triage_model"]
    llm_client.check_ollama(config["ollama_base_url"], [model])

    debug_print_prompt = bool(config.get("triage_debug_print_prompt", False))
    debug_stream_output = bool(config.get("triage_debug_stream_output", False))
    debug_prompt_max_chars = int(config.get("triage_debug_prompt_max_chars", 4000))
    debug_prompt_batches = int(config.get("triage_debug_prompt_batches", 1))

    df = pd.read_csv(csv_path, dtype=str)
    df = _mark_duplicates(df)

    needs_triage = df[
        (df.get("recommendation", pd.Series(dtype=str)).isna() | (df.get("recommendation", pd.Series(dtype=str)) == ""))
        & (df.get("is_duplicate", "False").str.lower() != "true")
    ].index.tolist()

    batch_size = int(config.get("triage_batch_size", 50))
    batches = [needs_triage[i:i + batch_size] for i in range(0, len(needs_triage), batch_size)]

    for batch_num, batch_indices in enumerate(tqdm(batches, desc="Triaging", unit=" batches"), start=1):
        rows = df.loc[batch_indices, ["path", "filename", "extension", "is_dir", "size_bytes", "modified"]].to_dict("records")
        prompt = _build_batch_prompt(rows)

        if debug_print_prompt and batch_num <= debug_prompt_batches:
            shown = prompt[:debug_prompt_max_chars]
            if len(prompt) > debug_prompt_max_chars:
                shown += "\n... [prompt truncated]"
            print(f"\n--- TRIAGE PROMPT batch {batch_num}/{len(batches)} ({len(prompt)} chars) ---")
            print(shown)
            print("--- END TRIAGE PROMPT ---\n")

        if debug_stream_output:
            print(f"[triage] batch {batch_num}/{len(batches)} streaming response from model '{model}'...")

        try:
            response = llm_client.generate(
                config["ollama_base_url"],
                model,
                prompt,
                stream=debug_stream_output,
                stream_to_stdout=debug_stream_output,
            )
            results = _parse_llm_response(response)
        except (ValueError, Exception) as e:
            logger.warning("First triage attempt failed (%s), retrying with strict prompt", e)
            try:
                strict_prompt = prompt + _STRICT_SUFFIX
                if debug_print_prompt and batch_num <= debug_prompt_batches:
                    shown = strict_prompt[:debug_prompt_max_chars]
                    if len(strict_prompt) > debug_prompt_max_chars:
                        shown += "\n... [strict prompt truncated]"
                    print(f"\n--- TRIAGE STRICT PROMPT batch {batch_num}/{len(batches)} ({len(strict_prompt)} chars) ---")
                    print(shown)
                    print("--- END TRIAGE STRICT PROMPT ---\n")

                response = llm_client.generate(
                    config["ollama_base_url"],
                    model,
                    strict_prompt,
                    stream=debug_stream_output,
                    stream_to_stdout=debug_stream_output,
                )
                results = _parse_llm_response(response)
            except Exception as e2:
                logger.error("Second triage attempt failed (%s), marking batch as REVIEW", e2)
                for idx in batch_indices:
                    df.at[idx, "recommendation"] = "REVIEW"
                    df.at[idx, "confidence"] = "0.5"
                    df.at[idx, "comment"] = "LLM parse error — manual review needed"
                safe_write_csv(df, csv_path)
                continue

        df = _apply_batch_results(df, results, batch_indices)
        safe_write_csv(df, csv_path)


def _mark_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    dup_mask = df.get("is_duplicate", pd.Series(index=df.index, dtype=str)).str.lower() == "true"
    baseline_dup_mask = dup_mask & (
        df.get("duplicate_kind", pd.Series(index=df.index, dtype=str)).fillna("") == "baseline_scan"
    )

    if "decision" not in df.columns:
        df["decision"] = ""
    decision = df.get("decision", pd.Series(index=df.index, dtype=str))

    df.loc[dup_mask, "recommendation"] = "SKIP"
    df.loc[dup_mask, "confidence"] = "0.99"
    blank_decision = decision.isna() | (decision == "")
    df.loc[baseline_dup_mask & blank_decision, "decision"] = "DELETE"
    # Only set comment if not already set (scanner may have filled it in)
    no_comment = dup_mask & (
        df.get("comment", pd.Series(index=df.index, dtype=str)).isna()
        | (df.get("comment", pd.Series(index=df.index, dtype=str)) == "")
    )
    df.loc[no_comment, "comment"] = "Duplicate — already in kept_hashes registry"
    return df


def _build_batch_prompt(rows: list[dict]) -> str:
    items_json = json.dumps(rows, default=str)
    return f"""You are reviewing files and directories from an old personal Windows computer.
For each item, provide:
  recommendation: INTERESTING | REVIEW | SKIP
  confidence: float 0.0 to 1.0
  comment: one sentence explaining your reasoning

Definitions:
  INTERESTING - likely unique personal content (financial docs, legal, creative
                work, personal projects, original writing, irreplaceable photos)
  REVIEW      - worth a closer look but uncertain (personal photos, mixed dirs,
                ambiguous filenames)
  SKIP        - generic, system, installer, or downloaded content unlikely to
                be unique

Base your assessment ONLY on: path, filename, extension, size_bytes, modified.
Do NOT hallucinate file contents.

Guidance:
- .docx, .pdf, .xlsx in user home directories → likely INTERESTING
- .jpg, .png, .psd, .raw, .tiff in personal folders → REVIEW (needs vision)
- .exe, .msi, .dll, .sys → SKIP
- Directories named Windows, Program Files, AppData → SKIP confidence 0.99
- Directories with personal project names, years, or proper nouns → INTERESTING
- Very small files (<1KB) outside project directories → SKIP

Respond ONLY with a valid JSON array. Each object must have keys:
path, recommendation, confidence, comment.
No preamble, no markdown, no explanation outside the JSON.

Items:
{items_json}"""


def _parse_llm_response(response_text: str) -> list[dict]:
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", response_text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text.strip())
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse error: {e}") from e

    if not isinstance(data, list):
        raise ValueError("Response is not a JSON array")

    for item in data:
        for key in ("path", "recommendation", "confidence", "comment"):
            if key not in item:
                raise ValueError(f"Item missing required key '{key}': {item}")

    return data


def _apply_batch_results(df: pd.DataFrame, results: list[dict], batch_indices: list[int]) -> pd.DataFrame:
    result_by_path = {str(r["path"]): r for r in results}

    for idx in batch_indices:
        path = str(df.at[idx, "path"])
        if path in result_by_path:
            r = result_by_path[path]
            df.at[idx, "recommendation"] = str(r["recommendation"])
            df.at[idx, "confidence"] = str(r["confidence"])
            df.at[idx, "comment"] = str(r["comment"])
        else:
            # LLM didn't return a result for this path
            df.at[idx, "recommendation"] = "REVIEW"
            df.at[idx, "confidence"] = "0.5"
            df.at[idx, "comment"] = "LLM did not return a result for this item"

    return df
