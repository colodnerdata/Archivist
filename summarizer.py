import logging

import pandas as pd
from tqdm import tqdm

import llm_client
from csv_utils import safe_write_csv
from extractor import extract, _truncate_to_tokens
from inheritance import resolve_all

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

    eff_review = resolve_all(df, "review")
    eff_decision = resolve_all(df, "decision")

    eligible = [
        idx for idx in df.index
        if _should_summarize(eff_review[idx], eff_decision[idx])
        and _is_blank_summary(df.at[idx, "summary"] if "summary" in df.columns else None)
        and str(df.at[idx, "is_dir"]).lower() != "true"
    ]

    if "summary" not in df.columns:
        df["summary"] = ""

    for idx in tqdm(eligible, desc="Summarizing", unit=" files"):
        path = str(df.at[idx, "path"])
        ext = str(df.at[idx, "extension"])

        content_type, content = extract(path, ext, config)

        try:
            if content_type == "error":
                summary = f"UNREADABLE: {content}"
            elif content_type == "image_b64":
                summary = llm_client.chat_with_image(
                    config["ollama_base_url"], vision_model, _VISION_PROMPT, content
                )
            elif content_type == "complex_pdf":
                truncated = _truncate_to_tokens(content, int(config.get("max_text_tokens", 2000)))
                prompt = _COMPLEX_PDF_PROMPT_TEMPLATE.format(text=truncated)
                summary = llm_client.generate(
                    config["ollama_base_url"], vision_pdf_model, prompt, temperature=0.3
                )
            else:
                truncated = _truncate_to_tokens(content, int(config.get("max_text_tokens", 2000)))
                prompt = _DOC_PROMPT_TEMPLATE.format(text=truncated)
                summary = llm_client.generate(
                    config["ollama_base_url"], summarize_model, prompt, temperature=0.3
                )
        except Exception as e:
            logger.error("LLM error for %s: %s", path, e)
            summary = f"LLM_ERROR: {e}"

        df.at[idx, "summary"] = summary.strip()
        safe_write_csv(df, csv_path)


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
