import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm

import llm_client
from csv_utils import safe_write_csv

logger = logging.getLogger(__name__)

_STRICT_SUFFIX = (
    "\n\nIMPORTANT: Your previous response was not valid JSON. "
    "Respond ONLY with a valid JSON array. No preamble, no markdown, no text outside the array."
)


def run_mark_duplicates(csv_path: str) -> None:
    """Phase 2a: auto-mark duplicates and write results back to CSV. No LLM calls."""
    df = pd.read_csv(csv_path, dtype=str)
    df = _mark_duplicates(df)

    dup_mask = df.get("is_duplicate", pd.Series(index=df.index, dtype=str)).str.lower() == "true"
    kind_col = df.get("duplicate_kind", pd.Series(index=df.index, dtype=str)).fillna("")
    decision_col = df.get("decision", pd.Series(index=df.index, dtype=str)).str.upper()

    baseline_count = int((dup_mask & (kind_col == "baseline_scan")).sum())
    kept_count     = int((dup_mask & (kind_col == "kept_hashes")).sum())
    same_count     = int((dup_mask & (kind_col == "same_drive")).sum())
    auto_delete    = int((dup_mask & (kind_col == "baseline_scan") & (decision_col == "DELETE")).sum())
    total_dup      = int(dup_mask.sum())
    remaining      = int((~dup_mask).sum())

    safe_write_csv(df, csv_path)

    print(f"\nDuplicate marking complete -- {csv_path}")
    if total_dup == 0:
        print("  No duplicates found.")
    else:
        if baseline_count:
            print(f"  Baseline (exact copy on C:\\):  {baseline_count:>6,}  -> decision: DELETE")
        if kept_count:
            print(f"  Already recovered (kept_hashes): {kept_count:>6,}  -> recommendation: SKIP")
        if same_count:
            print(f"  Same-drive duplicates:           {same_count:>6,}  -> recommendation: SKIP")
        print(f"\n  {auto_delete:,} file(s) auto-marked DELETE")
    print(f"  {remaining:,} non-duplicate file(s) remain for LLM triage")


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
    max_workers = int(config.get("max_workers", 1))
    total_batches = len(batches)

    # Pre-build all row dicts in the main thread (df access is not thread-safe)
    prepared = [
        (batch_indices,
         df.loc[batch_indices, ["path", "filename", "extension", "is_dir", "size_bytes", "modified"]].to_dict("records"))
        for batch_indices in batches
    ]

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_run_batch, batch_indices, rows, config, batch_num, total_batches): batch_indices
            for batch_num, (batch_indices, rows) in enumerate(prepared, start=1)
        }
        for future in tqdm(as_completed(futures), total=total_batches, desc="Triaging", unit=" batches"):
            batch_indices, results, error = future.result()
            if error is not None:
                logger.error("Both triage attempts failed (%s), marking batch as REVIEW", error)
                for idx in batch_indices:
                    df.at[idx, "recommendation"] = "REVIEW"
                    df.at[idx, "confidence"] = "0.5"
                    df.at[idx, "comment"] = "LLM parse error — manual review needed"
            else:
                df = _apply_batch_results(df, results, batch_indices)
            safe_write_csv(df, csv_path)


def _run_batch(
    batch_indices: list[int],
    rows: list[dict],
    config: dict,
    batch_num: int,
    total_batches: int,
) -> tuple[list[int], list[dict] | None, Exception | None]:
    """Worker: build prompt, call LLM, retry once on parse failure.
    Returns (indices, results, error). Safe to run concurrently."""
    model = config["triage_model"]
    debug_print = bool(config.get("triage_debug_print_prompt", False))
    debug_stream = bool(config.get("triage_debug_stream_output", False))
    debug_max_chars = int(config.get("triage_debug_prompt_max_chars", 4000))
    debug_batches = int(config.get("triage_debug_prompt_batches", 1))

    prompt = _build_batch_prompt(rows)

    if debug_print and batch_num <= debug_batches:
        shown = prompt[:debug_max_chars] + ("\n... [truncated]" if len(prompt) > debug_max_chars else "")
        print(f"\n--- TRIAGE PROMPT batch {batch_num}/{total_batches} ({len(prompt)} chars) ---\n{shown}\n---\n")

    if debug_stream:
        print(f"[triage] batch {batch_num}/{total_batches} → model '{model}'...")

    try:
        response = llm_client.generate(
            config["ollama_base_url"], model, prompt,
            stream=debug_stream, stream_to_stdout=debug_stream,
        )
        return batch_indices, _parse_llm_response(response), None
    except Exception as e:
        logger.warning("First triage attempt failed (%s), retrying with strict prompt", e)

    try:
        strict_prompt = prompt + _STRICT_SUFFIX
        if debug_print and batch_num <= debug_batches:
            shown = strict_prompt[:debug_max_chars] + ("\n... [truncated]" if len(strict_prompt) > debug_max_chars else "")
            print(f"\n--- TRIAGE STRICT PROMPT batch {batch_num}/{total_batches} ---\n{shown}\n---\n")
        response = llm_client.generate(
            config["ollama_base_url"], model, strict_prompt,
            stream=debug_stream, stream_to_stdout=debug_stream,
        )
        return batch_indices, _parse_llm_response(response), None
    except Exception as e2:
        return batch_indices, None, e2


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
    comment_col = df.get("comment", pd.Series(index=df.index, dtype=str))
    no_comment = dup_mask & (comment_col.isna() | (comment_col == ""))
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
