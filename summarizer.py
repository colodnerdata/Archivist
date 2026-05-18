import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm

import llm_client
from csv_utils import safe_write_csv
from extractor import extract, _truncate_to_tokens
from inheritance import resolve_all_multi

_WRITE_BATCH = 10

logger = logging.getLogger(__name__)

_DOC_PROMPT_TEMPLATE = """Summarize this document in 2-3 sentences. Focus on:
- What the document is about
- Whether it appears personal/unique vs generic/downloaded
- Any notable names, dates, projects, or topics

{text}"""

_VISION_PROMPT = (
    "Describe this image in 1-2 sentences. Include: "
    "what is shown (people, places, objects, scenes), "
    "approximate era if determinable from content or image quality, "
    "whether it appears personal (family, event, selfie) vs generic (stock, meme, download)."
)

_COMPLEX_PDF_PROMPT_TEMPLATE = """Summarize this document in 2-3 sentences. This PDF appears to contain more than plain text (embedded images, charts, diagrams, forms, or very sparse text suggesting scanned pages). Focus on:
- What the document is about
- Whether it appears personal/unique vs generic/downloaded
- Any notable names, dates, projects, or topics

{text}"""


def run_summarize(csv_path: str, config: dict) -> None:
    summarize_model = config["summarize_model"]
    vision_model = config["vision_model"]
    vision_pdf_model = config.get("vision_pdf_model", vision_model)
    models_needed = list({summarize_model, vision_model, vision_pdf_model})
    llm_client.check_ollama(config["ollama_base_url"], models_needed)

    df = pd.read_csv(csv_path, dtype=str)

    resolved = resolve_all_multi(df, ["review", "decision"])
    eff_review = resolved["review"]
    eff_decision = resolved["decision"]

    eligible = [
        idx for idx in df.index
        if _should_summarize(eff_review[idx], eff_decision[idx])
        and _is_blank_summary(df.at[idx, "summary"] if "summary" in df.columns else None)
        and str(df.at[idx, "is_dir"]).lower() != "true"
    ]

    if "summary" not in df.columns:
        df["summary"] = ""
    if "pdf_complexity" not in df.columns:
        df["pdf_complexity"] = ""

    # Snapshot per-file args from df before submitting (workers must not touch df)
    job_args: list[tuple[int, str, str, bool | None]] = []
    for idx in eligible:
        path = str(df.at[idx, "path"])
        ext = str(df.at[idx, "extension"])
        known_is_complex: bool | None = None
        if ext.lower() == ".pdf":
            cached = str(df.at[idx, "pdf_complexity"]).strip().lower()
            if cached == "complex":
                known_is_complex = True
            elif cached == "simple":
                known_is_complex = False
        job_args.append((idx, path, ext, known_is_complex))

    max_workers = int(config.get("max_workers", 1))
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_run_summarize_file, idx, path, ext, config, known_is_complex): idx
            for idx, path, ext, known_is_complex in job_args
        }
        for future in tqdm(as_completed(futures), total=len(futures), desc="Summarizing", unit=" files"):
            try:
                idx, summary, content_type = future.result()
            except Exception as e:
                idx = futures[future]
                logger.error("Worker crashed for idx %s: %s", idx, e)
                summary, content_type = f"LLM_ERROR: {e}", "error"

            df.at[idx, "summary"] = summary
            if str(df.at[idx, "extension"]).lower() == ".pdf" and str(df.at[idx, "pdf_complexity"]).strip() == "":
                df.at[idx, "pdf_complexity"] = "complex" if content_type == "complex_pdf" else "simple"
            completed += 1
            if completed % _WRITE_BATCH == 0:
                safe_write_csv(df, csv_path)

    safe_write_csv(df, csv_path)


def _run_summarize_file(
    idx: int,
    path: str,
    ext: str,
    config: dict,
    known_is_complex: bool | None,
) -> tuple[int, str, str]:
    """Worker: extract file content and call LLM. Returns (idx, summary, content_type).
    Safe to run concurrently — reads no shared state beyond config (immutable)."""
    summarize_model = config["summarize_model"]
    vision_model = config["vision_model"]
    vision_pdf_model = config.get("vision_pdf_model", vision_model)
    max_tokens = int(config.get("max_text_tokens", 2000))

    content_type, content = extract(path, ext, config, known_is_complex=known_is_complex)
    try:
        if content_type == "error":
            return idx, f"UNREADABLE: {content}", content_type
        elif content_type == "image_b64":
            summary = llm_client.chat_with_image(
                config["ollama_base_url"], vision_model, _VISION_PROMPT, content
            )
        elif content_type == "complex_pdf":
            prompt = _COMPLEX_PDF_PROMPT_TEMPLATE.format(text=_truncate_to_tokens(content, max_tokens))
            summary = llm_client.generate(config["ollama_base_url"], vision_pdf_model, prompt, temperature=0.3)
        else:
            prompt = _DOC_PROMPT_TEMPLATE.format(text=_truncate_to_tokens(content, max_tokens))
            summary = llm_client.generate(config["ollama_base_url"], summarize_model, prompt, temperature=0.3)
        return idx, summary.strip(), content_type
    except Exception as e:
        logger.error("LLM error for %s: %s", path, e)
        return idx, f"LLM_ERROR: {e}", content_type


def _should_summarize(eff_review, eff_decision) -> bool:
    if eff_review is None:
        return False
    review_str = str(eff_review).strip().lower()
    if review_str not in ("true", "1", "yes"):
        return False
    if eff_decision is not None and str(eff_decision).strip().upper() == "DELETE":
        return False
    return True


def _is_blank_summary(val) -> bool:
    if val is None:
        return True
    import math
    if isinstance(val, float) and math.isnan(val):
        return True
    return str(val).strip() == ""
