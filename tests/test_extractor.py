from unittest.mock import MagicMock, patch

import pytest

from extractor import _PDF_SPARSE_TEXT_THRESHOLD, _extract_pdf, _truncate_to_tokens, extract


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


# --- PDF complexity heuristic tests ---

def _make_mock_pdf(pages_text: list[str], pages_images: list[list]) -> MagicMock:
    """Build a pdfplumber-shaped mock from per-page text and image lists."""
    mock_pages = []
    for text, images in zip(pages_text, pages_images):
        page = MagicMock()
        page.extract_text.return_value = text
        page.images = images
        mock_pages.append(page)

    pdf = MagicMock()
    pdf.__enter__ = lambda s: s
    pdf.__exit__ = MagicMock(return_value=False)
    pdf.pages = mock_pages
    return pdf


def test_pdf_simple_text_returns_text_type(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    rich_text = "word " * 200  # well above 100 chars/page
    mock_pdf = _make_mock_pdf([rich_text], [[]])  # 1 page, no images

    with patch("pdfplumber.open", return_value=mock_pdf):
        content_type, content = extract(str(f), ".pdf", {})

    assert content_type == "text"
    assert "word" in content


def test_pdf_with_embedded_images_returns_complex_pdf(tmp_path):
    f = tmp_path / "chart.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    rich_text = "word " * 200
    mock_pdf = _make_mock_pdf([rich_text], [[{"object_type": "image"}]])  # has image

    with patch("pdfplumber.open", return_value=mock_pdf):
        content_type, _ = extract(str(f), ".pdf", {})

    assert content_type == "complex_pdf"


def test_pdf_sparse_text_returns_complex_pdf(tmp_path):
    f = tmp_path / "scanned.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    # avg chars/page = 10, well below threshold
    sparse_text = "a b c d e"
    mock_pdf = _make_mock_pdf([sparse_text, sparse_text], [[], []])

    with patch("pdfplumber.open", return_value=mock_pdf):
        content_type, _ = extract(str(f), ".pdf", {})

    assert content_type == "complex_pdf"


def test_pdf_dense_text_no_images_returns_text(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    # 3 pages, all well above threshold, no images
    page_text = "x " * (_PDF_SPARSE_TEXT_THRESHOLD + 50)
    mock_pdf = _make_mock_pdf([page_text, page_text, page_text], [[], [], []])

    with patch("pdfplumber.open", return_value=mock_pdf):
        content_type, _ = extract(str(f), ".pdf", {})

    assert content_type == "text"


def test_pdf_empty_returns_complex_pdf_not_raising(tmp_path):
    """A zero-page PDF should not raise; falls through as non-complex (no pages to check)."""
    f = tmp_path / "empty.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    mock_pdf = _make_mock_pdf([], [])

    with patch("pdfplumber.open", return_value=mock_pdf):
        content_type, content = extract(str(f), ".pdf", {})

    assert content_type == "text"
    assert content == ""
