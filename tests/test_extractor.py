from unittest.mock import patch

import pytest

from extractor import _truncate_to_tokens, extract


def test_extract_text_file(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("Hello world, this is a test.")
    content_type, content = extract(str(f), ".txt", {})
    assert content_type == "text"
    assert "Hello world" in content


def test_extract_unknown_extension_tries_text(tmp_path):
    f = tmp_path / "data.xyz"
    f.write_text("some readable content")
    content_type, content = extract(str(f), ".xyz", {})
    assert content_type == "text"
    assert "readable content" in content


def test_extract_unreadable_file(tmp_path):
    f = tmp_path / "locked.txt"
    f.write_text("x")
    with patch("builtins.open", side_effect=OSError("permission denied")):
        content_type, content = extract(str(f), ".txt", {})
    assert content_type == "error"
    assert "permission denied" in content.lower()


def test_extract_missing_file():
    content_type, content = extract("/nonexistent/path/file.txt", ".txt", {})
    assert content_type == "error"


def test_truncate_to_tokens_short():
    text = "one two three"
    result = _truncate_to_tokens(text, 10)
    assert result == text


def test_truncate_to_tokens_long():
    text = " ".join(str(i) for i in range(5000))
    result = _truncate_to_tokens(text, 2000)
    assert len(result.split()) == 2000


def test_truncate_to_tokens_exact():
    words = ["word"] * 100
    text = " ".join(words)
    result = _truncate_to_tokens(text, 100)
    assert result == text
