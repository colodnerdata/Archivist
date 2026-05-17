import pytest

import llm_client


class _MockStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        return None

    def iter_lines(self, decode_unicode=True):
        assert decode_unicode is True
        return iter(self._lines)


def test_generate_stream_raises_value_error_on_invalid_json(monkeypatch):
    def fake_post(*_args, **_kwargs):
        return _MockStreamResponse([
            '{"response": "hello", "done": false}',
            'not json',
        ])

    monkeypatch.setattr(llm_client.requests, "post", fake_post)

    with pytest.raises(ValueError, match="Failed to decode streamed JSON chunk 2") as exc_info:
        llm_client.generate("http://example.com", "model", "prompt", stream=True)

    assert "not json" in str(exc_info.value)