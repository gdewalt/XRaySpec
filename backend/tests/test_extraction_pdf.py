"""Native PDF parsing performance gates."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from app.extraction.pdf import load_pages


class _FakePage:
    width = 100.0
    height = 100.0

    def extract_words(self, **_kwargs):
        return [
            {
                "text": "104",
                "x0": 10.0,
                "top": 10.0,
                "x1": 20.0,
                "bottom": 20.0,
                "fontname": "Regular",
            }
        ]


class _FakePdf:
    pages = [_FakePage()]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_native_pages_do_not_open_renderer_when_bold_detection_is_disabled(monkeypatch):
    renderer_calls: list[bytes] = []
    monkeypatch.setitem(sys.modules, "pdfplumber", SimpleNamespace(open=lambda _data: _FakePdf()))
    monkeypatch.setitem(
        sys.modules,
        "pypdfium2",
        SimpleNamespace(PdfDocument=lambda data: renderer_calls.append(data)),
    )

    pages = load_pages(b"searchable pdf", detect_bold_numbers=False)

    assert [word.text for word in pages[0].words] == ["104"]
    assert renderer_calls == []
