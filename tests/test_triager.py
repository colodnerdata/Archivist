import json

import pandas as pd
import pytest

from triager import _build_batch_prompt, _mark_duplicates, _parse_llm_response


def test_parse_valid_json():
    data = [{"path": "foo.txt", "recommendation": "SKIP", "confidence": 0.9, "comment": "test"}]
    result = _parse_llm_response(json.dumps(data))
    assert len(result) == 1
    assert result[0]["recommendation"] == "SKIP"


def test_parse_strips_markdown_fences():
    data = [{"path": "foo.txt", "recommendation": "INTERESTING", "confidence": 0.8, "comment": "doc"}]
    wrapped = f"```json\n{json.dumps(data)}\n```"
    result = _parse_llm_response(wrapped)
    assert result[0]["recommendation"] == "INTERESTING"


def test_parse_strips_plain_fences():
    data = [{"path": "x.py", "recommendation": "REVIEW", "confidence": 0.5, "comment": "code"}]
    wrapped = f"```\n{json.dumps(data)}\n```"
    result = _parse_llm_response(wrapped)
    assert result[0]["recommendation"] == "REVIEW"


def test_parse_invalid_json_raises():
    with pytest.raises(ValueError):
        _parse_llm_response("not json at all")


def test_parse_non_array_raises():
    with pytest.raises(ValueError):
        _parse_llm_response('{"path": "x", "recommendation": "SKIP", "confidence": 0.9, "comment": "x"}')


def test_parse_missing_key_raises():
    data = [{"path": "foo.txt", "recommendation": "SKIP", "confidence": 0.9}]  # missing 'comment'
    with pytest.raises(ValueError):
        _parse_llm_response(json.dumps(data))


def test_mark_duplicates():
    df = pd.DataFrame([
        {"path": "a.txt", "is_duplicate": "True",  "duplicate_kind": "kept_hashes", "recommendation": "", "confidence": "", "comment": "dup comment"},
        {"path": "b.txt", "is_duplicate": "False", "duplicate_kind": "", "recommendation": "", "confidence": "", "comment": ""},
    ])
    df = _mark_duplicates(df)
    assert df.loc[0, "recommendation"] == "SKIP"
    assert df.loc[0, "confidence"] == "0.99"
    assert df.loc[1, "recommendation"] == ""  # untouched


def test_mark_baseline_duplicates_for_deletion():
    df = pd.DataFrame([
        {"path": "d.txt", "is_duplicate": "True", "duplicate_kind": "baseline_scan", "recommendation": "", "confidence": "", "comment": "baseline dup"},
        {"path": "e.txt", "is_duplicate": "True", "duplicate_kind": "same_drive", "recommendation": "", "confidence": "", "comment": "same drive dup"},
    ])

    df = _mark_duplicates(df)

    assert df.loc[0, "decision"] == "DELETE"
    assert df.loc[1, "decision"] == ""


def test_mark_duplicates_preserves_existing_decision():
    df = pd.DataFrame([
        {"path": "d.txt", "is_duplicate": "True", "duplicate_kind": "baseline_scan", "recommendation": "", "confidence": "", "comment": "baseline dup", "decision": "KEEP"},
    ])

    df = _mark_duplicates(df)

    assert df.loc[0, "decision"] == "KEEP"


def test_build_batch_prompt_contains_items():
    rows = [{"path": "D:\\doc.txt", "filename": "doc.txt", "extension": ".txt", "is_dir": False, "size_bytes": 1000, "modified": "2020-01-01"}]
    prompt = _build_batch_prompt(rows)
    assert "doc.txt" in prompt
    assert "INTERESTING" in prompt
    assert "JSON array" in prompt
